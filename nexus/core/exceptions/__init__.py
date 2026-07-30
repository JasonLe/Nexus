"""Nexus Error 异常体系。

分层设计意图
------------
框架异常按**故障域**分层，每一层对应一组内聚的失败模式，使调用方可以按层级
精确捕获和处理：

- ``NexusError``：所有框架异常的根。捕获此类型即可兜底处理所有框架级异常，
  而不会误吞不相关的异常（如 ValueError、TypeError 等应用逻辑异常）。
- ``LLMError``：故障域定界在 LLM 调用链路内（如 API 超时、鉴权失败、
  模型返回格式异常等）。调用方可以据此实施重试、降级或切换 provider。
- ``ToolError``：故障域定界在工具注册/查找/执行阶段（如工具未找到、
  参数校验失败、工具超时等）。调用方可据此进行 fallback 或上报。
- ``StateError``：故障域定界在状态读写与序列化阶段（如状态存储失败、
  序列化/反序列化异常、状态不一致等）。调用方可据此进行状态修复或回滚。
- ``PolicyError``：故障域定界在执行策略判断阶段（如策略检查超时、
  策略规则冲突、未授权操作等）。调用方可据此阻断当前执行路径。
- ``AgentRuntimeError``：故障域定界在 Runtime 调度阶段（如调度循环异常、
  executor 不可达、并发控制失败等）。注意命名加 ``Agent`` 前缀以避免与
  Python 内置 ``RuntimeError`` 冲突。
- ``PluginError``：故障域定界在插件生命周期管理阶段（如插件加载失败、
  依赖缺失、权限校验不通过、钩子执行异常等）。
- ``EventError``：故障域定界在事件发布/订阅/分发阶段（如事件类型未注册、
  订阅超时、事件总线异常等）。
"""

from __future__ import annotations

from nexus.logging import get_logger

logger = get_logger(__name__)


class NexusError(Exception):
    """Nexus 框架所有异常的基类。

    所有框架级异常必须继承自本类。应用层可以通过捕获 ``NexusError`` 来
    兜底处理所有框架异常，同时不干扰其他 Python 标准库或应用逻辑异常。

    使用场景
    --------
    - 作为框架内部异常的公共祖先，不直接抛出。
    - 调用方在最外层捕获 ``NexusError`` 作为框架异常的兜底容器。
    """


class LLMError(NexusError):
    """LLM 调用链路异常。

    涵盖 provider 通信、认证、限流、模型响应解析等阶段的所有故障。
    与特定 provider 无关，不同 provider 的异常应统一转换为本类或其子类。

    抛出场景
    --------
    - API 请求超时、连接失败、DNS 解析失败
    - 鉴权失败（API key 无效/过期/权限不足）
    - 速率限制（429 / rate limit exceeded）
    - 模型返回格式无法解析（非标准 JSON、缺失必要字段）
    - 模型返回内容策略拒绝（content filter / safety refusal）

    调用方处理建议
    --------------
    - 根据异常属性决定是否重试（幂等请求）、切换 provider 或降级处理。
    - 建议检查 ``__cause__`` 或 ``__context__`` 获取原始 provider 异常信息。
    - 可通过 ``isinstance(e, LLMRetryableError)`` 判断是否值得重试。
    """


class LLMRetryableError(LLMError):
    """可重试的 LLM 异常基类。

    所有"因临时性故障导致、重试有望成功"的异常继承此类。
    重试层（如指数退避）通过 ``isinstance(e, LLMRetryableError)`` 判断
    是否对该异常进行重试，避免对不可重试错误（如鉴权失败）做无意义重试。

    子类：LLMRateLimitError、LLMTimeoutError、LLMServerError。
    """


class LLMRateLimitError(LLMRetryableError):
    """触发 API 速率限制（HTTP 429）。

    重试建议：指数退避 + 较长基础间隔，尊重 Retry-After 头（若存在）。
    """


class LLMTimeoutError(LLMRetryableError):
    """LLM 请求超时。

    包括客户端超时（asyncio.wait_for 触发）和服务端超时（gateway timeout）。
    重试建议：可立即重试或适度增加超时阈值。
    """


class LLMServerError(LLMRetryableError):
    """LLM 服务端错误（HTTP 5xx）。

    包括 500/502/503/504 等服务端临时性故障。
    重试建议：指数退避，最多 3 次。
    """


class LLMAuthError(LLMError):
    """LLM 鉴权失败（HTTP 401/403）。

    不可重试。API key 无效/过期/权限不足，需用户介入修正配置。
    """


