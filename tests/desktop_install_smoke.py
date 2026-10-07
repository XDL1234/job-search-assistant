"""当前电脑安装版启动检查；不创建模拟任务、不访问招聘网站。"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import uuid

from playwright.sync_api import sync_playwright, expect
from desktop_app_smoke import close_window

ROOT = Path(__file__).resolve().parents[1]


def running(pid):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    code=ctypes.c_ulong()
    kernel.GetExitCodeProcess(handle,ctypes.byref(code));kernel.CloseHandle(handle)
    return code.value==259


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--shortcut',type=Path,required=True)
    parser.add_argument('--connect',action='store_true',help='发布后才执行真实 Codex 连接检查')
    args=parser.parse_args()
    folder=ROOT/'.verification/desktop-release'/('installed-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={k:v for k,v in os.environ.items() if not k.startswith(('JOB_ASSISTANT_','PYTHON','CARGO_','RUSTUP_'))}
    env['PATH']=os.environ['SystemRoot']+'/System32'
    env['JOB_ASSISTANT_DATA_DIR']=str(folder/'data')
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']=f'--remote-debugging-port={port}'
    env['WEBVIEW2_USER_DATA_FOLDER']=str(folder/'webview')
    # 用实际桌面快捷方式启动；路径按 PowerShell 字面量转义，不拼入可执行表达式。
    literal=str(args.shortcut.resolve()).replace("'","''")
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    output=subprocess.check_output([str(powershell),'-NoProfile','-Command',f"(Start-Process -FilePath '{literal}' -PassThru).Id"],
        env=env,cwd=folder,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    pid=int(output.strip())
    report={'pid':pid,'shortcut':str(args.shortcut),'inference_requested':False}
    print(json.dumps({'folder':str(folder),'pid':pid},ensure_ascii=False),flush=True)
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            if not running(pid):raise RuntimeError('安装版提前退出')
            try:
                opener.open(f'http://127.0.0.1:{port}/json/version',timeout=1).close();break
            except OSError:time.sleep(.5)
        with sync_playwright() as playwright:
            browser=playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
            page=browser.contexts[0].pages[0]
            expect(page.get_by_role('heading',name='今天，开始新的机会')).to_be_visible(timeout=30000)
            expect(page.get_by_text('测试版 0.3.0-beta.1 · 部分功能未开放',exact=True)).to_be_visible()
            expect(page.get_by_text('浏览器界面预览，执行器未连接。')).to_have_count(0)
            expect(page.get_by_text('还没有投递任务',exact=True)).to_be_visible()
            page.screenshot(path=str(folder/'installed-home.png'))
            report['embedded_ui_and_worker']='passed'
            page.get_by_role('navigation').get_by_role('button',name='投递记录',exact=True).click()
            page.get_by_role('button',name='导出 Excel',exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('记录已导出',timeout=20000)
            report['worker_export']='passed'
            if args.connect:
                page.get_by_role('button',name='设置与帮助',exact=True).click()
                page.get_by_role('button',name='连接 Codex',exact=True).click()
                expect(page.locator('.settings button').filter(has_text='连接 Codex')).to_be_enabled(timeout=90000)
                report['connection_text']=page.locator('.settings').inner_text()
                page.screenshot(path=str(folder/'installed-connection.png'))
            close_window(pid)
            deadline=time.monotonic()+75
            while running(pid) and time.monotonic()<deadline:time.sleep(.25)
            assert not running(pid),'关闭后应用仍存活'
            report['close']='passed'
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        if running(pid):close_window(pid)
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
