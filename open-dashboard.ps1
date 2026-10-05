param([string]$DataRoot = "")
$ErrorActionPreference = "Stop"
$entry = Join-Path $PSScriptRoot "skills/job-search-assistant/scripts/run.py"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = "python"
    $pythonArgs = @("-X", "utf8")
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = "py"
    $pythonArgs = @("-3", "-X", "utf8")
} else {
    throw "Python 3.11+ is required."
}
$arguments = @($entry)
if ($DataRoot) { $arguments += @("--root", $DataRoot) }
$arguments += @("bootstrap", "--install")
$result = (& $pythonCommand @pythonArgs @arguments) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $result.ok -or -not $result.result.ready) {
    throw "Runtime setup failed."
}
# 保持本终端运行面板。Ctrl+C 关闭服务；面板不启动求职执行器。
& $result.result.python -X utf8 $entry --root $result.result.root dashboard --open
exit $LASTEXITCODE
