"""用两套离线招聘页面贯通真实公司网申浏览器、资料和申请记录。"""
import argparse
from contextlib import closing, contextmanager
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from desktop_chrome_setup_smoke import ROOT, isolated_chrome
from desktop_configuration_smoke import environment
from job_assistant.app_decisions import current_generation
from job_assistant.app_runs import preview_run, start_run
from job_assistant.profiles import save_profile
from job_assistant.store import Store


COMPANIES = (
    ('微江研发', '/jobs/river?scenario=missing', '软件测试工程师'),
    ('北林智造', '/jobs/north', '嵌入式开发工程师'),
    ('微海研发', '/jobs/harbor?scenario=ambiguous', '软件测试工程师'),
    ('松林研发', '/jobs/pine?scenario=verification', '软件测试工程师'),
    ('北江招聘', '/jobs/foreign?scenario=ambiguous', '软件测试工程师'),
)
MANUAL_COMPANIES = (
    ('竹江研发', '/jobs/manual?scenario=ambiguous', '软件测试工程师'),
    ('鹭江研发', '/jobs/manual-next?scenario=ambiguous', '软件测试工程师'),
)


@contextmanager
def fixture_server():
    fixtures = ROOT / 'tests/fixtures'
    long_page = (fixtures / 'company-application-long.html').read_text(encoding='utf-8')
    steps = (fixtures / 'company-application-steps.html').read_text(encoding='utf-8')
    pages = {
        '/jobs/river': steps,
        '/jobs/north': long_page,
        '/jobs/harbor': steps.replace('微江研发', '微海研发'),
        '/jobs/pine': steps.replace('微江研发', '松林研发'),
        '/jobs/foreign': steps.replace('微江研发', '外部企业'),
        '/jobs/manual': steps.replace('微江研发', MANUAL_COMPANIES[0][0]),
        '/jobs/manual-next': steps.replace('微江研发', MANUAL_COMPANIES[1][0]),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            page = pages.get(urlsplit(self.path).path)
            if page is None:
                self.send_error(404)
                return
            encoded = page.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()


def prepare_batches(data, base):
    resume = data / 'synthetic-resume.pdf'
    from PIL import Image
    Image.new('P', (64, 64)).save(resume)
    profile = {
        'name': '合成求职者',
        'contact': {'email': 'different@example.invalid'},
        'education': [{'school': '合成大学', 'major': '合成专业'}],
    }
    with closing(Store(data)) as store:
        save_profile(store, None, profile, resume, True)
        batches = []
        for mode, companies in (('auto', COMPANIES), ('manual', MANUAL_COMPANIES)):
            company_table = data / f'synthetic-companies-{mode}.csv'
            with company_table.open('w', encoding='utf-8', newline='') as file:
                rows = csv.writer(file)
                rows.writerow(('公司', '申请网站', '岗位'))
                rows.writerows((company, base + path, job) for company, path, job in companies)
            preview = preview_run(store, {
                'channels': ['web'],
                'company_table': str(company_table),
                'execution_mode': 'ai',
                'employment_type': 'any',
                'target_roles': [],
                'window_title': '合成本地招聘',
                'web_submit': mode == 'auto',
            })
            run_id = start_run(store, {'ticket': preview['ticket'], 'confirmed': True})['run_id']
            assert store.one('runs', run_id)['status'] == 'paused'
            store.control(run_id, 'running')
            batches.append((run_id, current_generation(store, run_id)))
        return batches


def verify_ledger(data, run, manual_run):
    with closing(Store(data)) as store:
        assert not store.conn.execute('SELECT 1 FROM evidence').fetchone(), '公司网申不登记截图证据'
        assert not (data / 'company-web-frames').exists(), '实时预览和 AI 看图都不写截图文件'
        assert not list((data / 'frames').glob('*.png')), '公司网申不生成业务截图'
        rows = store.conn.execute('SELECT id,company,status,reason FROM attempts WHERE run_id=? ORDER BY rowid', (run,)).fetchall()
        assert [(row['company'], row['status']) for row in rows] == [
            (COMPANIES[-1][0], 'needs_user'), ('松林研发', 'needs_user'), ('微江研发', 'needs_user'),
            ('北林智造', 'success'), ('微海研发', 'uncertain'),
        ]
        actions = store.conn.execute(
            '''SELECT a.status, p.company, a.evidence_id FROM actions a JOIN attempts p ON p.id=a.attempt_id
               WHERE a.run_id=? AND a.kind='submit' ORDER BY a.rowid''', (run,)
        ).fetchall()
        assert [(action['company'], action['status']) for action in actions] == [
            ('北林智造', 'succeeded'), ('微海研发', 'uncertain'),
        ]
        assert all(action['evidence_id'] is None for action in actions)
        assert 'BL-LOCAL-001' in next(row['reason'] for row in rows if row['status'] == 'success')
        manual = store.conn.execute('SELECT id,company,status,reason FROM attempts WHERE run_id=?', (manual_run,)).fetchall()
        assert [(row['company'], row['status']) for row in manual] == [(MANUAL_COMPANIES[0][0], 'needs_user')]
        assert '等待你在当前网页确认并提交' in manual[0]['reason']
        assert store.one('runs', manual_run)['status'] == 'paused'
        assert not store.conn.execute('SELECT 1 FROM actions WHERE run_id=?', (manual_run,)).fetchone()
        for row in [*rows, *manual]:
            if row['status'] != 'success':
                assert store.conn.execute(
                    "SELECT 1 FROM attention WHERE subject=? AND resolved=0", (row['id'],)
                ).fetchone()
            recorded = store.conn.execute(
                "SELECT source, value, evidence_id FROM fields WHERE attempt_id=?", (row['id'],)
            ).fetchall()
            assert all(item['evidence_id'] is None for item in recorded)
            fields = {item['source']: item['value'] for item in recorded}
            if row['company'] in (COMPANIES[-1][0], '松林研发'):
                assert not fields, '公司不符或需安全验证时只交办'
                continue
            assert fields['profile.name'] == '合成求职者' and fields['profile.education.0.school'] == '合成大学'
            assert 'profile.contact.email' not in fields, '原有邮箱不能覆盖'
            if row['status'] == 'success':
                assert fields['profile.education.0.major'] == '合成专业' and fields['resume'] == 'synthetic-resume.pdf'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', type=Path,
                        default=Path(os.environ['ProgramFiles']) / 'Google/Chrome/Application/chrome.exe')
    parser.add_argument('--worker', type=Path, help='可选的独立构建 worker')
    args = parser.parse_args()
    env = environment()
    cargo = shutil.which('cargo', path=env['PATH'])
    if cargo is None:
        parser.error('找不到 Rust Cargo')

    with (TemporaryDirectory(ignore_cleanup_errors=True) as temporary, fixture_server() as base,
          sync_playwright() as playwright):
        folder = Path(temporary) / 'site-batch'
        with isolated_chrome(playwright, args.chrome.resolve(strict=True), folder,
                             launch_args=('--force-device-scale-factor=1.5', '--window-size=1708,1020')) as (tab, _, _):
            tab.context.unroute('**/*')
            opener = build_opener(ProxyHandler({}))

            def serve_fixture(route):
                url = route.request.url
                if route.request.method != 'GET' or not url.startswith(base + '/jobs/'):
                    route.abort()
                    return
                try:
                    with opener.open(url, timeout=2) as response:
                        route.fulfill(status=response.status, content_type=response.headers['Content-Type'],
                                      body=response.read())
                except HTTPError as error:
                    route.fulfill(status=error.code)

            tab.context.route('**/*', serve_fixture)
            data = folder / 'data'
            (run, generation), (manual_run, manual_generation) = prepare_batches(data, base)
            env.pop('JOB_ASSISTANT_WORKER', None)
            if args.worker:
                env['JOB_ASSISTANT_WORKER'] = str(args.worker.resolve(strict=True))
            env.update(JOB_ASSISTANT_DATA_DIR=str(data), JOB_ASSISTANT_REPO=str(ROOT),
                       JOB_ASSISTANT_TEST_WEB_RUN=run, JOB_ASSISTANT_TEST_WEB_GENERATION=str(generation),
                       JOB_ASSISTANT_TEST_WEB_MANUAL_RUN=manual_run,
                       JOB_ASSISTANT_TEST_WEB_MANUAL_GENERATION=str(manual_generation))
            command = [cargo, 'test', '--locked', '--lib', 'synthetic_company_batch', '--', '--ignored', '--nocapture']
            with subprocess.Popen(command, cwd=ROOT / 'apps/desktop/src-tauri', env=env) as process:
                deadline = time.monotonic() + 240
                while process.poll() is None and time.monotonic() < deadline:
                    # 维持 Playwright 事件循环以处理隔离 Chrome 的网络路由。
                    tab.wait_for_timeout(100)
                if process.poll() is None:
                    process.kill()
                    raise TimeoutError('公司网申浏览器验收超时')
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
            verify_ledger(data, run, manual_run)
            company_pages = [page for page in tab.context.pages if page.url.startswith(base + '/jobs/')]
            counts = {urlsplit(page.url).path: page.evaluate('window.testSubmitCount') for page in company_pages}
            assert len(company_pages) == len(counts), '每家公司应该只开一个测试标签页'
            assert counts == {'/jobs/foreign': 0, '/jobs/pine': 0, '/jobs/river': 0,
                              '/jobs/north': 1, '/jobs/harbor': 1, '/jobs/manual': 0}, counts
            manual = next(page for page in tab.context.pages if urlsplit(page.url).path == '/jobs/manual')
            assert manual.locator('#application').is_visible()
            assert manual.locator('#full-name').input_value() == '合成求职者'
            assert manual.locator('#school').input_value() == '合成大学'
            assert manual.locator('#mail').input_value() == 'existing@example.invalid'
            manual.get_by_role('button', name='确认提交').click()
            assert manual.evaluate('window.testSubmitCount') == 1, '用户可以在停留的网页上自行提交'
            verify_ledger(data, run, manual_run)

    print('两套模拟布局验收通过，覆盖实时画面、动态填写、手动提交接管、自动提交及回执、全程无截图留档。', flush=True)


if __name__ == '__main__':
    main()
