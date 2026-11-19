"""只在本机提取资料，保留原文定位和不可变版本。"""
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .inputs import digest_file
from .policy import value_at

MAX_FILE_SIZE = 20 * 1024 * 1024
ID_NUMBER = r'(?<![0-9])[0-9]{17}[0-9Xx](?![a-zA-Z0-9])'


def _pdf_blocks(path):
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError('PDF 已加密，请提供解密后的副本')
    if len(reader.pages) > 50:
        raise ValueError('资料最多支持 50 页')

    by_page, scanned = {}, []
    for index, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 20 * 1024 * 1024:
            raise ValueError('PDF 页面内容过大')
        lines = (page.extract_text() or '').splitlines()
        by_page[index] = [{'text': text.strip(), 'locator': f'第 {index} 页，第 {line} 行'}
                          for line, text in enumerate(lines, 1) if text.strip()]
        if contents is not None and not by_page[index]:
            scanned.append(index)

    recognized = {}
    warning = ''
    if scanned:
        try:
            from .resume_ocr import ocr_pdf_pages
            recognized = ocr_pdf_pages(path, scanned)
        except (ImportError, OSError, RuntimeError, ValueError):
            warning = '本机 OCR 暂时不可用，可打开原件或手动填写。'
    ocr_pages = []
    for index in scanned:
        lines = recognized.get(index, ())
        if lines:
            by_page[index] = [{'text': text, 'locator': f'第 {index} 页，OCR 第 {line} 行'}
                              for line, text in enumerate(lines, 1)]
            ocr_pages.append(index)
    blocks = [block for page in by_page.values() for block in page]
    if not blocks:
        raise ValueError(warning or '未提取到清晰的文字，请检查扫描件或打开原件后手动填写')
    if scanned and len(ocr_pages) != len(scanned) and not warning:
        warning = '部分没有文字层的页面未识别到文字，请核对原件。'
    return blocks, ocr_pages, warning


def extract_profile(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError('请选择不超过 20 MB 的资料文件')
    blocks, profile = [], {}
    ocr_pages, ocr_warning = [], ''
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
            namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            separators = {namespace + 'tab': '\t', namespace + 'br': '\n', namespace + 'cr': '\n'}
            for index, paragraph in enumerate(root.iter(namespace + 'p'), 1):
                text = ''.join((node.text or '') if node.tag == namespace + 't' else separators.get(node.tag, '')
                               for node in paragraph.iter())
                for line in text.splitlines():
                    if line.strip():
                        blocks.append({'text': line.strip(), 'locator': f'段落 {index}（含表格单元格）'})
        elif suffix == '.pdf':
            blocks, ocr_pages, ocr_warning = _pdf_blocks(path)
        else:
            raise ValueError('支持 PDF、DOCX 和 JSON；旧 DOC 请先转换为 DOCX')
    except (zipfile.BadZipFile, ET.ParseError, KeyError, UnicodeError) as error:
        raise ValueError('资料文件损坏或内容格式不支持') from error
    except Exception as error:
        if suffix == '.pdf' and not isinstance(error, (ValueError, OSError, ImportError)):
            raise ValueError('PDF 读取失败，请检查文件是否损坏') from error
        raise
    if not blocks:
        raise ValueError('未提取到可读文本')
    sources = {}
    if suffix != '.json':
        from .resume_parser import parse_resume

        text = '\n'.join(block['text'] for block in blocks)
        profile = {'contact': {}, 'resume_text': text}
        scrubbed = [{**block, 'text': re.sub(ID_NUMBER, '〔证件号已省略〕', block['text'])} for block in blocks]
        patterns = {
            'contact.email': r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
            'contact.phone': r'(?<!\d)1[3-9]\d{9}(?!\d)',
            'target_role': r'(?:求职意向|求职方向)\s*[:：]\s*([^\t|｜]+?)(?=\s{2,}|[\t|｜]|$)',
            'name': r'(?:姓名\s*[:：]\s*)([\u4e00-\u9fff]{2,6})',
            'id_number': rf'身份证号(?:码)?\s*[:：]\s*({ID_NUMBER})',
            'country': r'(?:当前|现)所在国家\s*[:：]\s*([^\t|｜]+?)(?=\s{2,}|[\t|｜]|$)',
            'province': r'(?:当前|现)所在省份\s*[:：]\s*([^\t|｜]+?)(?=\s{2,}|[\t|｜]|$)',
            'city': r'(?:当前|现)所在城市\s*[:：]\s*([^\t|｜]+?)(?=\s{2,}|[\t|｜]|$)',
            'graduation_date': r'(?:预计毕业时间|毕业时间)\s*[:：]\s*((?:19|20)\d{2}(?:[年./-]\s*(?:1[0-2]|0?[1-9])(?!\d)月?)?)',
        }
        for field, pattern in patterns.items():
            for block in blocks if field == 'id_number' else scrubbed:
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
        structured, structured_sources = parse_resume(scrubbed, str(path))
        profile.update(structured)
        sources.update(structured_sources)
    else:
        sources = {key: {'source_file': str(path), 'locator': '$.' + key} for key in profile}
    warnings = [ocr_warning] if ocr_warning else []
    if not profile.get('name'):
        warnings.append('未识别姓名，请核对后补充；未提供的信息不会自动编造。')
    return {'source_file': str(path), 'source_sha256': digest_file(path), 'blocks': blocks,
            'profile': profile, 'sources': sources, 'ocr_pages': ocr_pages,
            'ocr_warning': ocr_warning, 'warnings': warnings}


def save_profile(store, extraction, profile, resume_path, confirmed=False):
    if (confirmed is not True or not isinstance(profile, dict) or not isinstance(profile.get('name'),str)
            or not profile['name'].strip()):
        raise ValueError('请核对资料并填写姓名后保存')
    if 'contact' in profile and not isinstance(profile['contact'],dict):
        raise ValueError('联系方式必须为包含邮箱/电话的对象')
    resume = Path(resume_path).expanduser().resolve(strict=True)
    if not resume.is_file() or resume.stat().st_size > MAX_FILE_SIZE:
        raise ValueError('请选择不超过 20 MB 的简历文件')
    return _save_profile_version(store, profile, extraction, {
        'resume_path': str(resume), 'resume_sha256': digest_file(resume),
    })


def _without_blank(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {key: cleaned for key, item in value.items()
                if (cleaned := _without_blank(item)) not in (None, '', {}, [])}
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _without_blank(item)) not in (None, '', {}, [])]
    return value


