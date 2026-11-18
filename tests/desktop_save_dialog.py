"""Drive the Windows save dialog in an isolated desktop smoke test."""
import ctypes
from ctypes import wintypes
import subprocess
import time


def answer_export_dialog(app_pid, destination=None):
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
                title = ctypes.create_unicode_buffer(128)
                user.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
                if title.value == '导出投递记录':
                    matches.append(wintypes.HWND(hwnd))
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
        raise AssertionError('未找到隔离应用的 Excel 保存对话框')

    if destination is not None:
        path = destination.resolve()
        edits = []

        @callback
        def find_filename(hwnd, _):
            handle = wintypes.HWND(hwnd)
            name = ctypes.create_unicode_buffer(40)
            user.GetClassNameW(handle, name, len(name))
            if name.value == 'Edit' and user.GetDlgCtrlID(handle) == 1001:
                edits.append(handle)
            return True

        user.EnumChildWindows(dialog, find_filename, 0)
        edit = edits[0] if len(edits) == 1 else None
        if not edit or not user.SendMessageW(edit, 0x000C, 0, ctypes.c_wchar_p(str(path))):  # WM_SETTEXT
            raise AssertionError('无法填写 Excel 导出路径')
    button = wintypes.HWND(user.GetDlgItem(dialog, 2 if destination is None else 1))
    if not button or not user.PostMessageW(button, 0x00F5, 0, 0):  # BM_CLICK
        raise AssertionError('无法操作 Excel 保存对话框')
