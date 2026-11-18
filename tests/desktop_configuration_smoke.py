"""使用隔离数据和真实 Tauri 窗口回归配置，不启动投递或连接招聘网站。"""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'skills/job-search-assistant/scripts'))
from desktop_dev import environment
from desktop_app_smoke import close_window
from desktop_save_dialog import answer_export_dialog
from job_assistant.profiles import extract_profile, save_profile
from job_assistant.store import Store
from playwright.sync_api import expect, sync_playwright


@contextmanager
def desktop_page(playwright, app, folder, env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**env, 'JOB_ASSISTANT_DATA_DIR': str(folder / 'data'),
           'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={port}',
           'WEBVIEW2_USER_DATA_FOLDER': str(folder / f'webview-{port}')}
    with (folder / 'app.log').open('ab') as log:
        process = subprocess.Popen([str(app)], env=env, stdout=log, stderr=subprocess.STDOUT)
        print(json.dumps({'folder': str(folder), 'pid': process.pid}, ensure_ascii=False), flush=True)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(90):
                if process.poll() is not None:
                    raise RuntimeError(f'应用提前退出：{process.returncode}。请检查是否已有同标识实例。')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/json/version', timeout=1):
                        break
                except OSError:
                    time.sleep(.5)
            else:
                raise RuntimeError('未能连接隔离测试窗口的 WebView2')
            browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
            page = browser.contexts[0].pages[0]
            expect(page.get_by_role('heading', name='今天的求职进展', exact=True)).to_be_visible(timeout=30000)
            yield page, process.pid
        finally:
            if process.poll() is None:
                close_window(process.pid)
                process.wait(timeout=60)
        assert process.returncode == 0


def open_page(page, name):
    page.get_by_role('navigation', name='主导航').get_by_role('button', name=name, exact=True).click()


def open_task(page):
    open_page(page, '投递任务')
    page.get_by_role('button', name='新建投递', exact=True).click()
    expect(page.get_by_role('heading', name='新建投递', exact=True)).to_be_visible()


def open_settings(page, tab):
    page.locator('.sidebar').get_by_role('button', name='设置', exact=True).click()
    page.get_by_role('navigation', name='设置分类').get_by_role('button', name=tab, exact=True).click()


def edit_preferences(page):
    page.get_by_role('button', name='编辑岗位条件', exact=True).click()
    drawer = page.get_by_role('dialog', name='编辑岗位条件')
    expect(drawer).to_be_visible()
    expect(drawer.get_by_role('button', name='保存条件', exact=True)).to_be_enabled(timeout=15000)
    return drawer


def preview(page, label='预览本轮范围'):
    page.get_by_role('button', name=label, exact=True).click()
    expect(page.get_by_role('heading', name='确认本轮范围', exact=True)).to_be_visible(timeout=15000)
    return page.get_by_role('main')


def set_field(page, label, value):
    field = page.get_by_label(label, exact=True)
    if field.evaluate('element => element.localName') == 'select':
        field.select_option(value)
    else:
        field.fill(value)


