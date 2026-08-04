/**
 * Nexus Desktop —— Electron 主进程（CommonJS，避免 ESM 兼容问题）
 *
 * 职责：
 * 1. 启动时拉起后端服务：优先 `nexus serve --port 8321`（假设 nexus 已在 PATH），
 *    失败则回退 `python -m nexus serve --port 8321`；
 *    若后端已在运行则直接复用，不再重复拉起。
 * 2. 就绪探测：轮询 http://127.0.0.1:8321/api/health（每 500ms，超时约 30s），
 *    就绪后再创建窗口加载页面。
 * 3. 加载策略：
 *    - 存在环境变量 NEXUS_DEV_SERVER_URL → 加载该地址（开发模式，Vite dev server）
 *    - 否则加载 http://127.0.0.1:8321/（生产模式，由 FastAPI 托管 desktop/dist）
 * 4. 窗口关闭 / 应用退出时终止后端子进程；单实例锁。
 *
 * 冒烟测试：`electron . --smoke` —— 页面加载成功后打印 SMOKE_OK 并自动退出（0），
 * 任一步骤失败打印 SMOKE_FAIL 并以非零码退出，供无显示器的 CI 式环境验证。
 */

const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');

// ---------------- 常量 ----------------

const BACKEND_PORT = 8321;
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;
const HEALTH_URL = `${BACKEND_ORIGIN}/api/health`;
const HEALTH_INTERVAL_MS = 500; // 健康探测间隔
const HEALTH_TIMEOUT_MS = 30_000; // 后端就绪总超时
const SMOKE_TIMEOUT_MS = 60_000; // 冒烟测试整体超时
const SMOKE_MODE = process.argv.includes('--smoke');
// 项目根目录（desktop/electron/ 向上两级），作为后端进程的工作目录
const PROJECT_ROOT = path.join(__dirname, '..', '..');

// ---------------- 全局状态 ----------------

let mainWindow = null;
let backendProcess = null; // 由本进程拉起的后端子进程（复用外部后端时为 null）
let backendDead = false; // 后端子进程是否已意外退出
let quitting = false; // 应用正在退出（避免误报后端退出日志）

function log(message) {
  console.log(`[nexus-desktop] ${message}`);
}

// ---------------- 后端子进程管理 ----------------

/** 把子进程输出按行加前缀转发到主进程控制台。 */
function pipeWithPrefix(stream, prefix) {
  let carry = '';
  stream.on('data', (chunk) => {
    carry += chunk.toString();
    const lines = carry.split(/\r?\n/);
    carry = lines.pop(); // 最后一段可能是不完整的行，留到下次拼接
    for (const line of lines) {
      if (line.trim()) console.log(`${prefix} ${line}`);
    }
  });
  stream.on('end', () => {
    if (carry.trim()) console.log(`${prefix} ${carry}`);
    carry = '';
  });
}

/** 拉起后端：优先 nexus CLI，ENOENT 时回退 python -m nexus。 */
function spawnBackend() {
  const candidates = [
    { cmd: 'nexus', args: ['serve', '--port', String(BACKEND_PORT)] },
    { cmd: 'python', args: ['-m', 'nexus', 'serve', '--port', String(BACKEND_PORT)] },
  ];

  return new Promise((resolve, reject) => {
    const tryCandidate = (index) => {
      if (index >= candidates.length) {
        reject(
          new Error(
            '无法启动后端：nexus 与 python 命令均不可用，请确认 nexus 已安装且在 PATH 中'
          )
        );
        return;
      }
      const { cmd, args } = candidates[index];
      log(`启动后端: ${cmd} ${args.join(' ')}`);
      const child = spawn(cmd, args, {
        cwd: PROJECT_ROOT,
        windowsHide: true, // 避免弹出额外控制台窗口
      });

      let settled = false;
      // 'spawn' 事件表示进程成功拉起；'error'（如 ENOENT）则尝试下一个候选命令
      child.once('spawn', () => {
        if (settled) return;
        settled = true;
        backendProcess = child;
        resolve(child);
      });
      child.once('error', (err) => {
        if (settled) return;
        settled = true;
        log(`「${cmd}」启动失败（${err.message}），尝试下一个候选命令`);
        tryCandidate(index + 1);
      });

      // 后端 stdout/stderr 带 [nexus-serve] 前缀转发
      pipeWithPrefix(child.stdout, '[nexus-serve]');
      pipeWithPrefix(child.stderr, '[nexus-serve]');

      child.on('exit', (code, signal) => {
        if (backendProcess === child) backendProcess = null;
        backendDead = true;
        if (!quitting) {
          log(`后端进程已退出（code=${code}, signal=${signal}）`);
        }
      });
    };
    tryCandidate(0);
  });
}

