"""校验 MSI 安装配置与提取后的桌面应用，不注册或卸载系统产品。"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import UUID, uuid4
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
MSI_TABLES = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
function Invoke-Msi($Object, [string] $Name, [string] $Operation, [object[]] $Arguments) {
    $Object.GetType().InvokeMember($Name, $Operation, $null, $Object, $Arguments)
}
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = Invoke-Msi $installer OpenDatabase InvokeMethod @($env:JOB_ASSISTANT_MSI_SMOKE_PACKAGE, 0)
$queries = @{
    properties = @('SELECT `Property`, `Value` FROM `Property`', 2)
    directories = @('SELECT `Directory`, `Directory_Parent` FROM `Directory`', 2)
    shortcuts = @('SELECT `Directory_`, `Target`, `WkDir` FROM `Shortcut`', 3)
    upgrade = @('SELECT `UpgradeCode` FROM `Upgrade`', 1)
    execute = @('SELECT `Action` FROM `InstallExecuteSequence`', 1)
    delete = @('SELECT `DirProperty` FROM `RemoveFile`', 1)
    dialogs = @('SELECT `Dialog` FROM `Dialog`', 1)
    dir_controls = @('SELECT `Control`, `Type` FROM `Control` WHERE `Dialog_` = ''InstallDirDlg''', 2)
}
$result = @{}
foreach ($name in $queries.Keys) {
    $sql, $width = $queries[$name]
    $view = Invoke-Msi $database OpenView InvokeMethod @($sql)
    $null = Invoke-Msi $view Execute InvokeMethod @()
    $rows = [collections.generic.list[object]]::new()
    while ($record = Invoke-Msi $view Fetch InvokeMethod @()) {
        $cells = @(for ($i = 1; $i -le $width; $i++) { Invoke-Msi $record StringData GetProperty @($i) })
        $rows.Add($cells)
    }
    $null = Invoke-Msi $view Close InvokeMethod @()
    $result[$name] = $rows
}
$result | ConvertTo-Json -Depth 5 -Compress
"""


def inspect_msi(package, config):
    windows = Path(os.environ['SystemRoot'])
    powershell = windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    output = subprocess.check_output(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command', MSI_TABLES],
        env={**os.environ, 'JOB_ASSISTANT_MSI_SMOKE_PACKAGE': str(package)},
        creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8', timeout=20,
    )
    tables = json.loads(output)
    properties = dict(tables['properties'])
    upgrade = UUID(config['bundle']['windows']['wix']['upgradeCode'])
    assert properties['ProductName'] == config['productName']
    assert properties['ProductVersion'] == config['version']
    assert properties['ProductLanguage'] == '2052' and properties['ALLUSERS'] == '1'
    assert UUID(properties['UpgradeCode']) == upgrade
    assert {UUID(row[0]) for row in tables['upgrade']} == {upgrade}
    assert ['RemoveExistingProducts'] in tables['execute']
    assert properties['WIXUI_INSTALLDIR'] == 'INSTALLDIR' and ['InstallDirDlg'] in tables['dialogs']
    assert ['Folder', 'PathEdit'] in tables['dir_controls'] and ['ChangeFolder', 'PushButton'] in tables['dir_controls']

    directories = dict(tables['directories'])
    launch_dirs = {directory for directory, target, workdir in tables['shortcuts']
                   if target == '[!Path]' and workdir == 'INSTALLDIR'}
    assert 'DesktopFolder' in launch_dirs
    assert any(directories[folder] == 'ProgramMenuFolder' for folder in launch_dirs)
    assert {row[0] for row in tables['delete']} <= launch_dirs | {'INSTALLDIR'}
    return {'product': properties['ProductName'], 'version': properties['ProductVersion'],
            'product_code': properties['ProductCode'], 'upgrade_code': str(upgrade),
            'install_folder_dialog': 'passed', 'shortcut_metadata': 'passed'}


