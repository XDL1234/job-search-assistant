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
from desktop_save_dialog import answer_export_dialog

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
    output=subprocess.check_output([str(powershell),'-NoProfile','-Command',f"(Start-Process -FilePath '{literal}' -WindowStyle Hidden -PassThru).Id"],
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
            expect(page.get_by_role('heading',name='今天的求职进展')).to_be_visible(timeout=30000)
            version=json.loads((ROOT/'apps/desktop/package.json').read_text())['version']
            expect(page.locator('.brand')).to_have_attribute('title', f'求职助手 {version}')
            expect(page.locator('.workspace-topbar').get_by_role('button', name='演示', exact=True)).to_have_count(0)
            expect(page.get_by_text('浏览器界面预览，执行器未连接。')).to_have_count(0)
            expect(page.get_by_text('暂无进行中或暂停的投递任务',exact=True)).to_be_visible()
            page.screenshot(path=str(folder/'installed-home.png'))
            report['embedded_ui_and_worker']='passed'
            page.get_by_role('navigation', name='主导航').get_by_role('button', name='投递记录', exact=True).click()
            expect(page.get_by_role('heading', name='投递记录', exact=True)).to_be_visible()
            page.get_by_role('button', name='全部日期', exact=True).click()
            record_day = page.get_by_label('记录日期', exact=True)
            record_day.fill('2020-01-02')
            page.get_by_role('button', name='应用日期', exact=True).click()
            expect(page.get_by_text('暂无投递记录', exact=True)).to_be_visible()
            page.get_by_role('button', name='清除日期', exact=True).click()
            expect(page.get_by_role('button', name='全部日期', exact=True)).to_be_visible()
            report['packaged_history_date_filter']='passed'
            page.get_by_role('navigation',name='主导航').get_by_role('button',name='投递任务',exact=True).click()
            page.get_by_role('button', name='新建投递', exact=True).click()
            page.get_by_role('button', name='编辑岗位条件', exact=True).click()
            page.get_by_label('求职城市', exact=True).fill('深圳')
            page.get_by_role('button', name='保存条件', exact=True).click()
            expect(page.get_by_text('已保存岗位条件。', exact=True)).to_be_visible()
            page.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
            expect(page.get_by_text('深圳', exact=False).first).to_be_visible()
            preferences = json.loads((folder/'data/job-preferences.json').read_text(encoding='utf-8'))
            assert preferences['boss_filters']['city'] == '深圳'
            report['packaged_job_preferences']='passed'
            expect(page.get_by_role('heading', name='新建投递', exact=True)).to_be_visible()
            expect(page.get_by_role('radio', name='脚本快速沟通', exact=True)).to_be_visible()
            expect(page.get_by_role('spinbutton')).to_be_visible()
            page.screenshot(path=str(folder/'installed-task-creation.png'))
            report['packaged_task_creation']='passed'
            page.get_by_role('navigation',name='主导航').get_by_role('button',name='投递记录',exact=True).click()
            page.get_by_role('button',name='导出 Excel',exact=True).click()
            destination = folder / '自选投递记录.xlsx'
            answer_export_dialog(pid, destination)
            expect(page.get_by_role('status')).to_contain_text('记录已导出',timeout=20000)
            assert destination.is_file()
            report['worker_export']='passed'
            page.locator('.sidebar').get_by_role('button', name='设置', exact=True).click()
            page.get_by_role('button', name='连接与诊断', exact=True).click()
            expect(page.get_by_role('button',name='连接 Chrome',exact=True)).to_be_enabled()
            expect(page.get_by_role('button',name='检查当前页面')).to_be_disabled()
            runtime=page.evaluate("window.__TAURI_INTERNALS__.invoke('browser_runtime_status')")
            assert runtime['installed'] and not runtime['connected']
            probe=json.loads(subprocess.check_output([runtime['executable'],'--job-assistant-probe'],env=env,text=True))
            assert probe['embedded_auth'] and probe['read_only'] and probe['capabilities']['existing_daemon_only']
            report['packaged_runtime_without_browser_connection']='passed'
            if args.connect:
                page.get_by_role('button',name='连接 Codex',exact=True).click()
                expect(page.get_by_role('button', name='连接 Codex', exact=True)).to_be_enabled(timeout=90000)
                report['connection_text']=page.locator('.settings-page').inner_text()
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