def save_personal_info(store, profile, extraction=None):
    if not isinstance(profile, dict) or ('contact' in profile and not isinstance(profile['contact'], dict)):
        raise ValueError('个人资料必须是对象，联系方式需包含邮箱或电话字段')
    previous = _read_profile_version(store) or {}
    return _save_profile_version(store, _without_blank(profile), extraction, previous)


def _field(profile, key):
    try:
        return value_at(profile, key)
    except KeyError:
        return None


def _save_profile_version(store, profile, extraction, previous):
    source_file = previous.get('source_file', '')
    source_sha256 = previous.get('source_sha256', '')
    original = previous.get('profile', {})
    sources = original.get('sources', {})
    imported = {}
    if extraction is not None:
        source = Path(extraction['source_file']).resolve(strict=True)
        if digest_file(source) != extraction['source_sha256']:
            raise ValueError('原资料已变化，请重新导入核对')
        source_file, source_sha256 = str(source), extraction['source_sha256']
        imported = extraction['sources']
    version = uuid.uuid4().hex
    folder = store.root / 'profiles'
    folder.mkdir(exist_ok=True)
    data = {**profile, 'sources': {}}
    for key, value in {**sources, **imported}.items():
        current = _field(profile, key)
        if current is None:
            continue
        provenance = {**value}
        baseline = extraction['profile'] if key in imported else original
        if current != _field(baseline, key):
            provenance['edited'] = True
        data['sources'][key] = provenance
    profile_path = folder / (version + '.json')
    profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = {'version_id': version, 'profile_path': str(profile_path), 'profile_sha256': digest_file(profile_path),
                'resume_path': previous.get('resume_path', ''), 'resume_sha256': previous.get('resume_sha256', ''),
                'source_file': source_file, 'source_sha256': source_sha256, 'profile': data}
    temporary = folder / (version + '.current')
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'current.json')
    return _with_resume(store, metadata)


def _read_profile_version(store):
    pointer = store.root / 'profiles/current.json'
    if not pointer.is_file():
        return None
    value = json.loads(pointer.read_text(encoding='utf-8'))
    path = Path(value['profile_path']).resolve()
    if path.parent != store.root / 'profiles' or digest_file(path) != value['profile_sha256']:
        raise ValueError('已保存资料版本丢失或变更，请重新导入')
    value['profile'] = json.loads(path.read_text(encoding='utf-8'))
    return value