/** 终止由本进程拉起的后端子进程（复用外部后端时为无操作）。 */
function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    log('终止后端子进程');
    backendProcess.kill(); // Windows 下为 TerminateProcess
  }
  backendProcess = null;
}

// ---------------- 就绪探测 ----------------

/** 单次健康检查：后端已在运行时返回 true。 */
function checkBackendOnce() {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, (res) => {
      res.resume(); // 丢弃响应体
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(2000, () => req.destroy());
  });
}

/** 轮询等待后端就绪；后端子进程意外退出时立即失败。 */
function waitForBackend(timeoutMs) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;

    const retry = () => {
      if (backendDead) {
        reject(new Error('后端进程已退出，请查看上方 [nexus-serve] 日志定位原因'));
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error(`等待后端就绪超时（${timeoutMs / 1000}s）：${HEALTH_URL}`));
        return;
      }
      setTimeout(probe, HEALTH_INTERVAL_MS);
    };

    const probe = () => {
      if (backendDead) {
        reject(new Error('后端进程已退出，请查看上方 [nexus-serve] 日志定位原因'));
        return;
      }
      const req = http.get(HEALTH_URL, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else retry();
      });
      req.on('error', retry);
      req.setTimeout(2000, () => req.destroy());
    };

    probe();
  });
}

// ---------------- 窗口 ----------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 720,
    title: 'Nexus Desktop',
    backgroundColor: '#0a0e14',
    autoHideMenuBar: true,
    show: !SMOKE_MODE, // 冒烟模式不展示窗口（兼容无显示器环境）
    // 完全自定义标题栏（macOS 交通灯固定在左侧，改用 frame:false 实现自定义按钮）
    frame: false,
    webPreferences: {
      // 安全基线：渲染进程仅通过 HTTP/WS 与后端交互，不暴露任何 Node 能力
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload 需要 require('electron')，sandbox 模式下不可用
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  // IPC handlers for window controls
  const { ipcMain } = require('electron');
  ipcMain.on('window:minimize', () => {
    mainWindow?.minimize();
  });
  ipcMain.on('window:close', () => {
    mainWindow?.close();
  });
  ipcMain.handle('window:get-platform', () => {
    return process.platform;
  });

  // 加载策略：开发模式走 Vite dev server，生产模式由 FastAPI 托管 desktop/dist
  const target = process.env.NEXUS_DEV_SERVER_URL || `${BACKEND_ORIGIN}/`;
  log(`加载页面: ${target}`);

  mainWindow.webContents.on('did-finish-load', () => {
    log('窗口已加载');
    if (SMOKE_MODE) {
      console.log('SMOKE_OK');
      shutdown(0);
    }
  });
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, description) => {
    log(`页面加载失败（${errorCode}: ${description}）`);
    if (SMOKE_MODE) {
      console.log('SMOKE_FAIL');
      shutdown(1);
    }
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadURL(target);
}

// ---------------- 生命周期 ----------------

/** 统一退出入口：先清理后端子进程，再以指定退出码结束。 */
function shutdown(exitCode) {
  quitting = true;
  stopBackend();
  // 稍作等待，确保子进程退出与日志冲刷，避免遗留孤儿进程
  setTimeout(() => app.exit(exitCode), 300);
}

async function bootstrap() {
  if (SMOKE_MODE) {
    setTimeout(() => {
      log('冒烟测试超时');
      console.log('SMOKE_FAIL');
      shutdown(1);
    }, SMOKE_TIMEOUT_MS);
  }

  try {
    if (await checkBackendOnce()) {
      log('检测到后端已在运行，直接复用现有实例');
    } else {
      await spawnBackend();
    }
    log(`等待后端就绪: ${HEALTH_URL}`);
    await waitForBackend(HEALTH_TIMEOUT_MS);
    log('后端就绪');
    createWindow();
  } catch (err) {
    log(`启动失败: ${err.message}`);
    if (SMOKE_MODE) console.log('SMOKE_FAIL');
    shutdown(1);
  }
}

// 单实例锁：已有实例运行时直接退出，并聚焦已有窗口
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(bootstrap);

  app.on('window-all-closed', () => {
    app.quit();
  });

  app.on('will-quit', () => {
    quitting = true;
    stopBackend();
  });
}
