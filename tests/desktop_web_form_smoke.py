"""在隔离 Chrome 中用合成页面验证官网简历控件识别和草稿填写。"""
import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright

from desktop_chrome_setup_smoke import ROOT, isolated_chrome


ALIBABA = 'https://campus-talent.alibaba.com/personal/resume'
BAIC = 'https://baicgroup.zhiye.com/form?fromPage=editMyResume'
FIXTURES = {
    ALIBABA: '''
        <section class="FormCard--formCard--test">
          <div class="FormCard--formCardTitle--test">基本信息</div>
          <div class="next-form-item">
            <span class="next-form-item-label">手机</span>
            <div class="next-select cpo-phone-select-inner" role="combobox">
              <input id="dial-code" aria-label="区号" value="+86">
            </div>
            <div class="cpo-phone-select-number">
              <input id="phone" aria-label="号码选择器" type="tel">
            </div>
          </div>
        </section>
        <section class="FormCard--formCard--other">
          <div class="FormCard--formCardTitle--other">其它信息</div>
          <div class="next-form-item">
            <span class="next-form-item-label">其它</span>
            <textarea id="otherInformation" placeholder="请输入自我评价或其它补充信息"></textarea>
          </div>
        </section>
    ''',
    BAIC: '''
        <div>教育经历</div>
        <section><div>
          <form class="ux-standard-form">
            <div class="form-item">
              <span class="form-item__text">学历</span>
              <div class="phoenix-select"><input id="degree"></div>
            </div>
            <div class="form-item">
              <span class="form-item__text">专业名称</span>
              <input id="major">
            </div>
            <div aria-disabled="true">
              <label for="disabled">姓名</label><input id="disabled">
            </div>
            <label for="readonly">英文姓名</label>
            <input id="readonly" aria-readonly="true">
          </form>
        </div></section>
        <div>简历附件</div>
        <section><form class="ux-standard-form">
          <div class="form-item">
            <span class="form-item__text">简历附件</span>
            <div class="file-uploader__wrapper">
              <button type="button">上传简历</button>
              <input id="resume" type="file" accept=".pdf" style="display:none">
              <div class="file-uploader__list-view"></div>
            </div>
          </div>
        </form></section>
    ''',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', type=Path,
                        default=Path(os.environ['ProgramFiles']) / 'Google/Chrome/Application/chrome.exe')
    args = parser.parse_args()
    script = (ROOT / 'apps/desktop/src-tauri/src/web_form.js').read_text(encoding='utf-8')

    with TemporaryDirectory() as tmp, sync_playwright() as playwright:
        with isolated_chrome(playwright, args.chrome.resolve(strict=True), Path(tmp) / 'chrome') as (tab, _, _):
            def fixture(route):
                body = FIXTURES.get(route.request.url)
                if body is None:
                    route.abort()
                    return
                route.fulfill(content_type='text/html; charset=utf-8', body=(
                    '<meta charset=utf-8>' + body +
                    '<script>window.submits=0;document.addEventListener("submit",'
                    'event=>{event.preventDefault();window.submits++})</script>'
                ))

            tab.route('**/*', fixture)

            def evaluate(url, **payload):
                return tab.evaluate(script.replace('__JOB_FORM_ARGS__', json.dumps({'url': url, **payload})))

            tab.goto(ALIBABA)
            fields = evaluate(ALIBABA)['fields']
            assert [(field['label'], field['section'], field['type']) for field in fields] == [
                ('手机', '基本信息', 'tel'),
                ('自我评价或其它补充信息', '其它信息', 'textarea'),
            ], fields
            operation = {'id': fields[0]['id'], 'value': '13900000000'}
            result = evaluate(ALIBABA, fields=fields, operations=[operation])
            assert result == {'filled': [fields[0]['id']], 'skipped': []}, result
            expect(tab.locator('#phone')).to_have_value('13900000000')
            expect(tab.locator('#dial-code')).to_have_value('+86')
            assert tab.evaluate('window.submits') == 0

            tab.goto(BAIC)
            fields = evaluate(BAIC)['fields']
            assert [(field['label'], field['section'], field['type']) for field in fields] == [
                ('学历', '教育经历', 'custom'),
                ('专业名称', '教育经历', 'text'),
                ('英文姓名', '教育经历', 'custom'),
                ('简历附件', '简历附件', 'file'),
            ], fields
            assert fields[0]['row'] == fields[1]['row'] != fields[3]['row']
            assert fields[3]['accept'] == '.pdf' and not fields[3]['occupied']
            operation = {'id': fields[1]['id'], 'value': '合成专业'}
            result = evaluate(BAIC, fields=fields, operations=[operation])
            assert result == {'filled': [fields[1]['id']], 'skipped': []}, result
            expect(tab.locator('#major')).to_have_value('合成专业')
            expect(tab.locator('#degree')).to_have_value('')
            expect(tab.locator('#disabled')).to_have_value('')
            expect(tab.locator('#readonly')).to_have_value('')
            tab.locator('.file-uploader__list-view').evaluate(
                'element => element.appendChild(document.createElement("span"))')
            assert evaluate(BAIC)['fields'][3]['occupied']
            assert not evaluate(BAIC, fields=fields, fileId=fields[3]['id'])
            assert tab.evaluate('window.submits') == 0

    print('Alibaba and BAIC offline form regression passed.', flush=True)


if __name__ == '__main__':
    main()