def _managed_resume(store):
    folder = store.root / 'resumes'
    pointer = folder / 'current.json'
    if not pointer.is_file():
        return None
    saved = json.loads(pointer.read_text(encoding='utf-8'))
    path = Path(saved['path']).resolve()
    if (path.parent.parent != folder or not re.fullmatch(r'[0-9a-f]{32}', path.parent.name)
            or path.name != saved['name'] or path.suffix.lower() not in ('.pdf', '.docx')):
        raise ValueError('简历文件路径无效，请重新导入')
    return saved


def _with_resume(store, profile):
    saved = _managed_resume(store)
    if saved:
        if profile is None:
            profile = {'profile': {}, 'profile_path': '', 'profile_sha256': '', 'version_id': '',
                       'source_file': '', 'source_sha256': ''}
        profile.update(resume_path=saved['path'], resume_sha256=saved['sha256'])
    return profile


def get_profile(store):
    return _with_resume(store, _read_profile_version(store))


def get_resume(store):
    saved = _managed_resume(store)
    if saved is not None:
        return saved
    profile = _read_profile_version(store) or {}
    path = profile.get('resume_path')
    return {'name': Path(path).name, 'path': path, 'sha256': profile['resume_sha256']} if path else None


def import_resume(store, path):
    source = Path(path).expanduser().resolve(strict=True)
    kind = source.suffix.lower()
    if kind not in ('.pdf', '.docx') or not source.is_file() or not 0 < source.stat().st_size <= MAX_FILE_SIZE:
        raise ValueError('请选择不超过 20 MB 的 PDF 或 DOCX 简历')
    version = uuid.uuid4().hex
    folder = store.root / 'resumes'
    folder.mkdir(exist_ok=True)
    if folder.resolve() != folder:
        raise ValueError('简历目录无效，请检查本机资料目录')
    stored = folder / version / source.name
    with TemporaryDirectory(dir=folder) as temp:
        staged = Path(temp) / source.name
        with source.open('rb') as original:
            staged.write_bytes(original.read(MAX_FILE_SIZE + 1))
        if staged.stat().st_size > MAX_FILE_SIZE:
            raise ValueError('简历文件大小已变化，请重新选择')
        _check_resume_format(staged)
        fingerprint = digest_file(staged)
        if source.stat().st_size > MAX_FILE_SIZE or digest_file(source) != fingerprint:
            raise ValueError('简历原文件已变化，请重新选择')
        stored.parent.mkdir()
        staged.replace(stored)
    metadata = {'name': source.name, 'path': str(stored), 'sha256': fingerprint}
    temporary = folder / (version + '.current')
    temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / 'current.json')
    return metadata


def _check_resume_format(path):
    kind = path.suffix.lower()
    if kind == '.pdf':
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            reader = PdfReader(path)
            if not reader.is_encrypted and not reader.pages:
                raise ValueError('PDF 简历没有页面')
        except PdfReadError as error:
            raise ValueError('PDF 简历文件已损坏') from error
    else:
        try:
            with zipfile.ZipFile(path) as document:
                document.getinfo('word/document.xml')
        except (zipfile.BadZipFile, KeyError) as error:
            raise ValueError('DOCX 简历文件已损坏') from error


def _verified_resume(store):
    saved = get_resume(store)
    if not saved:
        raise ValueError('请先导入简历')
    source = Path(saved['path'])
    if (not source.is_file() or source.is_symlink() or source.suffix.lower() not in ('.pdf', '.docx')
            or source.stat().st_size > MAX_FILE_SIZE or digest_file(source) != saved['sha256']):
        raise ValueError('简历已变更、丢失或格式不受支持，请重新导入')
    return source


def preview_resume(store):
    source = _verified_resume(store)
    try:
        extracted = extract_profile(source)
    except ValueError as error:
        return {'text': '', 'message': str(error)}
    return {'text': '\n'.join(block['text'] for block in extracted['blocks']),
            'ocr_pages': extracted['ocr_pages'], 'message': extracted['ocr_warning']}


def extract_resume_profile(store):
    source = _verified_resume(store)
    result = extract_profile(source)
    saved = get_resume(store)
    if not saved or result['source_file'] != saved['path'] or result['source_sha256'] != saved['sha256']:
        raise ValueError('简历已变更，请重新读取后核对')
    return result


def open_resume(store):
    source = _verified_resume(store)
    if os.name != 'nt':
        raise ValueError('打开原件需要 Windows 桌面')
    os.startfile(source)
    return {'opened': True}