class ToolError(NexusError):
    """工具注册与执行异常。

    涵盖工具声明、查找、参数校验、执行、超时等阶段的所有故障。

    抛出场景
    --------
    - 工具未注册（tool not found）
    - 工具名称冲突（注册时发现同名工具）
    - 工具参数校验失败（类型不匹配、缺少必填参数）
    - 工具执行超时
    - 工具执行过程中返回的非预期结果

    调用方处理建议
    --------------
    - ``tool not found`` 场景通常是配置错误，不应静默吞掉；建议向上报告。
    - 参数校验失败可提示用户修正输入后重试。
    - 工具执行超时可考虑重试或使用更宽松的超时配置。
    """


class StateError(NexusError):
    """状态管理与序列化异常。

    涵盖 Agent 状态、Session 状态、Memory 状态的读写与序列化/反序列化
    过程中的所有故障。

    抛出场景
    --------
    - 状态存储后端不可用（Redis 断连、磁盘空间不足等）
    - 状态读/写超时或被中断
    - 序列化/反序列化失败（JSON 解析错误、pickle 版本不兼容）
    - 状态数据校验不通过（schema 不匹配、checksum 校验失败）
    - 状态不一致（expected version 与实际版本不匹配）

    调用方处理建议
    --------------
    - 读失败可尝试从备份恢复或重新初始化状态。
    - 写失败应保留现场日志，避免静默丢失状态。
    - 序列化失败通常意味着数据格式升级不兼容，需做迁移处理。
    """


class PolicyError(NexusError):
    """执行策略异常。

    涵盖安全/权限策略、执行策略、条件判断等阶段的故障。

    抛出场景
    --------
    - 操作被安全策略拦截（未授权 API 调用、越权操作）
    - 执行条件不满足（超时策略拒绝、步骤数超限）
    - 策略规则冲突或循环依赖
    - 策略引擎本身异常（规则引擎不可用、表达式求值失败）

    调用方处理建议
    --------------
    - 权限/安全策略拒绝通常不可重试，需提示用户申请权限或调整策略配置。
    - 执行条件拒绝可提示用户调整输入后重试。
    - 策略引擎异常属于基础设施问题，应告警并排查。
    """


class AgentRuntimeError(NexusError):
    """Runtime 调度异常。

    涵盖 Agent 运行循环、executor 调度、并发控制等阶段的故障。命名使用
    ``AgentRuntimeError`` 以区别于 Python 内置 ``RuntimeError``。

    抛出场景
    --------
    - 调度循环异常（step 死循环检测、task 依赖解析失败）
    - Executor 不可达（远程 executor 网络断开、gRPC 通道关闭）
    - 并发控制失败（锁获取超时、最大并发数超限）
    - 系统资源耗尽（线程池/进程池耗尽）
    - Task 执行被中断且无法恢复

    调用方处理建议
    --------------
    - Executor 不可达可尝试重连或切换备用 executor。
    - 资源耗尽应检查配置并考虑扩容，不可盲目重试。
    - 调度死循环和死锁需要人工排查调用链路。
    """


class PluginError(NexusError):
    """插件管理异常。

    涵盖插件发现、加载、初始化、钩子执行、卸载等生命周期阶段的故障。

    抛出场景
    --------
    - 插件包未找到或无法导入
    - 插件依赖缺失或版本不满足
    - 插件入口点/元数据格式无效
    - 插件初始化失败（注册阶段异常）
    - 插件权限校验不通过
    - 钩子执行异常（hook handler 返回异常或超时）
    - 插件卸载失败（资源清理异常）

    调用方处理建议
    --------------
    - 加载失败通常不可重试，需检查插件包完整性和依赖兼容性。
    - 可降级运行（跳过故障插件），在启动日志中报告被跳过的插件。
    - 钩子异常是否阻断流程取决于钩子类型（critical vs optional）。
    """


class EventError(NexusError):
    """事件系统异常。

    涵盖事件发布、订阅、分发等阶段的故障。

    抛出场景
    --------
    - 事件类型未注册（发布/订阅不存在的 event type）
    - 事件 schema 校验失败（payload 格式不匹配）
    - 事件总线不可用（broker 断连、队列满）
    - 事件分发超时或 handler 未正常返回
    - 事件订阅冲突（重复订阅、循环订阅）

    调用方处理建议
    --------------
    - 事件类型未注册或 schema 不匹配通常是配置/代码一致性问题。
    - 总线不可用可重试连接，队列满需检查消费者处理能力。
    - 通常是异步场景，调用方应注意异常可能不在发布线程中被捕获。
    """
