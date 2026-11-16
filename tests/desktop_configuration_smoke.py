"""实际 Tauri 配置页面回归；路径由调用者提供，不创建投递任务。"""
import sys,os,time,json,socket,uuid,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'skills/job-search-assistant/scripts'))
from desktop_dev import environment
from desktop_app_smoke import close_window
from job_assistant.profiles import extract_profile,save_profile
from job_assistant.store import Store
from playwright.sync_api import sync_playwright,expect
folder=ROOT/'.verification/live-development'/('ui-'+uuid.uuid4().hex[:8]);folder.mkdir()
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--profile',type=Path,required=True)
parser.add_argument('--resume',type=Path,required=True)
args=parser.parse_args()
db=Store(folder/'data');extraction=extract_profile(args.profile)
save_profile(db,extraction,extraction['profile'],args.resume,True);db.close()
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env=environment();env['JOB_ASSISTANT_DATA_DIR']=str(folder/'data')
env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']=f'--remote-debugging-port={port}'
env['WEBVIEW2_USER_DATA_FOLDER']=str(folder/'webview')
process=subprocess.Popen([str(ROOT/'apps/desktop/src-tauri/target/debug/job-search-desktop.exe')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(json.dumps({'folder':str(folder),'pid':process.pid},ensure_ascii=False),flush=True)
report={}
try:
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(90):
  try:opener.open(f'http://127.0.0.1:{port}/json/version',timeout=1).close();break
  except OSError:time.sleep(.5)
 with sync_playwright() as p:
  browser=p.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
  page=browser.contexts[0].pages[0]
  expect(page.get_by_role('heading',name='今天，开始新的机会')).to_be_visible(timeout=30000)
  nav=page.get_by_role('navigation',name='主导航')
  nav.get_by_role('button',name='我的资料',exact=True).click()
  expect(page.get_by_text('已保存资料',exact=True)).to_be_visible(timeout=15000)
  page.screenshot(path=str(folder/'profile.png'))
  report['real_resume_profile']='passed'
  nav.get_by_role('button',name='BOSS 投递',exact=True).click()
  expect(page.get_by_text('资料就绪',exact=True)).to_be_visible(timeout=15000)
  for employment in ['internship','campus','full_time']:
   page.get_by_label('求职类型').select_option(employment)
   for mode in ['script','filtered','screened']:
    page.get_by_label('投递模式').select_option(mode)
    page.get_by_role('button',name='预览本轮任务',exact=True).click()
    expect(page.get_by_role('dialog',name='确认投递范围')).to_be_visible(timeout=15000)
    page.get_by_role('button',name='返回修改',exact=True).click()
  page.screenshot(path=str(folder/'boss-modes.png'))
  report['nine_mode_previews']='passed'
  nav.get_by_role('button',name='公司网申',exact=True).click()
  expect(page.get_by_role('button',name='选择公司表')).to_be_visible()
  page.screenshot(path=str(folder/'companies.png'))
  report['web_application_retained']='passed'
  nav.get_by_role('button',name='自动回复',exact=True).click()
  page.get_by_role('button',name='添加预设话术').click()
  page.get_by_label('回复原文').fill('仅用于配置界面验证，规则保持关闭。')
  page.get_by_label('问题类别').select_option('salary')
  page.get_by_label('启用状态').select_option('true')
  page.get_by_role('button',name='保存回复规则').click()
  expect(page.get_by_role('alert')).to_contain_text('匹配条件',timeout=15000)
  page.get_by_label('可接受的税前月薪下限（元）').fill('5000')
  page.get_by_label('启用状态').select_option('false')
  page.get_by_role('button',name='保存回复规则').click()
  expect(page.get_by_role('status')).to_contain_text('已保存',timeout=15000)
  report['rules_and_validation']='passed'
  page.get_by_role('button',name='设置与帮助',exact=True).click()
  page.get_by_role('button',name='启动浏览器连接器',exact=True).click()
  code=page.get_by_role('textbox',name='浏览器连接码')
  expect(code).to_be_visible(timeout=15000)
  parts=code.input_value().split(':');assert len(parts)==2 and len(parts[1])>=32
  report['native_bridge_start']='passed'
 close_window(process.pid);process.wait(timeout=60);assert process.returncode==0
 report['native_close']='passed'
except Exception as e:
 report['error']=str(e);raise
finally:
 if process.poll() is None:close_window(process.pid)
 (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False),flush=True)
