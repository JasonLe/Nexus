"""python -m nexus 入口点 —— 委托给 CLI 主模块。

当用户执行 `python -m nexus` 时，Python 会先执行此文件。
此处将调用委托给 `nexus.cli.main:main()` 函数，与
`nexus` 控制台脚本入口 (`nexus.cli.main:main`) 保持一致。
"""

from __future__ import annotations

from nexus.cli.main import main

if __name__ == "__main__":
    main()
