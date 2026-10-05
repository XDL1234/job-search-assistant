param([string]$Destination = "", [string]$DataRoot = "")
$ErrorActionPreference = "Stop"
$entry = Join-Path $PSScriptRoot "skills/job-search-assistant/scripts/run.py"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = "python"
    $pythonArgs = @("-X", "utf8")
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = "py"
    $pythonArgs = @("-3", "-X", "utf8")
} else {
    throw "需要 Python 3.11+。请先从 python.org 安装，再运行本安装器。"
}
$arguments = @($entry, "install")
if ($Destination) { $arguments += @("--destination", $Destination) }
& $pythonCommand @pythonArgs @arguments
if ($LASTEXITCODE -ne 0) { throw "Skill 安装未完成；已有不同版本不会被覆盖。" }
$arguments = @($entry)
if ($DataRoot) { $arguments += @("--root", $DataRoot) }
$arguments += @("bootstrap", "--install")
& $pythonCommand @pythonArgs @arguments
if ($LASTEXITCODE -ne 0) { throw "依赖初始化未完成；可修复提示的问题后重试。" }
Write-Output "安装完成。下一轮在 Codex 调用 `$job-search-assistant；若未出现，重新打开会话。"