def extract(package, folder):
    target = folder / '自选 MSI 测试路径'
    target.mkdir()
    msiexec = Path(os.environ['SystemRoot']) / 'System32/msiexec.exe'
    log = folder / 'msiexec.log'
    result = subprocess.run(
        [str(msiexec), '/a', str(package), f'TARGETDIR={target}', '/qn', '/norestart', '/L*v', str(log)],
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=240,
    )
    if result.returncode:
        raise RuntimeError(f'MSI 管理提取返回 {result.returncode}，详情见 {log}')
    apps = list(target.rglob('job-search-desktop.exe'))
    assert len(apps) == 1, 'MSI 提取路径内应包含唯一的主程序'
    return apps[0]


def extract_with_wix(package, folder, dark):
    bundle = folder / 'cabinet'
    layout = folder / 'layout.wxs'
    with (folder / 'dark.log').open('wb') as log:
        subprocess.run(
            [str(dark), '-nologo', '-x', str(bundle), '-o', str(layout), str(package)],
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=120, check=True,
        )
    namespace = '{http://schemas.microsoft.com/wix/2006/wi}'
    root = ElementTree.parse(layout).find(f".//{namespace}Directory[@Id='INSTALLDIR']")
    assert root is not None, 'WiX 文件结构缺少应用目录'
    target = folder / '自选 MSI 测试路径'

    def copy_files(node, directory):
        assert directory.resolve().is_relative_to(target.resolve()), '无效的应用目录'
        directory.mkdir(parents=True, exist_ok=True)
        for file in node.findall(f'{namespace}Component/{namespace}File'):
            source = Path(file.attrib['Source']).resolve(strict=True)
            output = (directory / file.attrib['Name']).resolve()
            assert source.is_relative_to(bundle.resolve()) and output.parent == directory.resolve(), '无效的应用文件路径'
            shutil.copy2(source, output)
        for child in node.findall(f'{namespace}Directory'):
            copy_files(child, directory / child.attrib['Name'])

    copy_files(root, target)
    return target / 'job-search-desktop.exe'


def check_payload(app):
    manifest = json.loads(app.with_name('resource-manifest.json').read_text(encoding='utf-8'))
    assert manifest['files'], '资源清单为空'
    for item in manifest['files']:
        relative = Path(item['path'])
        prefix = 'runtime' if relative.parts[0] in {'worker', 'codex', 'agent-browser'} else ''
        path = (app.parent / prefix / relative).resolve()
        assert path.is_relative_to(app.parent.resolve()), f'资源超出应用目录 {relative}'
        assert path.stat().st_size == item['size'], f'资源大小不符 {relative}'
        with path.open('rb') as stream:
            digest = sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        assert digest.hexdigest() == item['sha256'], f'资源校验失败 {relative}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--msi', type=Path, required=True)
    parser.add_argument('--inspect-only', action='store_true', help='仅校验 MSI 数据表')
    parser.add_argument('--wix-dark', type=Path, help='直接提取 CAB，适用于 Windows Installer 服务受限的环境')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('需要 Windows')
    package = args.msi.resolve(strict=True)
    config = json.loads((ROOT / 'apps/desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    report = inspect_msi(package, config)
    if not args.inspect_only:
        from desktop_configuration_smoke import desktop_page
        from playwright.sync_api import expect, sync_playwright

        folder = ROOT / '.verification/desktop-release' / ('msi-' + uuid4().hex[:8])
        folder.mkdir(parents=True)
        app = extract_with_wix(package, folder, args.wix_dark.resolve(strict=True)) if args.wix_dark else extract(package, folder)
        check_payload(app)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('JOB_ASSISTANT_', 'PYTHON', 'CARGO_', 'RUSTUP_'))}
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        with sync_playwright() as playwright, desktop_page(playwright, app, folder, env) as (page, _):
            expect(page.locator('.brand')).to_have_attribute('title', f"求职助手 {config['version']}")
            expect(page.get_by_text('暂无进行中或暂停的投递任务', exact=True)).to_be_visible()
            runtime = page.evaluate("window.__TAURI_INTERNALS__.invoke('browser_runtime_status')")
            assert runtime['installed'] and not runtime['connected']
            assert Path(runtime['executable']).samefile(app.parent / 'runtime/agent-browser/agent-browser.exe')
        report.update({'extraction': 'wix_cabinet' if args.wix_dark else 'msiexec_admin',
                       'extracted_executable': str(app), 'resource_hashes': 'passed', 'embedded_ui_and_worker': 'passed'})
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
