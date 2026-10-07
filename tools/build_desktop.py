"""构建可安装的 Windows 测试版；只复制 Skill 白名单和官方依赖发行文件。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import venv

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / 'apps/desktop/src-tauri'
RESOURCES = TAURI / 'resources'
BUILD = ROOT / '.verification/desktop-release'
CODEX_VERSION = '0.156.1'
PYINSTALLER_VERSION = '6.15.0'


def resource_manifest(directory):
    directory = Path(directory).resolve()
    files = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('发行资源不得包含链接')
        if not path.is_file() or path.name == 'resource-manifest.json':
            continue
        relative = path.relative_to(directory)
        if (path.name.lower() in {'auth.json', 'config.toml', 'records.sqlite3', 'profile.json', 'resume.pdf'}
                or any(p in {'.git', '.codex', '.verification', 'frames', 'evidence', 'sessions'} for p in relative.parts)
                or path.suffix in {'.sqlite3', '.log'}):
            raise ValueError('发行目录出现用户数据或运行记录：' + relative.as_posix())
        files.append({'path': relative.as_posix(), 'size': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return files


def run(args, **kwargs):
    print('运行：' + ' '.join(str(a) for a in args[:5]), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def build_worker(env):
    sys.path.insert(0, str(ROOT/'skills/job-search-assistant/scripts'))
    from job_assistant.bootstrap import source_files
    python = BUILD/'python/Scripts/python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(python.parents[1])
    manifest = json.loads((ROOT/'skills/job-search-assistant/dependencies.json').read_text())
    requirements = [f"{p['name']}=={p['version']}" for p in manifest['packages']]
    run([python, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--index-url', 'https://pypi.org/simple',
         f'PyInstaller=={PYINSTALLER_VERSION}', *requirements], env=env)
    run([python, '-m', 'pip', 'check'], env=env)
    stage = BUILD/('stage-' + uuid.uuid4().hex[:8])
    skill = stage/'skill'
    source = ROOT/'skills/job-search-assistant'
    for path in source_files(source):
        target = skill/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # Python 包许可证进入分发资源，PyInstaller 自带 PSF Python 许可证。
    code = """import importlib.metadata as m, pathlib, shutil, sys
dest=pathlib.Path(sys.argv[1]); dest.mkdir(parents=True,exist_ok=True)
for d in m.distributions():
 for f in d.files or []:
  if 'license' in str(f).lower() or 'copying' in str(f).lower():
   p=d.locate_file(f)
   if p.is_file():
    t=dest/d.metadata['Name']/pathlib.Path(str(f)).name
    t.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,t)
