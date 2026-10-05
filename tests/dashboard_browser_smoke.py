"""离线浏览器端到端验收；需开发机已有 Playwright 和 Chrome，不属于运行依赖。"""
import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/job-search-assistant/scripts'))
from job_assistant.cli import dispatch, start
from job_assistant.dashboard import make_server
from job_assistant.store import Store
from playwright.sync_api import sync_playwright, expect


def main():
    folder = ROOT / '.verification' / ('dashboard-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    profile, resume = folder / 'profile.json', folder / 'resume.pdf'
    profile.write_text('{"name":"演示用户"}', encoding='utf-8')
    resume.write_bytes(b'local-fixture')
    db = Store(folder)
    config = {'channels': ['boss', 'web'], 'boss_mode': 'screened', 'max_contacts': 20,
              'target_roles': ['嵌入式软件工程师'], 'profile_path': str(profile), 'resume_path': str(resume),
              'unknown_reply_mode': 'human', 'company_table': str(folder / 'companies.csv')}
    (folder / 'companies.csv').write_text('公司名称,申请网站\n演示科技,https://example.com/careers\n', encoding='utf-8')
    run = start(db, {'config': config, 'approval': '仅本地测试'})['run_id']
    first = db.begin_attempt(run, '演示科技', 'https://example.com/careers', '嵌入式软件工程师', 'web', 'fixture-web')
    db.record_field(first, '姓名', '演示用户', 'profile.name')
    second = db.begin_attempt(run, '模拟智能', 'https://example.com/job/2', '固件工程师', 'boss', 'fixture-boss')
    db.set_attempt(second, 'uncertain', '仅用于演示结果待核实状态')
    db.observe_message('演示科技 · 招聘经理', 'm1', '周五下午方便来面试吗？薪资范围可以接受吗？')
    db.handoff('演示科技 · 招聘经理', '面试邀约，整条消息转人工')
    server = make_server(folder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/#token={server.token}'
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
            tab = context.new_page()
            tab.on('pageerror', lambda error: errors.append(str(error)))
            tab.goto((ROOT / 'tests/fixtures/sandbox.html').as_uri())
            frame = folder / 'frames' / (uuid.uuid4().hex + '.png')
            frame.parent.mkdir()
            tab.screenshot(path=str(frame))
            frame.with_suffix('.json').write_text(json.dumps({'window': {'title': '离线模拟页面 · 无真实投递'}, 'captured_at': time.time()}), encoding='utf-8')
            evidence = db.evidence(first, frame, 'pre_submit', ['姓名'])
            action = db.prepare_action(run, first, 'submit', '演示科技', {'pre_submit_evidence': evidence})
            receipt = db.evidence(first, frame, 'result')
            db.finish_action(action, 'succeeded', receipt, '仅模拟回执')
            dispatch(db, {'operation': 'tick', 'run_id': run})
            tab.goto(base)
            expect(tab.locator('#connection')).to_contain_text('已连接')
            expect(tab.locator('#stat-total')).to_have_text('2')
            expect(tab.locator('#frame')).to_be_visible()
            tab.screenshot(path=str(folder / 'overview.png'), full_page=True)
            tab.get_by_role('button', name='Ⅱ 暂停', exact=True).click()
            expect(tab.locator('#run-status')).to_contain_text('已暂停')
            with db.conn:
                assert db.one('runs', run)['status'] == 'paused'
            tab.get_by_role('button', name='继续', exact=True).click()
            expect(tab.locator('#command-state')).to_contain_text('等待 Codex')
            assert db.one('runs', run)['status'] == 'paused'
            dispatch(db, {'operation': 'tick', 'run_id': run})
            expect(tab.locator('#run-status')).to_contain_text('允许执行', timeout=6000)
            tab.locator('[data-page="records"]').click()
            expect(tab.locator('#record-rows tr')).to_have_count(2)
            tab.locator('#search').fill('演示科技')
            expect(tab.locator('#record-rows tr')).to_have_count(1)
            tab.get_by_role('button', name='查看详情 ↗').click()
            expect(tab.locator('#detail-content')).to_contain_text('profile.name')
            tab.get_by_role('button', name='查看截图 · pre_submit').click()
            expect(tab.locator('#detail-content img')).to_have_count(1)
            tab.locator('#detail-dialog .close-dialog').click()
            tab.locator('[data-page="attention"]').click()
            tab.get_by_label('处理意见').fill('我先核对面试时间，不发送。')
            tab.get_by_role('button', name='保存意见，不发送').click()
            expect(tab.locator('.attention-notes')).to_contain_text('不发送')
            assert db.one('conversations', '演示科技 · 招聘经理')['status'] == 'human'
            delayed = []
            tab.route('**/api/attention', lambda route: delayed.append(route))
            tab.get_by_label('处理意见').fill('先保存这一段')
            tab.get_by_role('button', name='保存意见，不发送').click()
            tab.wait_for_timeout(200)
            assert delayed, '保存请求未发出'
            tab.get_by_label('处理意见').fill('保存过程中继续输入的新草稿')
            delayed[0].continue_()
            expect(tab.locator('.attention-notes').last).to_contain_text('先保存这一段')
            expect(tab.get_by_label('处理意见')).to_have_value('保存过程中继续输入的新草稿')
            tab.unroute('**/api/attention')
            tab.screenshot(path=str(folder / 'attention.png'), full_page=True)
            tab.locator('[data-page="setup"]').click()
            tab.locator('#target-roles').fill('固件工程师')
            tab.locator('#resume-path').fill(str(resume))
            tab.locator('#profile-path').fill(str(profile))
            tab.get_by_role('button', name='复核本轮配置 →').click()
            expect(tab.locator('#config-dialog')).to_be_visible()
            expect(tab.locator('#start-run')).to_be_disabled()
            tab.locator('#approve-config').check()
            tab.locator('#start-run').click()
            expect(tab.locator('#notice')).to_contain_text('本轮已授权')
            expect(tab.locator('#executor-title')).to_have_text('等待 Codex 会话')
            with tab.expect_download() as info:
                tab.locator('#export').click()
            info.value.save_as(str(folder / 'download.xlsx'))
            assert (folder / 'download.xlsx').read_bytes().startswith(b'PK')
            tab.set_viewport_size({'width': 390, 'height': 844})
            tab.screenshot(path=str(folder / 'mobile.png'), full_page=True)
            assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth'), '移动布局横向溢出'
            assert not errors, errors
            browser.close()
        print(json.dumps({'passed': True, 'screenshots': str(folder), 'browser_errors': errors}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        db.close()


if __name__ == '__main__':
    main()
