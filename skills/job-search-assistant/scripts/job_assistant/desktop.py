"""Windows 可见界面执行器。每个动作使用新截图与绑定窗口；不识别招聘业务。"""
import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

BROWSERS = {'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe'}


@contextmanager
def desktop_lock(root):
    """不同命令进程共享同一桌面锁；暂停命令不依赖此锁。"""
    # 不同数据目录仍共用同一用户桌面，锁不能跟随 --root 分裂。
    path = Path(tempfile.gettempdir()) / 'job-search-assistant-desktop.lock'
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError('已有电脑操作正在进行，请串行执行') from None
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('已有电脑操作正在进行，请串行执行') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def validate_binding(binding, window):
    same_process = all(binding[key] == window[key] for key in ('pid', 'exe'))
    belongs = window['hwnd'] == binding['hwnd'] or window.get('root_owner') == binding['hwnd']
    if not same_process or not belongs:
        raise ValueError('前台不是绑定浏览器或其文件对话框，暂停以等待用户恢复')


def prune_frames(directory, keep=32):
    """只轮转本执行器产生的观察缓存，归档证据永不自动删除。"""
    directory = Path(directory).resolve()
    candidates = [p for p in directory.glob('*.png') if re.fullmatch(r'[0-9a-f]{32}', p.stem)
                  and p.with_suffix('.json').is_file() and not p.is_symlink()
                  and p.resolve().parent == directory and not p.with_suffix('.json').is_symlink()]
    for path in sorted(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)[keep:]:
        path.unlink()
        path.with_suffix('.json').unlink()


def screen_point(rect, x, y):
    left, top, right, bottom = rect
    if type(x) is not int or type(y) is not int or not (0 <= x < right - left and 0 <= y < bottom - top):
        raise ValueError('坐标超出截图范围')
    return left + x, top + y


def validate_frame(frame, state, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    if not 0 <= timestamp - frame['captured_at'] <= 30:
        raise ValueError('截图已过期，请重新观察')
    if frame['window'] != state['window'] or frame['screen'] != state['screen']:
        raise ValueError('窗口、标题、位置或屏幕已变化；请重新观察并确认')


def windows_api():
    if os.name != 'nt':
        raise ValueError('电脑操作仅支持 Windows')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    try:
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user.SetProcessDPIAware()
    return user


def foreground():
    user = windows_api()
    hwnd = user.GetForegroundWindow()
    if not hwnd or not user.IsWindowVisible(hwnd):
        raise ValueError('没有可操作的前台窗口，可能已锁屏')
    title = ctypes.create_unicode_buffer(2048)
    user.GetWindowTextW(hwnd, title, len(title))
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        raise ValueError('无法识别前台进程')
    try:
        executable = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(executable))
        if not kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(size)):
            raise ValueError('无法读取进程身份')
    finally:
        kernel.CloseHandle(handle)
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ValueError('无法读取窗口位置')
    screen = [user.GetSystemMetrics(0), user.GetSystemMetrics(1)]
    bounds = [max(0, rect.left), max(0, rect.top), min(screen[0], rect.right), min(screen[1], rect.bottom)]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError('请将浏览器移到主显示器并保持可见')
    return {'window': {'hwnd': int(hwnd), 'pid': pid.value, 'title': title.value, 'rect': bounds,
                       'exe': Path(executable.value).name.lower(), 'root_owner': int(user.GetAncestor(hwnd, 3) or hwnd)}, 'screen': screen}


