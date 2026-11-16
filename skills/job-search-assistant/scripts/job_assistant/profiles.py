"""只在本机提取资料，保留原文定位和不可变版本。"""
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .inputs import digest_file


def extract_profile(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('请选择不超过 20 MB 的资料文件')
    blocks, profile = [], {}
    suffix = path.suffix.lower()
    try:
        if suffix == '.json':
            profile = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(profile, dict):
                raise ValueError('JSON 个人资料必须是对象')
            for key, value in profile.items():
                blocks.append({'text': json.dumps(value, ensure_ascii=False), 'locator': '$.' + key})
        elif suffix == '.docx':
            with zipfile.ZipFile(path) as document:
                entry = document.getinfo('word/document.xml')
                if entry.file_size > 20 * 1024 * 1024:
                    raise ValueError('Word 文本内容过大')
                root = ET.fromstring(document.read(entry))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            for index, paragraph in enumerate(root.findall('.//w:p', ns), 1):
                text = ''.join(node.text or '' for node in paragraph.findall('.//w:t', ns)).strip()
                if text:
                    blocks.append({'text': text, 'locator': f'段落 {index}（含表格单元格）'})
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError('PDF 已加密，请提供解密后的副本')
            if len(reader.pages) > 50:
                raise ValueError('资料最多支持 50 页')
            for index, page in enumerate(reader.pages, 1):
                contents = page.get_contents()
                if contents and len(contents.get_data()) > 20 * 1024 * 1024:
                    raise ValueError('PDF 页面内容过大')
                for line, text in enumerate((page.extract_text() or '').splitlines(), 1):
                    if text.strip():
                        blocks.append({'text': text.strip(), 'locator': f'第 {index} 页，第 {line} 行'})
        else:
            raise ValueError('支持文字 PDF、DOCX 和 JSON；旧 DOC 请先转换为 DOCX')
    except (zipfile.BadZipFile, ET.ParseError, KeyError, UnicodeError) as error:
        raise ValueError('资料文件损坏或内容格式不支持') from error
    except Exception as error:
        if suffix == '.pdf' and not isinstance(error, (ValueError, OSError, ImportError)):
            raise ValueError('PDF 读取失败，请检查文件是否损坏') from error
        raise
    if not blocks:
        raise ValueError('未提取到可读文本；扫描 PDF 需要先转为文字版或提供 DOCX')
    sources = {}
    if suffix != '.json':
        text = '\n'.join(block['text'] for block in blocks)
        profile = {'contact': {}, 'resume_text': text}
        patterns = {
            'contact.email': r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
            'contact.phone': r'(?<!\d)1[3-9]\d{9}(?!\d)',
            'target_role': r'求职意向\s*[:：]\s*([^\n]+)',
            'name': r'(?:姓名\s*[:：]\s*)([\u4e00-\u9fff]{2,6})',
        }
        for field, pattern in patterns.items():
            for block in blocks:
                match = re.search(pattern, block['text'])
                if match:
                    value = (match.group(1) if match.lastindex else match.group()).strip()
                    if field.startswith('contact.'):
                        profile['contact'][field.split('.')[1]] = value
                    else:
                        profile[field] = value
                    sources[field] = {'source_file': str(path), 'locator': block['locator'], 'original': value}
                    break
        if not profile.get('name'):
            first = blocks[0]['text'].strip()
            if re.fullmatch(r'[\u4e00-\u9fff]{2,4}', first) and not first.endswith(('简历', '信息', '经历')):
                profile['name'] = first
                sources['name'] = {'source_file': str(path), 'locator': blocks[0]['locator'], 'original': first}
    else:
        sources = {key: {'source_file': str(path), 'locator': '$.' + key} for key in profile}
    return {'source_file': str(path), 'source_sha256': digest_file(path), 'blocks': blocks,
            'profile': profile, 'sources': sources,
            'warnings': [] if profile.get('name') else ['未识别姓名，请核对后补充；未提供的信息不会自动编造。']}


def save_profile(store, extraction, profile, resume_path, confirmed=False):
    if (confirmed is not True or not isinstance(profile, dict) or not isinstance(profile.get('name'),str)
            or not profile['name'].strip()):
        raise ValueError('请核对资料并填写姓名后保存')
    if 'contact' in profile and not isinstance(profile['contact'],dict):
        raise ValueError('联系方式必须为包含邮箱/电话的对象')
    source = Path(extraction['source_file']).resolve(strict=True)
    if digest_file(source) != extraction['source_sha256']:
        raise ValueError('原资料已变化，请重新导入核对')
    resume = Path(resume_path).expanduser().resolve(strict=True)
    if not resume.is_file() or resume.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('请选择不超过 20 MB 的简历文件')
    version = uuid.uuid4().hex
    folder = store.root / 'profiles'
    folder.mkdir(exist_ok=True)
    data = {**profile, 'sources': dict(extraction['sources'])}
    for key, provenance in list(data['sources'].items()):
        provenance = {**provenance}
        original = extraction['profile'].get(key)
        if '.' in key:
            parent, child = key.split('.', 1)
            original = extraction['profile'].get(parent, {}).get(child)
            current = profile.get(parent, {}).get(child)
        else:
            current = profile.get(key)
        if current != original:
            provenance['edited'] = True
        data['sources'][key] = provenance
    profile_path = folder / (version + '.json')
    profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = {'version_id': version, 'profile_path': str(profile_path), 'profile_sha256': digest_file(profile_path),
                'resume_path': str(resume), 'resume_sha256': digest_file(resume),
                'source_file': str(source), 'source_sha256': extraction['source_sha256'], 'profile': data}
    temporary = folder / (version + '.current')
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'current.json')
    return metadata


def get_profile(store):
    pointer = store.root / 'profiles/current.json'
    if not pointer.is_file():
        return None
    value = json.loads(pointer.read_text(encoding='utf-8'))
    path = Path(value['profile_path']).resolve()
    if path.parent != store.root / 'profiles' or digest_file(path) != value['profile_sha256']:
        raise ValueError('已保存资料版本丢失或变更，请重新导入')
    return value
