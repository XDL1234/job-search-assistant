//! 系统文件选择器只接收预定义类型，不拼接用户输入。
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
    let mut command = Command::new("powershell.exe");
    command
        .args(["-NoProfile", "-STA", "-Command", &script])
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let output = command.output().await.map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err("系统文件选择器未能打开，请直接填写文件路径".into());
    }
    let value = String::from_utf8(output.stdout).map_err(|_| "文件路径编码错误")?;
    Ok((!value.trim().is_empty()).then(|| value.trim().to_owned()))
}