def type_unicode(text, check=None):
    user = windows_api()
    class Keyboard(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Mouse(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Payload(ctypes.Union):
        _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]
    class Input(ctypes.Structure):
        _anonymous_ = ('payload',)
        _fields_ = [('type', wintypes.DWORD), ('payload', Payload)]
    raw = text.encode('utf-16-le')
    for offset in range(0, len(raw), 2):
        if check and offset % 64 == 0:
            check()
        unit = int.from_bytes(raw[offset:offset + 2], 'little')
        events = (Input * 2)(Input(type=1, keyboard=Keyboard(0, unit, 4, 0, 0)),
                             Input(type=1, keyboard=Keyboard(0, unit, 6, 0, 0)))
        if user.SendInput(2, ctypes.byref(events), ctypes.sizeof(Input)) != 2:
            raise ValueError('Windows 拒绝输入；请确认桌面未锁定、程序权限一致')


class Desktop:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.binding_path = self.root / 'desktop-binding.json'

    def bind(self, expected_title):
        state = foreground()
        if state['window']['exe'] not in BROWSERS:
            raise ValueError('请先将 Chrome、Edge、Firefox 或 Brave 浏览器置于前台')
        if not expected_title or expected_title not in state['window']['title']:
            raise ValueError('前台窗口与指定标题不符')
        self.binding_path.write_text(json.dumps(state), encoding='utf-8')
        return state

    def check_binding(self, state):
        if not self.binding_path.is_file():
            raise ValueError('尚未绑定浏览器窗口')
        binding = json.loads(self.binding_path.read_text(encoding='utf-8'))['window']
        validate_binding(binding, state['window'])

    def capture(self):
        state = foreground()
        self.check_binding(state)
        import pyautogui
        pyautogui.failSafeCheck()
        left, top, right, bottom = state['window']['rect']
        directory = self.root / 'frames'
        directory.mkdir(exist_ok=True)
        path = directory / f'{uuid.uuid4().hex}.png'
        pyautogui.screenshot(region=(left, top, right-left, bottom-top)).save(path)
        if foreground() != state:
            raise ValueError('截图期间窗口变化，请重新截图')
        frame = {**state, 'captured_at': time.time(), 'path': str(path)}
        path.with_suffix('.json').write_text(json.dumps(frame, ensure_ascii=False), encoding='utf-8')
        prune_frames(directory)
        return frame

    def act(self, frame_path, operation, check=None):
        frame_path = Path(frame_path).resolve()
        if not frame_path.is_relative_to(self.root / 'frames'):
            raise ValueError('只接受当前运行目录的截图')
        frame = json.loads(frame_path.with_suffix('.json').read_text(encoding='utf-8'))
        state = foreground()
        self.check_binding(state)
        validate_frame(frame, state)
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.failSafeCheck()
        if check:
            check()
        op = operation['op']
        if op == 'click':
            pyautogui.click(*screen_point(state['window']['rect'], operation['x'], operation['y']))
        elif op == 'scroll':
            clicks = operation['clicks']
            if type(clicks) is not int or not -12 <= clicks <= 12:
                raise ValueError('单次滚动必须在 -12 到 12 之间')
            pyautogui.moveTo(*screen_point(state['window']['rect'], operation['x'], operation['y']))
            pyautogui.scroll(clicks)
        elif op == 'type':
            text = operation['text']
            if not isinstance(text, str) or len(text) > 12000:
                raise ValueError('单次输入必须为不超过 12000 字符的文本')
            def check_typing():
                pyautogui.failSafeCheck()
                validate_frame(frame, foreground())
                if check:
                    check()
            type_unicode(text, check_typing)
        elif op == 'key':
            keys = operation['keys']
            allowed = {'ctrl', 'shift', 'alt', 'enter', 'tab', 'esc', 'backspace', 'delete', 'home', 'end', 'up', 'down', 'left', 'right', 'a', 'l', 'c', 'v', 'f', 'f5'}
            if not isinstance(keys, list) or not 1 <= len(keys) <= 3 or any(k not in allowed for k in keys):
                raise ValueError('不支持的按键组合')
            pyautogui.hotkey(*keys)
        else:
            raise ValueError('未知桌面操作')
        time.sleep(0.35)
        return self.capture()


def notify(title, body):
    if os.name != 'nt':
        return {'delivered': False, 'reason': '仅支持 Windows 桌面通知'}
    # 参数通过环境传递，用户文本不拼接到 PowerShell 代码中。
    code = '''Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notice = New-Object System.Windows.Forms.NotifyIcon
$notice.Icon = [System.Drawing.SystemIcons]::Information
$notice.Visible = $true
$notice.ShowBalloonTip(8000, $env:JOB_NOTICE_TITLE, $env:JOB_NOTICE_BODY, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 10
$notice.Dispose()
'''
    import base64
    env = {**os.environ, 'JOB_NOTICE_TITLE': title[:63], 'JOB_NOTICE_BODY': body[:255]}
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                      '-EncodedCommand', base64.b64encode(code.encode('utf-16-le')).decode()],
                     env=env, creationflags=subprocess.CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'delivered': None, 'requested': True, 'note': '已请求桌面通知；系统免打扰可能隐藏通知，请同时在聊天中提醒'}