"""
    run([python, '-c', code, skill/'third-party-licenses'], env=env)
    env = {**env, 'PYINSTALLER_CONFIG_DIR': str(BUILD/'pyinstaller-cache')}
    run([python, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'worker',
         '--distpath', RESOURCES, '--workpath', stage/'work', '--specpath', stage,
         '--paths', source/'scripts', '--add-data', f'{skill};skill',
         '--collect-all', 'pyautogui', '--collect-all', 'openpyxl', source/'scripts/run.py'], env=env, cwd=ROOT)
    return RESOURCES/'worker/worker.exe'


def copy_codex(env):
    exe = Path(env.get('JOB_ASSISTANT_CODEX', ''))
    if not exe.is_file():
        raise ValueError('请通过 JOB_ASSISTANT_CODEX 指定官方 Codex 0.156.1 Windows x64 发行版中的 bin/codex.exe')
    version = subprocess.check_output([str(exe), '--version'], text=True, encoding='utf-8').strip()
    if version != 'codex-cli ' + CODEX_VERSION:
        raise ValueError('Codex 版本不符：' + version)
    vendor = exe.parent.parent
    package = vendor.parent.parent/'package.json'
    metadata = json.loads(package.read_text(encoding='utf-8'))
    if metadata.get('license') != 'Apache-2.0' or not metadata['version'].startswith(CODEX_VERSION):
        raise ValueError('Codex 官方包元数据不符')
    target = RESOURCES/'codex'
    target.mkdir(parents=True, exist_ok=True)
    for path in vendor.rglob('*'):
        if path.is_symlink():
            raise ValueError('Codex 发行目录存在链接')
        if path.is_file():
            dest=target/path.relative_to(vendor)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path,dest)
    shutil.copy2(package, target/'package.json')
    # 官方 npm 平台包未附 Apache 文本，补入上游许可证，不复制任何用户 Codex 目录。
    license_path = target/'LICENSE-APACHE-2.0.txt'
    if not license_path.exists():
        with urllib.request.urlopen('https://raw.githubusercontent.com/openai/codex/main/LICENSE', timeout=60) as response:
            license_path.write_bytes(response.read())
    if 'Apache License' not in license_path.read_text(encoding='utf-8'):
        raise ValueError('上游许可证内容不符')


def collect_notices(env):
    destination = RESOURCES/'licenses'
    destination.mkdir(exist_ok=True)
    roots = [('npm', ROOT/'apps/desktop/node_modules'),
             ('cargo', Path(env.get('CARGO_HOME', str(Path.home()/'.cargo')))/'registry/src')]
    for label, source in roots:
        if not source.exists():
            raise ValueError('缺少依赖许可证来源：' + label)
        for base, directories, names in os.walk(source):
            directories[:] = [d for d in directories if d not in {'.git', '.cache'}]
            for name in names:
                if name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
                    path=Path(base)/name
                    if path.is_symlink() or path.stat().st_size>2_000_000:continue
                    relative=path.relative_to(source)
                    # 展平目录，避免 Windows 安装路径过长，保留可追溯的来源映射。
                    target=destination/(label+'-'+hashlib.sha256(str(relative).encode()).hexdigest()[:16]+'.txt')
                    target.write_bytes((f'Source: {label}/{relative.as_posix()}\n\n').encode()+path.read_bytes())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--resources-only', action='store_true')
    parser.add_argument('--reuse-resources', action='store_true')
    args=parser.parse_args()
    if os.name!='nt':
        raise SystemExit('本构建入口要求 Windows x64')
    sys.path.insert(0,str(ROOT/'tools'))
    from desktop_dev import environment
    env=environment()
    BUILD.mkdir(parents=True,exist_ok=True)
    (BUILD/'temp').mkdir(exist_ok=True)
    env.update({'TEMP':str(BUILD/'temp'),'TMP':str(BUILD/'temp'),'PIP_CACHE_DIR':str(BUILD/'pip-cache')})
    RESOURCES.mkdir(exist_ok=True)
    if RESOURCES.is_symlink() or RESOURCES.resolve().parent != TAURI.resolve():
        raise ValueError('不安全的构建资源目录')
    if not args.reuse_resources:
        build_worker(env)
        copy_codex(env)
        collect_notices(env)
        (RESOURCES/'THIRD-PARTY-NOTICES.txt').write_text(
            '求职助手测试版包含：Python（PSF）、PyInstaller bootloader（GPL exception）、'
            'PyAutoGUI/Pillow/openpyxl 等依赖（许可证见 runtime/worker/_internal/skill/third-party-licenses）；'
            'Codex CLI 0.156.1（Apache-2.0，见 runtime/codex/LICENSE-APACHE-2.0.txt）。'
            'Codex 附带组件的 notices/licenses 按官方发行目录保留。\n'
            'Tauri、React 等组件源码与锁定版本见项目仓库 https://github.com/XDL1234/job-search-assistant 。\n', encoding='utf-8')
    files=resource_manifest(RESOURCES)
    (RESOURCES/'resource-manifest.json').write_text(json.dumps({'codex':CODEX_VERSION,'files':files},ensure_ascii=False,indent=2),encoding='utf-8')
    if args.resources_only:
        print(json.dumps({'resources':str(RESOURCES),'files':len(files)}));return
    run([shutil.which('npm.cmd'), 'run', 'tauri', '--', 'build', '--config', 'src-tauri/tauri.bundle.conf.json', '--bundles', 'nsis'], cwd=ROOT/'apps/desktop', env=env)
    output=ROOT/'dist/desktop'
    output.mkdir(parents=True,exist_ok=True)
    version=json.loads((TAURI/'tauri.conf.json').read_text(encoding='utf-8'))['version']
    artifacts=list((TAURI/'target/release/bundle/nsis').glob(f'*{version}*-setup.exe'))
    if len(artifacts)!=1:raise ValueError('未得到唯一安装包')
    dest=output/f'job-search-assistant-{version}-windows-x64-setup.exe'
    shutil.copy2(artifacts[0],dest)
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{digest}  {dest.name}\n',encoding='ascii')
    shutil.copy2(RESOURCES/'resource-manifest.json',output/'resource-manifest.json')
    print(json.dumps({'installer':str(dest),'sha256':digest,'bytes':dest.stat().st_size},ensure_ascii=False))


if __name__=='__main__':
    main()
