"""Drive Windows file dialogs in isolated desktop smoke tests."""
import ctypes
from ctypes import wintypes
import subprocess
import time


def answer_export_dialog(app_pid, destination=None):
    _answer_file_dialog(app_pid, destination, '导出投递记录')


def answer_open_dialog(app_pid, source):
    _answer_file_dialog(app_pid, source)


def _answer_file_dialog(app_pid, path, expected_title=None):
    user = ctypes.windll.user32
    user.GetDlgItem.restype = wintypes.HWND
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def wait_for_dialog():
        pids = set(subprocess.check_output([
            'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={int(app_pid)} AND Name=\"powershell.exe\"').ProcessId",
        ], text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5).split())
        matches = []

        @callback
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if str(pid.value) in pids and user.IsWindowVisible(wintypes.HWND(hwnd)):
                handle = wintypes.HWND(hwnd)
                if expected_title:
                    title = ctypes.create_unicode_buffer(128)
                    user.GetWindowTextW(handle, title, len(title))
                    if title.value == expected_title:
                        matches.append(handle)
                else:
                    name = ctypes.create_unicode_buffer(40)
                    user.GetClassNameW(handle, name, len(name))
                    if name.value == '#32770':
                        matches.append(handle)
            return True

        user.EnumWindows(visit, 0)
        return matches[0] if len(matches) == 1 else None

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        dialog = wait_for_dialog()
        if dialog:
            break
        time.sleep(.2)
    else:
        raise AssertionError('未找到隔离应用的文件对话框')

    if path is not None:
        path = path.resolve()
        edits = []

        @callback
        def find_filename(hwnd, _):
            handle = wintypes.HWND(hwnd)
            name = ctypes.create_unicode_buffer(40)
            user.GetClassNameW(handle, name, len(name))
            if name.value == 'Edit' and user.IsWindowVisible(handle) and user.GetDlgCtrlID(handle) in (1001, 1148):
                edits.append(handle)
            return True

        user.EnumChildWindows(dialog, find_filename, 0)
        edit = edits[0] if len(edits) == 1 else None
        if not edit or not user.SendMessageW(edit, 0x000C, 0, ctypes.c_wchar_p(str(path))):  # WM_SETTEXT
            raise AssertionError('无法填写文件路径')
    button = wintypes.HWND(user.GetDlgItem(dialog, 2 if path is None else 1))
    if not button or not user.PostMessageW(button, 0x00F5, 0, 0):  # BM_CLICK
        raise AssertionError('无法操作文件对话框')
