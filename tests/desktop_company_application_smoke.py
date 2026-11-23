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

from PIL import Image
from playwright.sync_api import Error, expect, sync_playwright

from desktop_chrome_setup_smoke import ROOT, isolated_chrome
from desktop_configuration_smoke import desktop_page, environment, open_page
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
    ('竹江研发', '/jobs/manual?scenario=search', '软件测试工程师'),
    ('鹭江研发', '/jobs/manual-next?scenario=popup', '软件测试工程师'),
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
            ('微江研发', 'needs_user'), ('北林智造', 'success'), ('微海研发', 'uncertain'), (COMPANIES[-1][0], 'needs_user'),
        ]
        actions = store.conn.execute(
            '''SELECT a.status, p.company, a.evidence_id FROM actions a JOIN attempts p ON p.id=a.attempt_id
               WHERE a.run_id=? AND a.kind='submit' ORDER BY a.rowid''', (run,)
        ).fetchall()
        assert [(action['company'], action['status']) for action in actions] == [
            ('北林智造', 'succeeded'), ('微海研发', 'uncertain'),
        ]
        assert all(action['evidence_id'] is None for action in actions)
        assert store.one('runs', run)['status'] == 'paused'
        assert 'BL-LOCAL-001' in next(row['reason'] for row in rows if row['status'] == 'success')
        manual = store.conn.execute('SELECT id,company,status,reason FROM attempts WHERE run_id=?', (manual_run,)).fetchall()
        assert {(row['company'], row['status']) for row in manual} == {(item[0], 'needs_user') for item in MANUAL_COMPANIES}
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
            if row['company'] == COMPANIES[-1][0]:
                assert not fields, '公司不符或需安全验证时只交办'
                continue
            assert fields['profile.name'] == '合成求职者' and fields['profile.education.0.school'] == '合成大学'
            assert 'profile.contact.email' not in fields, '原有邮箱不能覆盖'
            if row['status'] == 'success':
                assert fields['profile.education.0.major'] == '合成专业' and fields['resume'] == 'synthetic-resume.pdf'


def verify_page_guards(context, base):
    script = (ROOT / 'apps/desktop/src-tauri/src/company_page.js').read_text(encoding='utf-8')
    observe = '(args) => ' + script.replace('__COMPANY_FORM_ARGS__', 'args')
    page = context.new_page()
    try:
        page.goto(base + '/jobs/north')
        args = {'url': page.url, 'company': '北林智造'}
        before = page.evaluate(observe, args)
        control = next(item for item in before['controls'] if item['label'] == '申请人姓名')
        page.locator('header p').evaluate("(node) => { node.textContent = '应聘岗位：其他岗位'; }")
        action = dict(args, document=before['document'], revision=before['revision'], controls=before['controls'],
                      action={'kind': 'fill', 'ref': control['ref'], 'value': '合成姓名'})
        try:
            page.evaluate(observe, action)
        except Error as error:
            assert 'WEB_STALE' in str(error), error
        else:
            raise AssertionError('岗位变更后旧观察仍可填写')
        assert page.locator('input[name=name]').input_value() == ''
        page.get_by_text('新增教育经历').click()
        modal = page.evaluate(observe, args)
        assert modal['modal'] and modal['companyMatched'] and modal['jobLabels'] == ['应聘岗位：其他岗位']
        page.locator('header h1').evaluate("(node) => { node.textContent = '另一家企业'; }")
        assert not page.evaluate(observe, args)['companyMatched']
        page.set_content('''<form><input aria-label="学号" value="123" aria-invalid="true">
            <div contenteditable="true" aria-label="教育经历" aria-required="true">已有内容</div>
            <button type="submit">确认提交</button></form>''')
        before = page.evaluate(observe, args)
        fields = {item['label']: item for item in before['controls']}
        assert fields['学号']['invalid'] and fields['教育经历']['occupied']
        action = dict(args, document=before['document'], revision=before['revision'], controls=before['controls'],
                      action={'kind': 'ready', 'ref': fields['确认提交']['ref']})
        try:
            page.evaluate(observe, action)
        except Error as error:
            assert 'WEB_REQUIRED' in str(error), error
        else:
            raise AssertionError('网页仍显示字段校验错误时不能完成提交前复核')
    finally:
        page.close()

