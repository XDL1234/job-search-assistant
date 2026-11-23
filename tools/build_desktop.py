"""构建 Windows MSI 和 EXE 安装包，按白名单复制工作进程和官方依赖。"""
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
AGENT_FILES = ('agent-browser.exe', 'runtime-manifest.json', 'LICENSE-APACHE-2.0.txt', 'NOTICE.txt')
OCR_REQUIREMENTS = ('rapidocr==3.10.0', 'onnxruntime==1.32.0', 'pypdfium2==5.15.2')


def resource_manifest(directory):
    directory = Path(directory).resolve()
    files = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('发行资源不得包含链接')
        if not path.is_file() or path == directory/'resource-manifest.json':
            continue
        relative = path.relative_to(directory)
        if (relative.parts[0] not in {'worker', 'codex', 'licenses', 'agent-browser', 'THIRD-PARTY-NOTICES.txt'}
                or path.name.lower() in {'auth.json', 'config.toml', 'records.sqlite3', 'profile.json', 'resume.pdf',
                                         'resume.docx', 'agent-browser.json', 'browser-bridge.json', 'security-smoke.json'}
                or any(p.casefold() in {'.git', '.codex', '.verification', 'frames', 'evidence', 'sessions'} for p in relative.parts)
                or path.suffix.lower().startswith(('.db-', '.sqlite-', '.sqlite3-'))
                or path.suffix.lower() in {'.sqlite3', '.sqlite', '.db', '.log', '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm'}):
            raise ValueError('发行目录出现用户数据或运行记录：' + relative.as_posix())
        files.append({'path': relative.as_posix(), 'size': path.stat().st_size,
                      'sha256': file_hash(path)})
    return files


def clear_generated_resources():
    for name in ('worker','codex','licenses','browser-extension','agent-browser'):
        path=RESOURCES/name
        if path.is_symlink() or path.resolve().parent != RESOURCES.resolve():
            raise ValueError('构建输出指向资源目录之外，拒绝清理')
        if path.is_dir():
            shutil.rmtree(path)


