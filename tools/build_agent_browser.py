"""固定 agent-browser 源码与嵌入补丁，构建 Windows 原生运行时；不安装全局工具。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'tools/vendor'
BUILD = ROOT / '.verification/agent-browser-src'
OUTPUT = ROOT / '.verification/runtime/agent-browser'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('路径不在专用构建目录内')
    return path


def unpack(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        members = []
        for item in source.infolist():
            inside(destination / item.filename, destination)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                # 上游 .claude 是文档目录别名；构建不创建归档链接。
                continue
            members.append(item)
        source.extractall(destination, members)


def apply_embedding(source, changes, helper):
    source = Path(source).resolve()
    planned = {}
    for change in changes:
        path = inside(source / change['path'], source)
        text = planned.get(path, path.read_text(encoding='utf-8'))
        if not change['before'] or text.count(change['before']) != 1:
            raise ValueError('上游补丁上下文不唯一：' + change['path'])
        planned[path] = text.replace(change['before'], change['after'], 1)
    target = inside(source / 'cli/src/job_assistant.rs', source)
    if target.exists():
        raise ValueError('上游源码已有同名嵌入模块')
    for path, text in planned.items():
        path.write_text(text, encoding='utf-8', newline='\n')
    shutil.copy2(helper, target)


def source_tree():
    lock = json.loads((VENDOR / 'agent-browser-lock.json').read_text(encoding='utf-8'))
    patch = VENDOR / 'agent-browser-patch.json'
    helper = VENDOR / 'agent-browser-embedded.rs'
    patch_hash = hashlib.sha256(patch.read_bytes() + helper.read_bytes()).hexdigest()
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / ('agent-browser-v' + lock['version'] + '.zip')
    if not archive.exists():
        temporary = archive.with_suffix('.download')
        with urllib.request.urlopen(lock['source_url'], timeout=90) as response:
            temporary.write_bytes(response.read())
        if digest(temporary) != lock['source_sha256']:
            raise ValueError('上游源码 SHA-256 不符')
        temporary.replace(archive)
    if digest(archive) != lock['source_sha256']:
        raise ValueError('缓存源码 SHA-256 不符')
    stage = inside(BUILD / ('patched-' + lock['version'] + '-' + patch_hash[:16]), BUILD)
    source = inside(stage / lock['source_directory'], stage)
    marker = source / '.job-assistant-patch.json'
    if not marker.exists():
        if stage.exists():
            raise ValueError('发现未完成的补丁目录，请检查后另行清理：' + str(stage))
        stage.mkdir()
        unpack(archive, stage)
        changes = json.loads(patch.read_text(encoding='utf-8'))['changes']
        apply_embedding(source, changes, helper)
        marker.write_text(json.dumps({'patch_sha256': patch_hash}), encoding='utf-8')
    elif json.loads(marker.read_text(encoding='utf-8')).get('patch_sha256') != patch_hash:
        raise ValueError('源码补丁记录不符')
    return source, lock, patch_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--skip-tests', action='store_true')
    args = parser.parse_args()
    source, lock, patch_hash = source_tree()
    if args.prepare_only:
        print(json.dumps({'source': str(source), 'version': lock['version'], 'patch_sha256': patch_hash})); return
    if os.name != 'nt':
        raise SystemExit('此构建入口要求 Windows x64')
    sys.path.insert(0, str(ROOT / 'tools'))
    from desktop_dev import environment
    env = environment()
    env['CARGO_TARGET_DIR'] = str(ROOT / '.verification/agent-browser-target')
    cargo = shutil.which('cargo', path=env['PATH'])
    if not cargo:
        raise ValueError('缺少本机 Rust 工具链')
    base = [cargo, '--locked', '--profile', 'ci', '--manifest-path', str(source / 'cli/Cargo.toml')]
    if not args.skip_tests:
        subprocess.run([base[0], 'test', *base[1:], '--bin', 'agent-browser', 'job_assistant::tests'], check=True, env=env)
    subprocess.run([base[0], 'build', *base[1:]], check=True, env=env)
    built = Path(env['CARGO_TARGET_DIR']) / 'ci/agent-browser.exe'
    probe = json.loads(subprocess.check_output([str(built), '--job-assistant-probe'], text=True, encoding='utf-8'))
    if probe.get('version') != lock['version'] or probe.get('job_assistant_patch') != lock['patch_level']:
        raise ValueError('运行时版本或补丁能力探针不符')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    executable = OUTPUT / 'agent-browser.exe'
    if not executable.is_file() or digest(built) != digest(executable):
        shutil.copy2(built, executable)
    shutil.copy2(source / 'LICENSE', OUTPUT / 'LICENSE-APACHE-2.0.txt')
    notice = VENDOR / 'agent-browser-NOTICE.txt'
    shutil.copy2(notice, OUTPUT / 'NOTICE.txt')
    manifest = {**lock, 'patch_sha256': patch_hash, 'exe_sha256': digest(executable),
                'executable': executable.name, 'capabilities': probe['capabilities']}
    (OUTPUT / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({**manifest, 'executable': str(executable)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
