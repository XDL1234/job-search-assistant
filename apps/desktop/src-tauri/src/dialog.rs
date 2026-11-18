//! 系统文件选择器只接收预定义类型，不拼接用户输入。
use std::{env, path::PathBuf};
use tokio::process::Command;

#[tauri::command]
pub async fn pick_file(kind: String) -> Result<Option<String>, String> {
    let filter = match kind.as_str() {
        "profile" => "资料文件 (*.pdf;*.docx;*.json)|*.pdf;*.docx;*.json",
        "resume" => "简历 (*.pdf;*.docx)|*.pdf;*.docx",
        "companies" => "公司表 (*.xlsx;*.csv)|*.xlsx;*.csv",
        _ => return Err("未知文件类型".into()),
    };
    let script = format!("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Add-Type -AssemblyName System.Windows.Forms; $dialog=New-Object System.Windows.Forms.OpenFileDialog; $dialog.Filter='{filter}'; $dialog.CheckFileExists=$true; $owner=New-Object System.Windows.Forms.Form; $owner.TopMost=$true; try {{ if ($dialog.ShowDialog($owner) -eq 'OK') {{ [Console]::Write($dialog.FileName) }} }} finally {{ $dialog.Dispose(); $owner.Dispose() }}");
    select_path(&script, "系统文件选择器未能打开，请直接填写文件路径").await
}

pub async fn choose_export_path() -> Result<Option<String>, String> {
    select_path(r#"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.SaveFileDialog
$dialog.Title = '导出投递记录'
$dialog.Filter = 'Excel 工作簿 (*.xlsx)|*.xlsx'
$dialog.DefaultExt = 'xlsx'
$dialog.AddExtension = $true
$dialog.CheckPathExists = $true
$dialog.OverwritePrompt = $true
$dialog.FileName = '投递记录.xlsx'
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
try {
    if ($dialog.ShowDialog($owner) -eq 'OK') { [Console]::Write($dialog.FileName) }
} finally {
    $dialog.Dispose()
    $owner.Dispose()
}
"#, "无法打开 Excel 保存对话框").await
}

async fn select_path(script: &str, failure: &str) -> Result<Option<String>, String> {
    let powershell = env::var_os("SystemRoot").map_or_else(
        || PathBuf::from("powershell.exe"),
        |root| PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe"),
    );
    let mut command = Command::new(powershell);
    command
        .args(["-NoProfile", "-STA", "-Command", script])
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let output = command.output().await.map_err(|e| format!("{failure}：{e}"))?;
    if !output.status.success() {
        return Err(failure.into());
    }
    let value = String::from_utf8(output.stdout).map_err(|_| "文件路径编码错误")?;
    Ok((!value.is_empty()).then_some(value))
}
