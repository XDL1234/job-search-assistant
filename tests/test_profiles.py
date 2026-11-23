"""真实格式提取、材料指纹、版本与个人数据隔离。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'apps/worker/scripts'))
from job_assistant.store import Store
from job_assistant.app_service import handle_app_request


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data')
        self.addCleanup(self.store.close)

    def call(self,method,**params):
        return handle_app_request(self.store,{'id':'test','method':method,'params':params})

    def test_concurrent_editors_only_one_can_publish_same_profile_version(self):
        expected = ''
        folder = self.store.root / 'profiles'
        for phase in ('首次创建', '再次编辑'):
            with self.subTest(phase=phase):
                gate = Barrier(2)
                def save(name):
                    with closing(Store(self.store.root)) as store:
                        gate.wait(timeout=5)
                        return handle_app_request(store, {'id': name, 'method': 'save_personal_info', 'params': {
                            'profile': {'name': name}, 'expected_version': expected}})
                with ThreadPoolExecutor(2) as workers:
                    outcomes = list(workers.map(save, ['甲' + phase, '乙' + phase]))
                winner, = (reply for reply in outcomes if reply['ok'])
                rejected, = (reply for reply in outcomes if not reply['ok'])
                self.assertEqual(rejected['error']['code'], 'profile_version_conflict')
                self.assertEqual(self.call('get_profile')['result'], winner['result'])
                self.assertEqual(winner['result']['profile']['name'], winner['id'])
                expected = winner['result']['version_id']
        self.assertEqual(len(list(folder.glob('*.json'))), 3)  # 两次成功的不可变版本和一个指针。

    def test_import_with_stale_version_preserves_newer_edits(self):
        source = self.root / 'profile.json'
        source.write_text('{"name":"A"}', encoding='utf-8')
        extraction = self.call('extract_profile', path=str(source))['result']
        previous = self.call('save_personal_info', profile={'name': '甲'}, expected_version='')['result']
        updated = self.call('save_personal_info', profile={'name': '乙'}, expected_version=previous['version_id'])['result']
        for method in ('save_personal_info', 'save_profile'):
            with self.subTest(method=method):
                args = {'resume_path': str(source), 'confirmed': True} if method == 'save_profile' else {}
                response = self.call(method, profile=extraction['profile'], extraction=extraction,
                                     expected_version=previous['version_id'], **args)
                self.assertEqual(response['error']['code'], 'profile_version_conflict')
                self.assertEqual(self.call('get_profile')['result'], updated)

    def test_docx_paragraphs_and_tables_have_sources_without_duplicates(self):
        path=self.root/'含 空格 中文.docx'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
            <w:p><w:r><w:t>测试同学</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>邮箱：student@example.com</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            <w:p><w:r><w:t>求职意向：嵌入式软件实习生</w:t></w:r></w:p></w:body></w:document>''')
        result=self.call('extract_profile',path=str(path))
        self.assertTrue(result['ok'],result)
        value=result['result']
        self.assertEqual(len(value['blocks']),3)
        self.assertEqual(value['profile']['contact']['email'],'student@example.com')
        self.assertEqual(value['profile']['target_role'],'嵌入式软件实习生')
        self.assertIn('段落',value['blocks'][1]['locator'])

    def test_json_roundtrip_versions_are_immutable(self):
        source=self.root/'个人资料.json'
        source.write_text(json.dumps({'name':'测试同学','projects':['STM32'],'contact':{'email':'demo@example.com'}}))
        extraction=self.call('extract_profile',path=str(source))['result']
        for confirmed in (False,True):
            saved=self.call('save_profile',extraction=extraction,profile=extraction['profile'],resume_path=str(source),confirmed=confirmed)
            self.assertEqual(saved['ok'],confirmed,saved)
        first=Path(saved['result']['profile_path'])
        original=first.read_bytes()
        changed={**extraction['profile'],'name':'另一个测试值'}
        self.assertTrue(self.call('save_profile',extraction=extraction,profile=changed,resume_path=str(source),confirmed=True)['ok'])
        self.assertEqual(first.read_bytes(),original)
        self.assertEqual(self.call('get_profile')['result']['profile']['name'],'另一个测试值')

    def test_material_change_rejected_and_current_profile_kept(self):
        source=self.root/'个人资料.json';source.write_text('{"name":"A"}')
        extraction=self.call('extract_profile',path=str(source))['result']
        source.write_text('{"name":"B"}')
        self.assertFalse(self.call('save_profile',extraction=extraction,profile=extraction['profile'],resume_path=str(source),confirmed=True)['ok'])
        self.assertIsNone(self.call('get_profile')['result'])

    def test_optional_personal_info_without_source_or_resume(self):
        saved = self.call('save_personal_info', profile={
            'name': '   ', 'id_number': ' example-id ', 'contact': {'email': '', 'phone': '  '},
            'education': [{}, {'school': ' 示例学校 ', 'major': ''}], 'projects': ['已有项目'],
        })
        self.assertTrue(saved['ok'], saved)
        result = saved['result']
        self.assertEqual(result['source_file'], '')
        self.assertEqual(result['resume_path'], '')
        self.assertEqual(result['profile'], {'id_number': 'example-id', 'education': [{'school': '示例学校'}],
                                                  'projects': ['已有项目'], 'sources': {}})
        self.assertEqual(json.loads(Path(result['profile_path']).read_text(encoding='utf-8')), result['profile'])

    def test_resume_copy_and_personal_information_update_independently(self):
        docx = self.root / '示例 简历.docx'
        with zipfile.ZipFile(docx, 'w') as document:
            document.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>合成简历文本</w:t></w:r></w:p></w:document>')
        imported = self.call('import_resume', path=str(docx))
        self.assertTrue(imported['ok'], imported)
        attachment = imported['result']
        saved_file = Path(attachment['path'])
        only_resume = self.call('get_profile')['result']
        self.assertEqual(only_resume['profile_path'], '')
        self.assertEqual(only_resume['resume_path'], str(saved_file))
        self.assertEqual(saved_file.name, docx.name)
        self.assertEqual(saved_file.parent.parent, self.store.root / 'resumes')
        docx.write_bytes(b'invalid new contents')
        self.assertFalse(self.call('import_resume', path=str(docx))['ok'])
        self.assertEqual(self.call('get_resume')['result'], attachment)
        self.assertEqual(self.call('preview_resume')['result']['text'], '合成简历文本')
        personal = self.call('save_personal_info', profile={'name': '虚构姓名'})['result']
        self.assertEqual(personal['resume_path'], str(saved_file))
        with patch('job_assistant.profiles.os.startfile', create=True) as launch:
            if os.name == 'nt':
                self.assertTrue(self.call('open_resume')['ok'])
            self.assertFalse(self.call('open_resume', path=str(docx))['ok'])
            saved_file.write_bytes(b'tampered')
            self.assertFalse(self.call('preview_resume')['ok'])
            self.assertFalse(self.call('open_resume')['ok'])
            if os.name == 'nt':
                launch.assert_called_once_with(saved_file)
            else:
                launch.assert_not_called()

    def test_legacy_attachment_is_kept_when_saving_personal_information(self):
        source = self.root / '旧资料.json'
        source.write_text('{"name":"Previous","projects":["Original entry"]}', encoding='utf-8')
        extracted = self.call('extract_profile', path=str(source))['result']
        old = self.call('save_profile', extraction=extracted, profile=extracted['profile'],
                        resume_path=str(source), confirmed=True)['result']
        personal = self.call('save_personal_info', profile={**old['profile'], 'name': ''})['result']
        self.assertEqual(personal['resume_path'], old['resume_path'])
        self.assertEqual(personal['resume_sha256'], old['resume_sha256'])
        self.assertEqual(personal['profile']['projects'], ['Original entry'])
        self.assertNotIn('name', personal['profile'])
        self.assertEqual(json.loads(Path(old['profile_path']).read_text(encoding='utf-8'))['name'], 'Previous')

    def test_extract_imported_resume_as_draft_and_preserve_existing_sources(self):
        source = self.root / '已有资料.json'
        source.write_text('{"name":"先前保存的人名"}', encoding='utf-8')
        previous = self.call('extract_profile', path=str(source))['result']
        self.call('save_personal_info', profile=previous['profile'], extraction=previous)
        self.assertFalse(self.call('extract_resume_profile')['ok'])
        docx = self.root / '合成简历.docx'
        with zipfile.ZipFile(docx, 'w') as document:
            document.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
            <w:p><w:r><w:t>邮箱：resume@example.invalid</w:t><w:br/><w:t>项目经历</w:t></w:r></w:p>
            <w:p><w:r><w:t>｜</w:t></w:r></w:p>
            <w:p><w:r><w:t>测试项目</w:t><w:tab/><w:t>2024.06 — 2025.01</w:t></w:r></w:p>
            <w:p><w:r><w:t>项目描述：编写测试固件。身份证号码：00000020000101000X</w:t></w:r></w:p>
            </w:body></w:document>''')
        attachment = self.call('import_resume', path=str(docx))['result']
        response = self.call('extract_resume_profile')
        self.assertTrue(response['ok'], response)
        extraction = response['result']
        self.assertEqual(extraction['source_file'], attachment['path'])
        self.assertEqual(extraction['source_sha256'], attachment['sha256'])
        self.assertEqual(extraction['profile']['projects'][0]['name'], '测试项目')
        self.assertEqual(extraction['profile']['projects'][0]['start_date'], '2024-06')
        self.assertEqual(extraction['profile']['contact']['email'], 'resume@example.invalid')
        self.assertEqual(extraction['profile']['id_number'], '00000020000101000X')
        self.assertNotIn('00000020000101000X', extraction['profile']['projects'][0]['description'])
        self.assertNotIn('00000020000101000X', extraction['sources']['projects']['original'])
        self.assertNotIn('projects', self.call('get_profile')['result']['profile'])
        self.assertFalse(self.call('extract_resume_profile', path=str(docx))['ok'])
        merged = self.call('save_personal_info', extraction=extraction, profile={
            'name': '先前保存的人名', 'contact': extraction['profile']['contact'],
            'projects': extraction['profile']['projects'],
        })['result']['profile']
        self.assertEqual(merged['sources']['name']['source_file'], str(source))
        self.assertEqual(merged['sources']['projects']['source_file'], attachment['path'])
        Path(attachment['path']).write_bytes(b'tampered')
        self.assertFalse(self.call('extract_resume_profile')['ok'])

    def test_blank_pdf_and_old_doc_have_explicit_failures(self):
        from pypdf import PdfWriter
        path=self.root/'扫描.pdf'
        writer=PdfWriter();writer.add_blank_page(width=200,height=200)
        writer.write(path)
        result=self.call('extract_profile',path=str(path))
        self.assertFalse(result['ok'],result)
        self.assertIn('文字',result['error']['message'])
        path=self.root/'旧.doc';path.write_bytes(b'old')
        self.assertFalse(self.call('extract_profile',path=str(path))['ok'])

    def test_pdf_text_source_page_and_corrupted_file(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
        writer=PdfWriter();page=writer.add_blank_page(width=400,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 100 Td (Email: pdf@example.com) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        path=self.root/'文字.pdf';writer.write(path)
        with patch('job_assistant.resume_ocr.ocr_pdf_pages') as ocr:
            result=self.call('extract_profile',path=str(path))
        ocr.assert_not_called()
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['profile']['contact']['email'],'pdf@example.com')
        self.assertIn('第 1 页',result['result']['blocks'][0]['locator'])
        path.write_bytes(b'not a pdf')
        self.assertFalse(self.call('extract_profile',path=str(path))['ok'])

    def test_mixed_pdf_ocr_keeps_page_order_and_surfaces_missing_pages(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject

        writer = PdfWriter()
        for _ in range(4):
            page = writer.add_blank_page(width=300, height=300)
            stream = DecodedStreamObject()
            stream.set_data(b'q 50 50 55 10 re f Q')
            page[NameObject('/Contents')] = writer._add_object(stream)
        path = self.root / '混合简历.pdf'
        writer.write(path)
        self.assertTrue(self.call('import_resume', path=str(path))['ok'])
        text_pages = ['', '实习经历\n单位名称：示例单位', '', '实习内容：编写采集程序']
        with patch('pypdf._page.PageObject.extract_text', side_effect=text_pages * 2), patch(
            'job_assistant.resume_ocr.ocr_pdf_pages', return_value={1: ['姓名：陈示例']}
        ) as ocr:
            result = self.call('extract_resume_profile')['result']
            preview = self.call('preview_resume')['result']
        self.assertEqual(ocr.call_args.args[1], [1, 3])
        self.assertEqual(result['profile']['name'], '陈示例')
        self.assertEqual(result['profile']['internships'][0]['description'], '编写采集程序')
        self.assertEqual(result['ocr_pages'], [1])
        self.assertEqual(preview['ocr_pages'], [1])
        self.assertIn('部分', preview['message'])
        self.assertTrue(preview['text'].startswith('姓名：陈示例'))


if __name__=='__main__':unittest.main()
