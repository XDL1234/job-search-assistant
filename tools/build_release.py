"""仅打包获准分发的源码、示例和测试，不递归打包工作目录。"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    skill = root / 'skills/job-search-assistant'
    sys.path.insert(0, str(skill / 'scripts'))
    from job_assistant.bootstrap import source_files
    version = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    files = source_files(skill)
    files += [root / name for name in ('.codex-plugin/plugin.json', 'README.md', 'install.ps1', 'open-dashboard.ps1', 'tools/build_release.py', 'docs/implementation.md')]
    files += sorted((root / 'tests').glob('*.py'))
    files += sorted((root / 'tests/fixtures').glob('*.html'))
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'job-search-assistant-{version}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(files):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError('发行文件丢失或指向源码之外：' + str(path))
            package.write(path, 'job-search-assistant/' + path.relative_to(root).as_posix())
    checksum = archive.with_suffix('.zip.sha256')
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f'{sha}  {archive.name}\n', encoding='ascii')
    return {'archive': str(archive), 'checksum': str(checksum), 'sha256': sha, 'files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'dist')
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output), ensure_ascii=False, indent=2))
