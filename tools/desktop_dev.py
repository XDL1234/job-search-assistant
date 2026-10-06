"""D 盘工具链启动器；只为子进程设置环境，不改全局 PATH。"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def environment():
    env = os.environ.copy()
    rust = Path('D:/Development/Rust')
    if (rust / 'cargo/bin/cargo.exe').exists():
        env['CARGO_HOME'] = str(rust / 'cargo')
        env['RUSTUP_HOME'] = str(rust / 'rustup')
        env['PATH'] = str(rust / 'cargo/bin') + os.pathsep + env['PATH']
    env['CARGO_TARGET_DIR'] = str(ROOT / 'apps/desktop/src-tauri/target')
    env.setdefault('JOB_ASSISTANT_DATA_DIR', str(ROOT / '.verification/desktop-app/data'))
    env.setdefault('JOB_ASSISTANT_REPO', str(ROOT))
    sys.path.insert(0, str(ROOT / 'skills/job-search-assistant/scripts'))
    from job_assistant.bootstrap import setup, default_root
    runtime = setup(default_root(), False)
    env.setdefault('JOB_ASSISTANT_PYTHON', runtime['python'] if runtime['ready'] else sys.executable)
    shim = shutil.which('codex.cmd')
    if shim:
        package = Path(shim).parent / 'node_modules/@openai/codex/node_modules'
        matches = list(package.glob('@openai/codex-win32-*/vendor/*/bin/codex.exe'))
        if matches:
            env.setdefault('JOB_ASSISTANT_CODEX', str(matches[0]))
    for kind in ('http', 'https'):
        proxy = urllib.request.getproxies().get(kind)
        if proxy:
            env.setdefault(kind.upper() + '_PROXY', proxy)
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['dev', 'test', 'build', 'check', 'run'])
    args = parser.parse_args()
    env = environment()
    cargo = shutil.which('cargo', path=env['PATH'])
    if not cargo:
        raise SystemExit('缺少 Rust/Cargo，请先安装构建工具')
    if args.action == 'dev':
        command, cwd = [shutil.which('npm.cmd'), 'run', 'tauri', 'dev'], ROOT / 'apps/desktop'
    else:
        command, cwd = [cargo, args.action], ROOT / 'apps/desktop/src-tauri'
    return subprocess.call(command, cwd=cwd, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
