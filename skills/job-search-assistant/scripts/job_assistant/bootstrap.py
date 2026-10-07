"""首次使用自动补齐私有运行环境；不修改系统 Python 或宿主权限。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import venv
from pathlib import Path

from .inputs import load_json

def skill_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'skill'
    return Path(__file__).resolve().parents[2]


SKILL_ROOT = skill_root()


def default_root():
    # MSIX 宿主可能虚拟化 LocalAppData，导致 pip 的原子替换报 WinError 17。
    return Path.home() / '.job-search-assistant'


def validate_manifest(manifest):
    if manifest.get('schema_version') != 1:
        raise ValueError('不支持的依赖清单版本')
    seen = set()
    for package in manifest.get('packages', []):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', package.get('name', '')) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', package.get('version', '')):
            raise ValueError('依赖必须使用合法包名和固定版本')
        name = package['name'].lower().replace('_', '-')
        if name in seen:
            raise ValueError('依赖名称重复')
        seen.add(name)
    # 本版能力全部内置。没有经过验证的第三方 skill 不自动下载安装。
    if manifest.get('skills'):
        raise ValueError('本发行版仅支持内置 Skill 能力，禁止未验证的外部安装来源')
    return manifest


def source_files(source):
    source = Path(source).resolve()
    approved = []
    for path in source.rglob('*'):
        if not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in path.parents if p != source):
            continue
        relative = path.relative_to(source)
        allowed = relative.as_posix() in {'SKILL.md', 'dependencies.json', 'agents/openai.yaml'}
        allowed |= relative.as_posix() in {'assets/dashboard/index.html', 'assets/dashboard/app.js', 'assets/dashboard/style.css'}
        allowed |= relative.as_posix() == 'assets/simulation.html'
        allowed |= relative.parts[0] == 'scripts' and path.suffix == '.py' and '__pycache__' not in relative.parts
        allowed |= relative.parts[0] == 'references' and path.suffix == '.md'
        allowed |= relative.parts[0] == 'assets' and '.example.' in path.name and path.suffix in ('.json', '.csv')
        if allowed:
            approved.append(path)
    return sorted(approved)


def tree_hash(source):
    source = Path(source)
    result = hashlib.sha256()
    for path in source_files(source):
        result.update(path.relative_to(source).as_posix().encode())
        result.update(b'\0')
        result.update(path.read_bytes())
    return result.hexdigest()


def install_skill(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / 'SKILL.md').is_file():
        raise ValueError('源目录缺少 SKILL.md')
    if destination.exists():
        if tree_hash(source) == tree_hash(destination):
            return {'status': 'already_installed', 'path': str(destination)}
        raise ValueError('目标已存在不同版本；不会覆盖，请使用新的安装目录或先审阅差异')
    if destination.is_relative_to(source):
        raise ValueError('安装目录不能位于源目录内部')
    staging = destination.with_name(destination.name + '.install-' + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    for path in source_files(source):
        target = staging / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if tree_hash(source) != tree_hash(staging):
        raise ValueError('安装校验失败，保留暂存目录以便诊断')
    staging.rename(destination)
    return {'status': 'installed', 'path': str(destination)}


def python_path(environment):
    return Path(environment) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def installed_versions(python):
    code = 'import importlib.metadata as m,json; print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))'
    completed = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(completed.stdout)


def setup(root, install=False):
    root = Path(root).resolve()
    manifest = validate_manifest(load_json(SKILL_ROOT / 'dependencies.json'))
    if sys.version_info < (3, 11):
        raise ValueError('需要 Python 3.11 或更高版本；请通过官方安装器准备 Python')
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
    environment = root / 'environments' / fingerprint
    python = python_path(environment)
    if install and os.name != 'nt':
        raise ValueError('本版电脑操作仅支持 Windows；可以在其他系统运行纯逻辑测试')
    if install:
        root.mkdir(parents=True, exist_ok=True)
        if not python.is_file():
            venv.EnvBuilder(with_pip=True).create(environment)
    versions = installed_versions(python) if python.is_file() else {}
    missing = [p for p in manifest['packages'] if versions.get(p['name'].lower().replace('_', '-')) != p['version']]
    if install and missing:
        requirements = [f"{p['name']}=={p['version']}" for p in manifest['packages']]
        try:
            subprocess.run([str(python), '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                            '--index-url', 'https://pypi.org/simple', *requirements], check=True, timeout=600, stdout=sys.stderr)
            subprocess.run([str(python), '-m', 'pip', 'check'], check=True, timeout=30, stdout=sys.stderr)
        except subprocess.SubprocessError as error:
            raise RuntimeError(f'依赖安装失败（{type(error).__name__}）；保留环境以便重试：{environment}') from None
        versions = installed_versions(python)
        missing = [p for p in manifest['packages'] if versions.get(p['name'].lower().replace('_', '-')) != p['version']]
    result = {'ready': not missing and os.name == 'nt', 'python': str(python), 'root': str(root),
              'missing': missing, 'platform_supported': os.name == 'nt',
              'bundled_capabilities': manifest['bundled_capabilities']}
    if install:
        (root / 'setup-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