def check_record_dates(page, pid, data, folder):
    from PIL import Image

    first_day = datetime(2020, 6, 12, 0, 20)
    next_day = datetime(2020, 6, 13, 0, 20)
    first_utc = first_day.astimezone(timezone.utc).isoformat()
    next_utc = next_day.astimezone(timezone.utc).isoformat()
    fixtures = (
        (uuid.uuid4().hex, '合成隔日更新', '共享岗位', 'boss', 'failed', first_utc, next_utc),
        (uuid.uuid4().hex, '合成次日记录', '共享岗位', 'boss', 'failed', next_utc, next_utc),
        (uuid.uuid4().hex, '合成旧格式记录', '其他岗位', 'web', 'uncertain', None, first_utc),
    )
    with closing(sqlite3.connect(data / 'records.sqlite3')) as conn, conn:
        [(run_id,)] = conn.execute('SELECT id FROM runs').fetchall()
        conn.executemany('''INSERT INTO attempts
            (id, run_id, company, job, channel, status, reason, started, updated, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [(identity, run_id, company, job, channel, status, '离线记录核验', started, updated, 'https://example.invalid/apply')
             for identity, company, job, channel, status, started, updated in fixtures])
        conn.execute('INSERT INTO fields VALUES (?, ?, ?, ?, NULL)', (fixtures[0][0], '申请岗位', '共享岗位', '模拟资料'))
    frame = folder / 'synthetic-record.png'
    Image.new('RGB', (520, 310), '#e9efff').save(frame)
    with closing(Store(data)) as store:
        store.evidence(fixtures[0][0], frame, 'pre_submit', ['申请岗位'])

    open_page(page, '投递记录')
    expect(page.get_by_role('heading', name='投递记录', exact=True)).to_be_visible()
    first, later, fallback = (page.get_by_role('row').filter(has_text=company)
                              for _, company, *_ in fixtures)
    for row in (first, later, fallback):
        expect(row).to_be_visible(timeout=15000)
    page.get_by_role('button', name='全部日期', exact=True).click()
    calendar = page.get_by_role('dialog', name='按投递日期筛选')
    expect(calendar).to_be_visible()
    day = page.get_by_label('记录日期', exact=True)
    day.fill(first_day.date().isoformat())
    expect(calendar.get_by_role('button', name='2020年6月12日', exact=True)).to_have_attribute('aria-pressed', 'true')
    page.screenshot(path=str(folder / 'history-calendar.png'))
    calendar.get_by_role('button', name='应用日期', exact=True).click()
    expect(first).to_be_visible()
    expect(first.locator('time')).to_have_attribute('datetime', next_utc)
    expect(later).to_have_count(0)
    expect(fallback).to_be_visible()
    expect(page.get_by_text('6月12日 · 2 条记录', exact=True)).to_be_visible()
    page.get_by_label('投递渠道').select_option('web')
    expect(first).to_have_count(0)
    expect(fallback).to_be_visible()
    page.get_by_label('投递结果').select_option('failed')
    expect(page.get_by_text('没有符合条件的投递记录', exact=True)).to_be_visible()
    page.get_by_label('投递渠道').select_option('')
    expect(first).to_be_visible()
    page.get_by_label('投递结果').select_option('')
    search = page.get_by_label('搜索记录', exact=True)
    search.fill('共享岗位')
    expect(first).to_be_visible()
    expect(later).to_have_count(0)
    expect(fallback).to_have_count(0)
    first.get_by_role('button', name='查看证据').click()
    details = page.get_by_role('complementary', name='投递详情')
    expect(details).to_contain_text('合成隔日更新')
    expect(details.get_by_text('暂无明确回执', exact=True)).to_be_visible()
    details.get_by_role('button', name='放大操作前截图').click()
    enlarged = page.get_by_role('dialog', name='截图预览')
    expect(enlarged.get_by_role('img', name='操作前截图')).to_be_visible()
    enlarged.get_by_role('button', name='关闭截图预览').click()
    expect(enlarged).to_have_count(0)
    page.screenshot(path=str(folder / 'history-by-date.png'))
    details.get_by_role('tab', name='申请信息', exact=True).click()
    expect(details).to_contain_text('模拟资料')
    expect(details).to_contain_text('https://example.invalid/apply')
    page.get_by_role('button', name='清除日期', exact=True).click()
    expect(later).to_be_visible()
    expect(fallback).to_have_count(0)
    search.fill('')
    page.get_by_label('更新时间排序').select_option('oldest')
    expect(page.locator('.records-page tbody tr').first).to_contain_text('合成旧格式记录')
    export = page.get_by_role('button', name='导出 Excel', exact=True)
    export.click()
    answer_export_dialog(pid)
    expect(export).to_be_enabled(timeout=15000)
    expect(page.get_by_role('status').filter(has_text='记录已导出')).to_have_count(0)
    export.click()
    destination = folder / '历史投递记录.xlsx'
    answer_export_dialog(pid, destination)
    expect(page.get_by_role('status').filter(has_text='记录已导出')).to_be_visible(timeout=15000)
    assert destination.is_file() and not (data / 'results.xlsx').exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'apps/desktop/src-tauri/target/debug/job-search-desktop.exe')
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    if bool(args.profile) != bool(args.resume):
        parser.error('--profile 和 --resume 必须一同提供')

    folder = ROOT / '.verification/live-development' / ('ui-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    profile, resume = args.profile, args.resume
    if profile is None:
        from PIL import Image
        profile, resume = folder / 'synthetic-profile.json', folder / 'synthetic-resume.pdf'
        profile.write_text(json.dumps({'name': '离线测试', 'target_role': '嵌入式软件工程师'}, ensure_ascii=False), encoding='utf-8')
        Image.new('RGB', (72, 72), 'white').convert('P').save(resume)
    companies = folder / 'companies.csv'
    companies.write_text('公司,申请网站\n离线公司,https://example.invalid/apply\n'
                         '离线公司,https://example.invalid/apply\n缺失网址,\n', encoding='utf-8')

    data = folder / 'data'
    db = Store(data)
    try:
        extraction = extract_profile(profile)
        save_profile(db, extraction, extraction['profile'], resume, True)
        profile_role = extraction['profile'].get('target_role') or ''
    finally:
        db.close()

    preferences = {
        '求职城市': '深圳', '省份': '广东', '行政区': '南山区', '商圈': '科技园',
        '岗位方向': '嵌入式驱动工程师', '搜索关键词': '嵌入式',
        '职位分类路径': '电子/电气/通信 > 电子/硬件开发 > 嵌入式软件工程师\n电子/电气/通信 > 电子/硬件开发 > 驱动开发工程师',
        '薪资待遇': '10-20K', '工作经验': '1-3年', '学历要求': '本科',
        '公司行业': '电子/硬件开发', '公司规模': '100-499人', '融资阶段': '已上市',
    }
    env = environment()
    report = {}
    try:
        with sync_playwright() as playwright:
            with desktop_page(playwright, args.app.resolve(), folder, env) as (page, _):
                nav = page.get_by_role('navigation', name='主导航').get_by_role('button').all_inner_texts()
                assert nav == ['总览', '投递任务', '待办', '投递记录'], nav
                open_settings(page, '资料与简历')
                expect(page.get_by_text('已保存资料', exact=True)).to_be_visible(timeout=15000)
                report['profile'] = 'passed'

                open_task(page)
                expect(page.get_by_text('尚未保存岗位条件', exact=True)).to_be_visible(timeout=15000)
                expect(page.get_by_role('button', name='预览本轮范围', exact=True)).to_be_disabled()
                if profile_role:
                    page.get_by_role('radio', name='沿用 BOSS 当前页面', exact=True).check()
                    expect(preview(page)).to_contain_text('沿用已绑定的 BOSS 页面筛选')
                    page.get_by_role('button', name='返回修改', exact=True).click()
                    page.get_by_role('radio', name='使用已保存条件', exact=True).check()
                drawer = edit_preferences(page)
                report['task_creation_requires_preferences'] = 'passed'

                save = drawer.get_by_role('button', name='保存条件', exact=True)
                if profile_role:
                    save.click()
                    expect(drawer.get_by_role('status').filter(has_text='已保存岗位条件。')).to_be_visible(timeout=15000)
                    drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                    expect(preview(page)).to_contain_text(profile_role)
                    page.get_by_role('button', name='返回修改', exact=True).click()
                    drawer = edit_preferences(page)
                    report['missing_saved_role_uses_profile'] = 'passed'
                drawer.get_by_role('button', name='全职', exact=True).click()
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                expect(drawer.get_by_label('行政区', exact=True)).to_be_disabled()
                drawer.get_by_role('button', name='基本条件', exact=True).click()
                basic_fields = ('求职城市', '省份', '岗位方向', '搜索关键词')
                for label in basic_fields:
                    set_field(drawer, label, preferences[label])
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                drawer.get_by_role('button', name='地铁', exact=True).click()
                expect(drawer.get_by_label('站点', exact=True)).to_be_disabled()
                set_field(drawer, '地铁线路', '1号线')
                set_field(drawer, '站点', '高新园')
                drawer.get_by_role('button', name='行政区与商圈', exact=True).click()
                for label, value in preferences.items():
                    if label not in basic_fields:
                        set_field(drawer, label, value)
                save.click()
                expect(drawer.get_by_role('status').filter(has_text='已保存岗位条件。')).to_be_visible(timeout=15000)
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(folder / 'boss-preferences.png'))

            with desktop_page(playwright, args.app.resolve(), folder, env) as (page, pid):
                open_task(page)
                drawer = edit_preferences(page)
                expect(drawer.get_by_label('求职城市', exact=True)).to_have_value('深圳', timeout=15000)
                expect(drawer.get_by_role('button', name='全职', exact=True)).to_have_attribute('aria-pressed', 'true')
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                expect(drawer.get_by_label('行政区', exact=True)).to_have_value('南山区')
                expect(drawer.get_by_label('商圈', exact=True)).to_have_value('科技园')
                expect(drawer.get_by_role('textbox', name='职位分类路径', exact=True)).to_have_value(preferences['职位分类路径'])
                report['preferences_after_restart'] = 'passed'
                drawer.get_by_role('button', name='在 BOSS 核验', exact=True).click()
                expect(drawer.get_by_role('alert')).to_contain_text('请先连接 Chrome 并选择 BOSS 标签页')
                drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                report['filter_verification_requires_bound_tab'] = 'passed'

                expect(page.get_by_role('button', name='编辑岗位条件', exact=True)).to_be_visible()
                for label in ('求职类型', '岗位方向', '沟通城市（可选）', '目标浏览器标题', '平台筛选（请在 BOSS 页面设置）'):
                    expect(page.get_by_label(label, exact=True)).to_have_count(0)
                page.get_by_role('spinbutton', name='本轮沟通上限', exact=True).fill('7')
                for method in ('脚本快速沟通', 'AI 看岗位卡片', 'AI 阅读岗位详情'):
                    page.get_by_role('radio', name=method, exact=True).check()
                    expect(page.get_by_label('筛选条件 / 补充要求', exact=True)).to_have_count(0)
                    dialog = preview(page)
                    for shown in ('全职', '嵌入式驱动工程师', method, '7 个',
                                  '深圳', '南山区', '科技园', '驱动开发工程师', '10-20K', '1-3年', '本科', '100-499人', '已上市'):
                        expect(dialog).to_contain_text(shown)
                    page.get_by_role('button', name='返回修改', exact=True).click()
                dialog = preview(page)
                page.screenshot(path=str(folder / 'boss-preferences-preview.png'))
                dialog.get_by_role('button', name='确认并创建任务', exact=True).click()
                expect(page.get_by_role('button', name='开始 / 恢复', exact=True)).to_be_visible(timeout=15000)
                expect(page.get_by_role('button', name='开始 / 恢复', exact=True)).to_be_disabled()
                with closing(sqlite3.connect(data / 'records.sqlite3')) as conn:
                    [(run_id, status, payload)] = conn.execute('SELECT id, status, config FROM runs').fetchall()
                    assert status == 'paused'
                    config = json.loads(payload)
                    assert config['employment_type'] == 'full_time'
                    assert config['target_roles'] == ['嵌入式驱动工程师']
                    assert config['cities'] == ['深圳'] and config['max_contacts'] == 7
                    assert config['execution_mode'] == 'ai' and config['boss_mode'] == 'screened'
                    assert config['window_title'] == 'BOSS直聘' and not config.get('filters')
                    filters = config['boss_filters']
                    assert filters['city'] == '深圳' and filters['district'] == '南山区'
                    assert filters['business_area'] == '科技园' and filters['salary'] == '10-20K'
                    assert 'metro_line' not in filters and 'metro_station' not in filters
                    assert [path[-1] for path in filters['positions']] == ['嵌入式软件工程师', '驱动开发工程师']
                    assert conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0] == 0
                report['preferences_saved_to_paused_run'] = 'passed'

                page.get_by_role('button', name='结束任务', exact=True).click()
                page.get_by_role('button', name='确认结束', exact=True).click()
                expect(page.get_by_text('已停止', exact=True).first).to_be_visible(timeout=15000)
                page.get_by_role('button', name='编辑任务', exact=True).click()
                page.get_by_role('spinbutton', name='本轮沟通上限', exact=True).fill('9')
                page.get_by_role('radio', name='脚本快速沟通', exact=True).check()
                drawer = edit_preferences(page)
                set_field(drawer, '搜索关键词', '嵌入式驱动')
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                set_field(drawer, '行政区', '宝安区')
                set_field(drawer, '商圈', '')
                drawer.get_by_role('button', name='保存条件', exact=True).click()
                expect(drawer.get_by_role('status')).to_contain_text('已保存到任务草稿')
                drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                dialog = preview(page)
                expect(dialog).to_contain_text('宝安区')
                expect(dialog).to_contain_text('9 个')
                dialog.get_by_role('button', name='确认修改', exact=True).click()
                expect(page.get_by_role('button', name='开始 / 恢复', exact=True)).to_be_visible(timeout=15000)
                with closing(sqlite3.connect(data / 'records.sqlite3')) as conn:
                    [(revised_id, status, payload)] = conn.execute('SELECT id, status, config FROM runs').fetchall()
                    assert (revised_id, status) == (run_id, 'paused')
                    config = json.loads(payload)
                    assert config['max_contacts'] == 9 and config['execution_mode'] == 'script'
                    assert config['boss_filters']['query'] == '嵌入式驱动' and config['boss_filters']['district'] == '宝安区'
                    assert conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0] == 0
                report['stopped_task_revised_in_place'] = 'passed'

                open_task(page)
                expect(page.get_by_role('spinbutton', name='本轮沟通上限', exact=True)).to_have_value('7')
                expect(page.get_by_role('radio', name='AI 阅读岗位详情', exact=True)).to_be_checked()
                drawer = edit_preferences(page)
                set_field(drawer, '搜索关键词', '未保存的新建草稿')
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                set_field(drawer, '商圈', '未保存商圈')
                drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                open_task(page)
                drawer = edit_preferences(page)
                expect(drawer.get_by_label('搜索关键词', exact=True)).to_have_value('未保存的新建草稿')
                drawer.get_by_role('button', name='高级筛选', exact=True).click()
                expect(drawer.get_by_label('商圈', exact=True)).to_have_value('未保存商圈')
                drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                if profile_role:
                    drawer = edit_preferences(page)
                    set_field(drawer, '岗位方向', '')
                    drawer.get_by_role('button', name='保存条件', exact=True).click()
                    expect(drawer.get_by_role('status')).to_contain_text('已保存岗位条件')
                    drawer.get_by_role('button', name='关闭岗位条件编辑', exact=True).click()
                    dialog = preview(page)
                    expect(dialog).to_contain_text(profile_role)
                    if profile_role != '嵌入式驱动工程师':
                        expect(dialog).not_to_contain_text('嵌入式驱动工程师')
                open_page(page, '投递任务')
                expect(page.get_by_text('1 个任务', exact=True)).to_have_count(0)
                report['new_task_reuses_basic_advanced_and_form_drafts'] = 'passed'

                check_record_dates(page, pid, data, folder)
                report['history_by_local_date'] = 'passed'

                open_task(page)
                page.get_by_role('button', name='公司网申', exact=True).click()
                expect(page.get_by_role('button', name='选择公司表')).to_be_visible()
                page.get_by_text('调整网页填写设置', exact=True).click()
                expect(page.get_by_label('网申求职类型', exact=True)).to_have_value('any')
                expect(page.get_by_label('岗位方向', exact=True)).to_have_value(profile_role)
                expect(page.get_by_label('目标浏览器标题', exact=True)).to_have_value('Google Chrome')
                page.get_by_label('公司表路径', exact=True).fill(str(companies))
                page.get_by_role('button', name='读取公司表', exact=True).click()
                expect(page.get_by_text('缺失网址', exact=True)).to_be_visible()
                dialog = preview(page, label='预览可用的 1 家')
                expect(dialog).to_contain_text('离线公司')
                expect(dialog).not_to_contain_text('科技园')
                page.get_by_role('button', name='返回修改', exact=True).click()
                report['web_configuration_is_independent'] = 'passed'

                open_settings(page, '回复规则')
                page.get_by_role('button', name='添加预设话术').click()
                page.get_by_label('回复原文').fill('仅用于配置界面验证，规则保持关闭。')
                page.get_by_label('问题类别').select_option('salary')
                page.get_by_label('启用状态').select_option('true')
                page.get_by_role('button', name='保存回复规则').click()
                expect(page.get_by_role('alert')).to_contain_text('匹配条件', timeout=15000)
                page.get_by_label('可接受的税前月薪下限（元）').fill('5000')
                page.get_by_label('启用状态').select_option('false')
                page.get_by_role('button', name='保存回复规则').click()
                expect(page.get_by_role('status').filter(has_text='已保存。')).to_be_visible(timeout=15000)
                report['rules_and_validation'] = 'passed'

                open_settings(page, '连接与诊断')
                expect(page.get_by_role('button', name='连接 Chrome', exact=True)).to_be_enabled()
                diagnostics = page.get_by_role('region', name='连接与诊断')
                expect(diagnostics.get_by_role('button', name='检查当前页面')).to_be_disabled()
                runtime = page.evaluate("window.__TAURI_INTERNALS__.invoke('browser_runtime_status')")
                assert runtime['installed'] and not runtime['connected']
                probe = json.loads(subprocess.check_output([runtime['executable'], '--job-assistant-probe'], env=env, text=True))
                assert probe['embedded_auth'] and probe['read_only'] and probe['capabilities']['existing_daemon_only']
                report['native_runtime_without_browser_connection'] = 'passed'
        report['native_close'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (folder / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
