"""真实 Tauri/WebView2 窗口验收；调试端口仅供本测试，全部使用模拟数据。"""
import argparse
import ctypes
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from desktop_dev import environment
from playwright.sync_api import sync_playwright, expect


def close_window(pid):
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(ctypes.c_void_p(hwnd)):
            user.PostMessageW(ctypes.c_void_p(hwnd), 0x10, 0, 0)
        return True
    user.EnumWindows(callback_type(visit), 0)


def focus_simulation(title):
    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    user.GetForegroundWindow.restype = ctypes.c_void_p
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    matches=[]
    def visit(hwnd, _):
        text=ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(ctypes.c_void_p(hwnd),text,512)
        if title in text.value and user.IsWindowVisible(ctypes.c_void_p(hwnd)):
            matches.append(hwnd)
        return True
    user.EnumWindows(callback_type(visit),0)
    if not matches:raise RuntimeError('未找到独立模拟浏览器窗口')
    hwnd=ctypes.c_void_p(matches[-1])
    current=kernel.GetCurrentThreadId()
    foreground_thread=user.GetWindowThreadProcessId(user.GetForegroundWindow(),None)
    user.AttachThreadInput(current,foreground_thread,True)
    try:
        user.ShowWindow(hwnd,9)
        user.BringWindowToTop(hwnd)
        user.SetForegroundWindow(hwnd)
    finally:
        user.AttachThreadInput(current,foreground_thread,False)
    return user.GetForegroundWindow()==hwnd.value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--lifecycle', action='store_true', help='验证启动即暂停、恢复与关闭，不消耗模型推理额度')
    parser.add_argument('--protocol-fixture', action='store_true', help='使用离线协议替身验证故障路径，不代表真实模型验收')
    parser.add_argument('--close-connecting', action='store_true', help='验证连接初始化期间关闭不遗留进程')
    args = parser.parse_args()
    folder = ROOT / '.verification/desktop-app' / ('smoke-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    env = environment()
    if args.protocol_fixture:
        if args.execute:
            raise ValueError('真实执行不能使用协议替身')
        env['JOB_ASSISTANT_CODEX']=str(ROOT/'apps/desktop/src-tauri/target/debug/examples/protocol_fixture.exe')
    if args.close_connecting:
        if not args.protocol_fixture:
            raise ValueError('关闭初始化测试需要协议替身')
        env['JOB_ASSISTANT_FIXTURE_DELAY_MS']='8000'
        env['JOB_ASSISTANT_FIXTURE_PID_PATH']=str(folder/'fixture.pid')
    env['JOB_ASSISTANT_DATA_DIR'] = str(folder / 'data')
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'--remote-debugging-port={port}'
    env['WEBVIEW2_USER_DATA_FOLDER'] = str(folder / 'webview')
    process = subprocess.Popen([str(ROOT / 'apps/desktop/src-tauri/target/debug/job-search-desktop.exe')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({'folder': str(folder), 'pid': process.pid, 'port': port}), flush=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = {'ui': False, 'connection': False, 'execution': 'not_requested'}
    report['server']='offline_protocol_fixture' if args.protocol_fixture else 'real_codex'
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'应用提前退出：{process.returncode}')
            try:
                opener.open(f'http://127.0.0.1:{port}/json/version', timeout=1).close()
                break
            except OSError:
                time.sleep(.5)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
            page = browser.contexts[0].pages[0]
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            expect(page.get_by_role('heading', name='今天，开始新的机会')).to_be_visible(timeout=30000)
            expect(page.get_by_text('浏览器界面预览，执行器未连接。')).to_have_count(0)
            expect(page.get_by_role('navigation', name='主导航').get_by_role('button')).to_have_count(8)
            page.screenshot(path=str(folder / '01-home.png'))
            page.get_by_role('button', name='设置与帮助', exact=True).click()
            page.get_by_role('button', name='连接 Codex', exact=True).click()
            if args.close_connecting:
                for _ in range(100):
                    if (folder/'fixture.pid').exists(): break
                    time.sleep(.05)
                child_pid=int((folder/'fixture.pid').read_text())
                close_window(process.pid)
                process.wait(timeout=75)
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.restype=ctypes.c_void_p
                handle=kernel.OpenProcess(0x1000,False,child_pid)
                if handle:
                    code=ctypes.c_ulong()
                    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
                    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                    kernel.GetExitCodeProcess(handle,ctypes.byref(code))
                    kernel.CloseHandle(handle)
                    assert code.value!=259, f'初始化子进程 {child_pid} 仍存活'
                report['close_during_connect']='passed'
                return
            expect(page.locator('.topline').get_by_role('button', name='Codex 已连接', exact=True)).to_be_visible(timeout=90000)
            report['connection'] = True
            page.screenshot(path=str(folder / '02-connection.png'))
            page.get_by_role('button', name='新建本地演示', exact=True).click()
            expect(page.get_by_role('dialog', name='确认本轮模拟任务')).to_be_visible()
            page.get_by_role('button', name='确认范围，创建任务', exact=True).click()
            expect(page.get_by_role('heading', name='执行工作台', exact=True)).to_be_visible()
            expect(page.get_by_text('已暂停', exact=True)).to_be_visible(timeout=15000)
            page.screenshot(path=str(folder / '03-running.png'))
            report['ui'] = True
            assert not errors, errors
            print(json.dumps({'ui':'passed', 'connection':'passed'},ensure_ascii=False),flush=True)
            if args.lifecycle:
                import sqlite3
                from contextlib import closing
                for _ in range(2):
                    page.get_by_role('button', name='开始 / 恢复', exact=True).click()
                    page.get_by_role('button', name='暂停接管', exact=True).click()
                    time.sleep(6)
                    with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                        assert db.execute('SELECT status FROM runs').fetchone()[0]=='paused'
                        assert db.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==0
                        assert db.execute('SELECT thread_id FROM app_sessions').fetchone()[0] is None
                report['immediate_pause_and_resume']='passed'
                if args.protocol_fixture:
                    page.get_by_role('button', name='开始 / 恢复', exact=True).click()
                    expect(page.get_by_role('alert').filter(has_text='额度已用尽').first).to_be_visible(timeout=20000)
                    with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                        assert db.execute('SELECT state FROM app_sessions').fetchone()[0]=='error'
                        assert db.execute('SELECT status FROM runs').fetchone()[0]=='paused'
                    report['failed_turn_visible']='passed'
                page.get_by_role('button', name='开始 / 恢复', exact=True).click()
            if args.execute:
                # 使用独立 Chrome 配置，避免用户已有标签页改变测试窗口。
                import sqlite3
                from contextlib import closing
                with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                    config=json.loads(db.execute('SELECT config FROM runs ORDER BY created DESC LIMIT 1').fetchone()[0])
                simulation=playwright.chromium.launch_persistent_context(str(folder/'simulation-browser'),channel='chrome',headless=False,
                    viewport=None,args=['--window-size=1440,1050','--no-first-run'])
                simulation_page=simulation.pages[0]
                simulation_page.goto(config['simulation_url'])
                page.get_by_role('button', name='开始 / 恢复', exact=True).click()
                simulation_page.bring_to_front()
                assert focus_simulation(config['window_title']), '模拟窗口未成为前台'
                deadline=time.monotonic()+600
                while time.monotonic()<deadline:
                    time.sleep(3)
                    sys.path.insert(0,str(ROOT/'skills/job-search-assistant/scripts'))
                    from job_assistant.desktop import foreground
                    (folder/'foreground.json').write_text(json.dumps(foreground(),ensure_ascii=False),encoding='utf-8')
                    import sqlite3
                    with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                        db.row_factory=sqlite3.Row
                        attempts=[dict(r) for r in db.execute('SELECT company,channel,status FROM attempts')]
                        session=dict(db.execute('SELECT * FROM app_sessions').fetchone())
                        attention=[dict(r) for r in db.execute('SELECT subject,reason,resolved FROM attention')]
                    (folder/'progress.json').write_text(json.dumps({'attempts':attempts,'session':session,'attention':attention},ensure_ascii=False,indent=2),encoding='utf-8')
                    (folder/'model-output.txt').write_text(page.get_by_role('log').inner_text(),encoding='utf-8')
                    if session['state']=='error':raise AssertionError(session['error'])
                    if session['state']=='idle' and session['thread_id']:
                        assert sum(a['status']=='success' for a in attempts)==2, attempts
                        assert attention, '没有面试转人工记录'
                        report['execution']='passed'
                        page.screenshot(path=str(folder/'04-complete.png'))
                        simulation.close()
                        break
                else:raise TimeoutError('模拟执行未在10分钟内完成')
            # 走真实窗口关闭事件，验证正常收尾而非直接 kill。
            close_window(process.pid)
            process.wait(timeout=75)
            assert process.returncode==0,process.returncode
            report['close']='passed'
            if args.lifecycle:
                with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                    assert db.execute('SELECT status FROM runs').fetchone()[0]=='paused'
                    assert db.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==0
                process=subprocess.Popen([str(ROOT / 'apps/desktop/src-tauri/target/debug/job-search-desktop.exe')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                time.sleep(8)
                assert process.poll() is None, '重启应用失败'
                with closing(sqlite3.connect(folder / 'data/records.sqlite3')) as db:
                    assert db.execute('SELECT status FROM runs').fetchone()[0]=='paused'
                    assert db.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==0
                close_window(process.pid)
                process.wait(timeout=75)
                assert process.returncode==0
                report['restart_remains_paused']='passed'
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        if process.poll() is None:
            close_window(process.pid)
            try:
                process.wait(timeout=75)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
