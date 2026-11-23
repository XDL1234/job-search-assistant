"""复制桌面应用资料，在同盘暂存并校验后切换数据目录。"""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .inputs import path_key
from .store import Store, packed


_DATABASE = 'records.sqlite3'
_TRANSIENT = {
    _DATABASE, _DATABASE + '-wal', _DATABASE + '-shm', _DATABASE + '-journal',
    'agent-browser.json', 'browser-bridge.json', 'desktop-binding.json',
    'chrome-setup-complete', 'requests',
}
_CHROME_TRANSIENT = {'DevToolsActivePort', 'SingletonCookie', 'SingletonLock', 'SingletonSocket'}


def _checked_entry(path):
    metadata = path.lstat()
    if (getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))):
        raise ValueError('资料迁移目录包含链接或特殊文件')
    return metadata


def _optional_directory(path):
    if path is None:
        return None
    path = Path(path).absolute()
    try:
        if not stat.S_ISDIR(_checked_entry(path).st_mode):
            raise ValueError('资料迁移来源必须是文件夹')
    except FileNotFoundError:
        return None
    return path.resolve(strict=True)


def _copy_tree(source, destination, skip=()):
    def checked_names(directory, names):
        ignored = _CHROME_TRANSIENT if Path(directory) == source / 'chrome-profile' else ()
        for name in set(names) - set(ignored):
            _checked_entry(Path(directory) / name)
        return ignored

    for entry in source.iterdir():
        if entry.name in skip:
            continue
        destination_entry = destination / entry.name
        if stat.S_ISDIR(_checked_entry(entry).st_mode):
            shutil.copytree(entry, destination_entry, ignore=checked_names)
        else:
            shutil.copy2(entry, destination_entry)


def _rebased_path(value, source, destination):
    if not isinstance(value, str) or not value:
        return value
    path, source = path_key(value), path_key(source)
    if path.is_absolute() and path.is_relative_to(source):
        relative = path.relative_to(source)
        if '..' not in relative.parts:
            return str(destination / relative)
    return value


def _rebase_config(config, rebase):
    for key in ('profile_path', 'resume_path', 'company_table'):
        if key in config:
            config[key] = rebase(config[key])
    url = config.get('simulation_url')
    if isinstance(url, str):
        parsed = urlsplit(url)
        if parsed.scheme == 'file' and not parsed.netloc:
            path = url2pathname(parsed.path)
            target = rebase(path)
            if target != path:
                config['simulation_url'] = Path(target).as_uri()


def _rebase_database(path, rebase):
    from .ai_selection import digest
    with closing(sqlite3.connect(path)) as db, db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'evidence' in tables:
            for identity, value in db.execute('SELECT id,path FROM evidence').fetchall():
                updated = rebase(value)
                if updated != value:
                    db.execute('UPDATE evidence SET path=? WHERE id=?', (updated, identity))
        hashes = {}
        if 'runs' in tables:
            for identity, data, approval in db.execute('SELECT id,config,approval FROM runs').fetchall():
                config = json.loads(data)
                before = digest(config)
                _rebase_config(config, rebase)
                hashes[identity] = (before, digest(config))
                try:
                    history = json.loads(approval)
                except (TypeError, ValueError):
                    history = None
                if isinstance(history, dict) and isinstance(history.get('revisions'), list):
                    for revision in history['revisions']:
                        if isinstance(revision, dict) and isinstance(revision.get('previous_config'), dict):
                            _rebase_config(revision['previous_config'], rebase)
                    approval = packed(history)
                db.execute('UPDATE runs SET config=?,approval=? WHERE id=?', (packed(config), approval, identity))
        if 'app_previews' in tables:
            for ticket, data in db.execute('SELECT ticket,config FROM app_previews').fetchall():
                preview = json.loads(data)
                _rebase_config(preview.get('config', preview), rebase)
                revision = preview.get('revision')
                if isinstance(revision, dict):
                    pair = hashes.get(revision.get('run_id'))
                    if pair and revision.get('config_digest') == pair[0]:
                        revision['config_digest'] = pair[1]
                db.execute('UPDATE app_previews SET config=? WHERE ticket=?', (packed(preview), ticket))
        if 'app_sessions' in tables:
            db.execute("UPDATE app_sessions SET thread_id=NULL,state='idle'")
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('投递记录数据库未通过复制校验')


def _rebase_metadata(stage, rebase):
    pointers = [(stage / 'profiles/current.json', ('profile_path', 'resume_path', 'source_file')),
                (stage / 'resumes/current.json', ('path',))]
    for folder in ('frames', 'browser-diagnostics'):
        pointers.extend((path, ('path',)) for path in (stage / folder).glob('*.json'))
    for path, keys in pointers:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (UnicodeError, ValueError):
            if path.name == 'current.json':
                raise
            continue
        if isinstance(value, dict):
            changes = {key: rebase(value[key]) for key in keys if key in value}
            if any(value[key] != updated for key, updated in changes.items()):
                value.update(changes)
                path.write_text(packed(value), encoding='utf-8')


def migrate_desktop_storage(source, destination, legacy_webview=None):
    destination = Path(destination).absolute()
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('安装目录下已有应用资料，迁移未覆盖现有文件')
    destination = destination.resolve()
    trees = (_optional_directory(source), _optional_directory(legacy_webview))
    source, webview = trees
    target = path_key(destination)
    if any(target.is_relative_to(tree) or tree.is_relative_to(target)
           for tree in (path_key(tree) for tree in trees if tree)):
        raise ValueError('资料迁移的来源与目标目录不能重叠')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix='.desktop-storage-') as temporary:
        stage = Path(temporary) / 'data'
        stage.mkdir(mode=0o700)
        if source:
            # 版本资料本身保持原字节，运行时仍可核验原先授权的 SHA-256。
            rebase = lambda value: _rebased_path(value, source, destination)
            _copy_tree(source, stage, _TRANSIENT)
            original = source / _DATABASE
            if original.exists() or original.is_symlink():
                if not stat.S_ISREG(_checked_entry(original).st_mode):
                    raise ValueError('投递记录数据库不是普通文件')
                with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as old_db, \
                        closing(sqlite3.connect(stage / _DATABASE)) as new_db:
                    old_db.backup(new_db)
                _rebase_database(stage / _DATABASE, rebase)
                if (stage / 'results.xlsx').is_file():
                    with closing(Store(stage)) as store:
                        store.export(evidence_directory=destination / 'evidence')
            _rebase_metadata(stage, rebase)
        if webview and not (stage / 'webview').exists():
            target = stage / 'webview'
            target.mkdir(mode=0o700)
            _copy_tree(webview, target)
        stage.rename(destination)
    return {'migrated': source is not None or webview is not None, 'data_root': str(destination)}