def run(args, **kwargs):
    print('运行：' + ' '.join(str(a) for a in args[:5]), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def collect_python_notices(python, destination, env):
    # Python 包许可证进入分发资源，PyInstaller 自带 PSF Python 许可证。
    code = """import importlib.metadata as m, pathlib, shutil, sys
dest=pathlib.Path(sys.argv[1]); dest.mkdir(parents=True,exist_ok=True)
for d in m.distributions():
 for f in d.files or []:
  if any(marker in f.as_posix().lower() for marker in ('license', 'copying', 'notice')):
   source=d.locate_file(f)
   if source.is_file():
    relative=pathlib.Path(f.name)
    if len(f.parts)>2 and f.parts[0].endswith('.dist-info') and f.parts[1]=='licenses':
     relative=pathlib.Path(*f.parts[2:])
    target=dest/d.metadata['Name']/relative
    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
"""
    run([python, '-c', code, destination], env=env)


def build_worker(env):
    source = ROOT/'apps/worker'
    sys.path.insert(0, str(source/'scripts'))
    from job_assistant.bootstrap import source_files, tree_hash
    python = BUILD/'python/Scripts/python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(python.parents[1])
    manifest = json.loads((source/'dependencies.json').read_text())
    requirements = [f"{p['name']}=={p['version']}" for p in manifest['packages']]
    run([python, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--index-url', 'https://pypi.org/simple',
         f'PyInstaller=={PYINSTALLER_VERSION}', *requirements, *OCR_REQUIREMENTS], env=env)
    run([python, '-m', 'pip', 'check'], env=env)
    stage = BUILD/('stage-' + uuid.uuid4().hex[:8])
    worker_data = stage/'worker-data'
    source_hash = tree_hash(source)
    for path in source_files(source):
        target = worker_data/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    collect_python_notices(python, worker_data/'third-party-licenses', env)
    env = {**env, 'PYINSTALLER_CONFIG_DIR': str(BUILD/'pyinstaller-cache')}
    run([python, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'worker',
         '--distpath', RESOURCES, '--workpath', stage/'work', '--specpath', stage,
         '--paths', worker_data/'scripts', '--add-data', f'{worker_data};worker-data',
         '--collect-all', 'pyautogui', '--collect-all', 'openpyxl',
         '--collect-data', 'rapidocr', '--hidden-import', 'onnxruntime', '--collect-binaries', 'onnxruntime',
         '--collect-data', 'pypdfium2_raw', '--collect-binaries', 'pypdfium2_raw', worker_data/'scripts/run.py'], env=env, cwd=ROOT)
    if tree_hash(source) != source_hash:
        raise ValueError('构建期间工作进程源码发生变化，请重新构建')
    worker = RESOURCES/'worker/worker.exe'
    (RESOURCES/'worker/build-manifest.json').write_text(json.dumps({
        'source_sha256': source_hash, 'worker_sha256': file_hash(worker),
        'pyinstaller': PYINSTALLER_VERSION,
    }, indent=2), encoding='utf-8')
    return RESOURCES/'worker/worker.exe'


def file_hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verify_agent_browser(directory, published=False):
    directory = Path(directory)
    if published and {path.name for path in directory.iterdir()} != set(AGENT_FILES):
        raise ValueError('agent-browser 发行目录含白名单之外的文件')
    for name in AGENT_FILES:
        path = directory/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('agent-browser 发行文件缺失或不安全：' + name)
    manifest = json.loads((directory/'runtime-manifest.json').read_text(encoding='utf-8'))
    expected = json.loads((ROOT/'tools/vendor/agent-browser-lock.json').read_text(encoding='utf-8'))
    permitted = set(expected) | {'patch_sha256', 'exe_sha256', 'executable', 'capabilities'}
    patch_hash = hashlib.sha256((ROOT/'tools/vendor/agent-browser-patch.json').read_bytes()
                               + (ROOT/'tools/vendor/agent-browser-embedded.rs').read_bytes()).hexdigest()
    if (set(manifest) != permitted or any(manifest.get(key) != value for key, value in expected.items())
            or manifest.get('patch_sha256') != patch_hash or manifest.get('executable') != 'agent-browser.exe'
            or manifest.get('exe_sha256') != file_hash(directory/'agent-browser.exe')):
        raise ValueError('agent-browser 版本、来源、补丁或文件 SHA-256 不符')
    probe = json.loads(subprocess.check_output([str(directory/'agent-browser.exe'), '--job-assistant-probe'],
                                             text=True, encoding='utf-8', timeout=10))
    required = {'command_auth', 'stream_auth', 'stream_read_only', 'stream_http_disabled', 'jpeg_ack_pacing', 'existing_daemon_only'}
    capabilities = manifest.get('capabilities')
    if (not isinstance(capabilities, dict) or set(capabilities) != required
            or any(type(value) is not bool for value in capabilities.values())
            or not all(capabilities.get(key) is True for key in required)
            or probe.get('version') != expected['version'] or probe.get('job_assistant_patch') != expected['patch_level']
            or probe.get('embedded_auth') is not True or probe.get('read_only') is not True
            or probe.get('capabilities') != capabilities):
        raise ValueError('agent-browser 实际运行时能力与清单不符')
    if 'Apache License' not in (directory/'LICENSE-APACHE-2.0.txt').read_text(encoding='utf-8'):
        raise ValueError('agent-browser 缺少上游许可证')
    if (directory/'NOTICE.txt').read_bytes() != (ROOT/'tools/vendor/agent-browser-NOTICE.txt').read_bytes():
        raise ValueError('agent-browser 补丁声明不符')
    return manifest


def copy_agent_browser(source):
    manifest = verify_agent_browser(source)
    target = RESOURCES/'agent-browser'
    if target.is_symlink() or target.resolve().parent != RESOURCES.resolve():
        raise ValueError('agent-browser 输出指向资源目录之外')
    target.mkdir(exist_ok=True)
    for name in AGENT_FILES:
        shutil.copy2(Path(source)/name, target/name)
    verify_agent_browser(target, published=True)
    return manifest


def verify_reused_resources():
    if (RESOURCES/'browser-extension').exists():
        raise ValueError('资源仍含旧浏览器扩展，请执行完整资源构建')
    previous = RESOURCES/'resource-manifest.json'
    if not previous.is_file():
        raise ValueError('缺少原资源校验清单，不能复用')
    manifest = json.loads(previous.read_text(encoding='utf-8'))
    if manifest.get('codex') != CODEX_VERSION or manifest.get('files') != resource_manifest(RESOURCES):
        raise ValueError('现有资源内容与校验清单不符，不能复用')
    sys.path.insert(0, str(ROOT/'apps/worker/scripts'))
    from job_assistant.bootstrap import tree_hash
    worker = RESOURCES/'worker/worker.exe'
    metadata = json.loads((RESOURCES/'worker/build-manifest.json').read_text(encoding='utf-8'))
    if (metadata.get('source_sha256') != tree_hash(ROOT/'apps/worker')
            or metadata.get('worker_sha256') != file_hash(worker)
            or metadata.get('pyinstaller') != PYINSTALLER_VERSION):
        raise ValueError('内置 worker 与当前工作进程源码不符，不能复用')
    version = subprocess.check_output([str(RESOURCES/'codex/bin/codex.exe'), '--version'],
                                      text=True, encoding='utf-8', timeout=10).strip()
    if version != 'codex-cli ' + CODEX_VERSION:
        raise ValueError('内置 Codex 版本不符，不能复用')
    runtime = verify_agent_browser(RESOURCES/'agent-browser', published=True)
    if manifest.get('agent_browser') != runtime:
        raise ValueError('原生浏览器运行时与总资源清单不符')
    return runtime


def copy_codex(env, target=None):
    exe = Path(env.get('JOB_ASSISTANT_CODEX', ''))
    if not exe.is_file():
        raise ValueError('请通过 JOB_ASSISTANT_CODEX 指定官方 Codex 0.156.1 Windows x64 发行版中的 bin/codex.exe')
    version = subprocess.check_output([str(exe), '--version'], text=True, encoding='utf-8').strip()
    if version != 'codex-cli ' + CODEX_VERSION:
        raise ValueError('Codex 版本不符：' + version)
    vendor = exe.parent.parent
    package = vendor/'package.json' if (vendor/'package.json').exists() else vendor.parent.parent/'package.json'
    metadata = json.loads(package.read_text(encoding='utf-8'))
    if metadata.get('license') != 'Apache-2.0' or not metadata['version'].startswith(CODEX_VERSION):
        raise ValueError('Codex 官方包元数据不符')
    target = target or RESOURCES/'codex'
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
    # WiX 的 Windows Installer 校验使用系统临时目录，构建目录只用于 Python 打包。
    build_env = {**env, 'TEMP': str(BUILD/'temp'), 'TMP': str(BUILD/'temp'), 'PIP_CACHE_DIR': str(BUILD/'pip-cache')}
    RESOURCES.mkdir(exist_ok=True)
    if RESOURCES.is_symlink() or RESOURCES.resolve().parent != TAURI.resolve():
        raise ValueError('不安全的构建资源目录')
    if not args.reuse_resources:
        agent_source = Path(env.get('JOB_ASSISTANT_AGENT_BROWSER', str(ROOT/'.verification/runtime/agent-browser/agent-browser.exe')))
        agent_source = agent_source.parent if agent_source.suffix.lower() == '.exe' else agent_source
        if not (agent_source/'agent-browser.exe').is_file():
            run([sys.executable, ROOT/'tools/build_agent_browser.py'], env=env, cwd=ROOT)
        verify_agent_browser(agent_source)
        cached_codex=BUILD/'codex-source'/CODEX_VERSION
        if not (cached_codex/'bin/codex.exe').is_file():
            copy_codex(env,cached_codex)
        env['JOB_ASSISTANT_CODEX']=str(cached_codex/'bin/codex.exe')
        clear_generated_resources()
        build_worker(build_env)
        copy_codex(env)
        collect_notices(env)
        runtime = copy_agent_browser(agent_source)
        (RESOURCES/'THIRD-PARTY-NOTICES.txt').write_text(
            '求职助手包含：Python（PSF）、PyInstaller bootloader（GPL exception）、'
            'PyAutoGUI/Pillow/openpyxl 等依赖（许可证见 runtime/worker/_internal/worker-data/third-party-licenses）；'
            'Codex CLI 0.156.1（Apache-2.0，见 runtime/codex/LICENSE-APACHE-2.0.txt）。'
            'agent-browser 0.43.0 与本项目嵌入补丁（Apache-2.0，见 runtime/agent-browser 的 LICENSE 与 NOTICE）。'
            'Codex 附带组件的 notices/licenses 按官方发行目录保留。\n'
            '本机 OCR 包含 RapidOCR（Apache-2.0）、ONNX Runtime（MIT）及 pypdfium2/PDFium。'
            'OCR 模型由 RapidOCR 从 PaddleOCR 转换为 ONNX，模型权重版权属百度及相关 PaddleOCR 权利人，'
            '许可见 https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/ 。\n'
            'Tauri、React 等组件源码与锁定版本见项目仓库 https://github.com/XDL1234/job-search-assistant 。\n', encoding='utf-8')
    else:
        runtime = verify_reused_resources()
    files=resource_manifest(RESOURCES)
    (RESOURCES/'resource-manifest.json').write_text(json.dumps({'codex':CODEX_VERSION,'agent_browser':runtime,'files':files},ensure_ascii=False,indent=2),encoding='utf-8')
    if args.resources_only:
        print(json.dumps({'resources':str(RESOURCES),'files':len(files)}));return
    config = json.loads((TAURI/'tauri.conf.json').read_text(encoding='utf-8'))
    run([shutil.which('npm.cmd'), 'run', 'tauri', '--', 'build', '--config', 'src-tauri/tauri.bundle.conf.json'],
        cwd=ROOT/'apps/desktop', env=env)
    output=ROOT/'dist/desktop'
    output.mkdir(parents=True,exist_ok=True)
    version=config['version']
    bundles = TAURI/'target/release/bundle'
    installers = [
        ('msi', f'*_{version}_x64*.msi', f'job-search-assistant-{version}-windows-x64.msi'),
        ('nsis', f'*_{version}_x64-setup.exe', f'job-search-assistant-{version}-windows-x64-setup.exe'),
    ]
    report, checksums = [], []
    for kind, pattern, name in installers:
        artifacts = list((bundles/kind).glob(pattern))
        if len(artifacts) != 1:
            raise ValueError(f'{kind} 未得到唯一安装包')
        destination = output/name
        shutil.copy2(artifacts[0], destination)
        digest = file_hash(destination)
        checksums.append(f'{digest}  {name}\n')
        report.append({'installer': str(destination), 'sha256': digest, 'bytes': destination.stat().st_size})
    (output/'SHA256SUMS.txt').write_text(''.join(checksums), encoding='ascii')
    shutil.copy2(RESOURCES/'resource-manifest.json',output/'resource-manifest.json')
    print(json.dumps({'installers': report}, ensure_ascii=False))


if __name__=='__main__':
    main()
