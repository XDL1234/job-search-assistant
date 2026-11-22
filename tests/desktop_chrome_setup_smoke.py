"""用独立 Chrome 资料与合成 BOSS 页面验证原生首次连接。"""
import argparse
from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from playwright.sync_api import Error, expect, sync_playwright

from desktop_configuration_smoke import ROOT, desktop_page, environment, open_page

LOGIN = 'https://www.zhipin.com/web/user/'
JOBS = 'https://www.zhipin.com/web/geek/job'
TITLES = {LOGIN: 'BOSS 登录', JOBS: '合成 BOSS 岗位'}


@contextmanager
def isolated_chrome(playwright, chrome, folder, *, launch_args=()):
    folder.mkdir(parents=True)
    profile = folder / 'data' / 'chrome-profile'
    profile.mkdir(parents=True)
    active_port = profile / 'DevToolsActivePort'
    process = subprocess.Popen([
        str(chrome), f'--user-data-dir={profile}', '--headless=new',
        '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
        '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
        '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND', *launch_args, 'about:blank',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    browser = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None, '隔离 Chrome 提前退出'
            if active_port.is_file():
                lines = active_port.read_text(encoding='utf-8').splitlines()
                if len(lines) >= 2:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError('隔离 Chrome 没有生成调试端点')
        port, path = lines[:2]
        browser = playwright.chromium.connect_over_cdp(f'ws://127.0.0.1:{int(port)}{path}')
        context = browser.contexts[0]

        def serve_fixture(route):
            title = TITLES.get(route.request.url)
            if title:
                route.fulfill(content_type='text/html; charset=utf-8', body=f'<title>{title}</title><h1>{title}</h1>')
            else:
                route.abort()

        context.route('**/*', serve_fixture)
        yield context.pages[0], active_port, lines
        assert process.poll() is None, '关闭求职助手不应关闭独立 Chrome'
        assert browser.is_connected()
    finally:
        if browser and browser.is_connected():
            with suppress(Error):
                browser.new_browser_cdp_session().send('Browser.close')
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


def startup_state(page):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = page.evaluate("window.__TAURI_INTERNALS__.invoke('browser_runtime_status')")
        if not state['connecting']:
            if state.get('connection_error'):
                raise RuntimeError(state['connection_error'])
            if state['connected']:
                return state
        time.sleep(0.15)
    raise RuntimeError('专用 Chrome 未在时限内连接')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--chrome', type=Path,
                        default=Path(os.environ['ProgramFiles']) / 'Google/Chrome/Application/chrome.exe')
    args = parser.parse_args()
    folder = ROOT / '.verification/desktop-app' / ('chrome-setup-' + uuid.uuid4().hex[:8])
    env = {**environment(), 'JOB_ASSISTANT_SKIP_CHROME_SETUP': '0'}
    with sync_playwright() as playwright:
        automatic = folder / 'automatic'
        with isolated_chrome(playwright, args.chrome.resolve(strict=True), automatic) as (chrome_tab, port_file, original):
            chrome_tab.goto(JOBS)
            expect(chrome_tab).to_have_title(TITLES[JOBS])
            with desktop_page(playwright, args.app.resolve(strict=True), automatic, env) as (page, _):
                state = startup_state(page)
                assert state['connection_mode'] == 'dedicated' and state['url'] == JOBS
                assert state['pinned_target'] == state['tabs'][0]['targetId']
                assert port_file.read_text(encoding='utf-8').splitlines() == original
                assert (automatic / 'data' / 'chrome-setup-complete').is_file()
                open_page(page, '连接与诊断')
                expect(page.get_by_role('combobox', name='BOSS 标签页')).to_have_value(state['pinned_target'])
                page.get_by_role('button', name='检查当前页面', exact=True).click()
                saved_path = page.locator('.settings-diagnostic-body code')
                expect(saved_path).to_be_visible(timeout=15000)
                diagnostic = Path(saved_path.inner_text())
                assert diagnostic.is_file() and diagnostic.parent.samefile(automatic / 'data/browser-diagnostics')
            expect(chrome_tab).to_have_title(TITLES[JOBS])

        # 隔离重启时隐藏 Chrome 安装路径，启动分支若有回归也不会访问实站。
        unavailable = str(folder / 'chrome-unavailable')
        restart_env = {**env, **dict.fromkeys(
            ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'), unavailable)}
        with desktop_page(playwright, args.app.resolve(strict=True), automatic, restart_env) as (page, _):
            page.wait_for_timeout(2500)
            state = page.evaluate("window.__TAURI_INTERNALS__.invoke('browser_runtime_status')")
            assert not state['connected'] and not state['connecting'] and not state.get('connection_error'), state

        manual = folder / 'manual'
        with isolated_chrome(playwright, args.chrome.resolve(strict=True), manual) as (chrome_tab, _, _):
            chrome_tab.goto(LOGIN)
            expect(chrome_tab).to_have_title(TITLES[LOGIN])
            with desktop_page(playwright, args.app.resolve(strict=True), manual, env) as (page, _):
                state = startup_state(page)
                assert state['pinned_target'] is None and state['tabs'] == []
                open_page(page, '连接与诊断')
                manual_select = page.get_by_role('combobox', name='BOSS 标签页')
                expect(manual_select).to_be_disabled()
                chrome_tab.goto(JOBS)
                expect(chrome_tab).to_have_title(TITLES[JOBS])
                page.get_by_role('button', name='刷新标签', exact=True).click()
                expect(manual_select).to_be_enabled(timeout=15000)
                expect(manual_select).to_have_value('')
                [tab] = startup_state(page)['tabs']
                assert tab['url'] == JOBS
                manual_select.select_option(value=tab['targetId'], timeout=15000)
                expect(page.get_by_role('button', name='检查当前页面')).to_be_enabled(timeout=15000)
                assert startup_state(page)['url'] == JOBS
            expect(chrome_tab).to_have_title(TITLES[JOBS])
    report = {'first_start_reuse_and_bind': 'passed', 'completed_start_does_not_relaunch': 'passed',
              'login_fallback_and_manual_bind': 'passed',
              'app_exit_preserves_chrome': 'passed', 'diagnostic_uses_data_root': 'passed'}
    (folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