def check_desktop_login_batch(playwright, app, chrome, folder, env, base):
    """从真实工作台打开 12 行，检验登录等待和 Codex 离线时的恢复入口。"""
    data = folder / 'data'
    with isolated_chrome(playwright, chrome, folder) as (tab, _, _):
        tab.context.unroute('**/*')
        def local_login(route):
            if not route.request.url.startswith(base + '/jobs/pool-'):
                route.abort()
                return
            company = '批次公司' + urlsplit(route.request.url).path.rsplit('-', 1)[1]
            content = f'<h1>{company}</h1><section><label>验证码<input autocomplete="one-time-code"></label>' \
                '<button onclick="this.parentElement.remove()">完成模拟登录</button></section><h2>软件测试工程师</h2>'
            route.fulfill(content_type='text/html; charset=utf-8', body=content)
        tab.context.route('**/*', local_login)
        with closing(Store(data)) as store:
            resume = folder / 'pool-resume.pdf'
            Image.new('P', (64, 64)).save(resume)
            save_profile(store, None, {'name':'批次测试'}, resume, True)
            table = folder / 'companies.csv'
            with table.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(('公司','申请网站','岗位'))
                writer.writerows((f'批次公司{i}', f'{base}/jobs/pool-{i}', '软件测试工程师') for i in range(1, 13))
            ticket = preview_run(store, {'channels':['web'],'company_table':str(table),'web_submit':False,
                'execution_mode':'ai','target_roles':[],'employment_type':'any','window_title':'隔离批次测试'})
            run = start_run(store, {'ticket':ticket['ticket'],'confirmed':True})['run_id']
        with desktop_page(playwright, app, folder, env) as (desk, _):
            open_page(desk, '投递任务')
            desk.get_by_role('button', name='打开工作台').click()
            desk.get_by_role('button', name='打开本批网页').click()
            start = desk.get_by_role('button', name='我已登录，开始填写')
            expect(start).to_be_enabled(timeout=60000)
            sites = [page for page in tab.context.pages if page.url.startswith(base + '/jobs/pool-')]
            assert {urlsplit(page.url).path for page in sites} == {f'/jobs/pool-{i}' for i in range(1,6)}
            queue = desk.get_by_role('list', name='公司网申队列')
            expect(queue.get_by_text('尚未打开', exact=True)).to_have_count(7)
            start.click()
            resume = desk.get_by_role('button', name='继续检查', exact=True)
            expect(resume).to_be_enabled(timeout=60000)
            expect(queue.get_by_text('待登录', exact=True)).to_have_count(5)
            sites[1].get_by_role('button', name='完成模拟登录').click()
            resume.click()
            expect(desk.get_by_role('alert')).to_contain_text('请连接 Codex', timeout=60000)
            with closing(Store(data)) as store:
                assert store.one('runs', run)['status'] == 'paused'
                assert not store.conn.execute('SELECT 1 FROM actions').fetchone()
                assert not store.conn.execute('SELECT thread_id FROM app_sessions WHERE run_id=?',(run,)).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', type=Path,
                        default=Path(os.environ['ProgramFiles']) / 'Google/Chrome/Application/chrome.exe')
    parser.add_argument('--worker', type=Path, help='可选的独立构建 worker')
    parser.add_argument('--workbench', type=Path, help='额外验收构建后的桌面程序及 12 行批次')
    args = parser.parse_args()
    env = environment()
    cargo = shutil.which('cargo', path=env['PATH'])
    if cargo is None:
        parser.error('找不到 Rust Cargo')

    with (TemporaryDirectory(ignore_cleanup_errors=True) as temporary, fixture_server() as base,
          sync_playwright() as playwright):
        folder = Path(temporary) / 'site-batch'
        if args.workbench:
            check_desktop_login_batch(playwright,args.workbench.resolve(strict=True),args.chrome.resolve(strict=True),
                                     Path(temporary) / 'workbench', env, base)
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
            verify_page_guards(tab.context, base)
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
                deadline = time.monotonic() + 360
                while process.poll() is None and time.monotonic() < deadline:
                    # 维持 Playwright 事件循环以处理隔离 Chrome 的网络路由。
                    tab.wait_for_timeout(100)
                    for name in ('fixture-close-auto','fixture-login-second','fixture-login-first'):
                        signal = data / name
                        if not signal.exists() or signal.stat().st_size:
                            continue
                        sites = [page for page in tab.context.pages if page.url.startswith(base + '/jobs/')]
                        if name == 'fixture-close-auto':
                            counts = {urlsplit(page.url).path: page.evaluate('window.testSubmitCount') for page in sites}
                            assert len(sites) == len(counts) == 5
                            assert counts == {'/jobs/foreign':0,'/jobs/pine':0,'/jobs/river':0,'/jobs/north':1,'/jobs/harbor':1}, counts
                            for site in sites:
                                site.close()
                        else:
                            path = '/jobs/manual-next' if name == 'fixture-login-second' else '/jobs/manual'
                            site = next(page for page in sites if urlsplit(page.url).path == path)
                            site.get_by_role('button', name='完成模拟登录').click()
                        signal.write_text('ok', encoding='utf-8')
                if process.poll() is None:
                    process.kill()
                    raise TimeoutError('公司网申浏览器验收超时')
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
            verify_ledger(data, run, manual_run)
            company_pages = [page for page in tab.context.pages if page.url.startswith(base + '/jobs/')]
            assert len(company_pages) == 3, '应保留两个岗位的表单及第二家公司的入口原标签'
            assert all(page.evaluate('window.testSubmitCount') == 0 for page in company_pages)
            drafts = [page for page in company_pages if page.locator('#application').is_visible() or page.locator('#review').is_visible()]
            assert len(drafts) == 2 and all(page.locator('#school').input_value() == '合成大学' for page in drafts)
            popup = next(page for page in drafts if urlsplit(page.url).path == '/jobs/manual-next')
            assert popup.locator('#review').is_visible()
            assert popup.locator('#school').get_attribute('data-selected') == '合成大学', '联想结果须实际选中'
            assert popup.get_by_label('已有教育经历').input_value() == '已有院校'
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
