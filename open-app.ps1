$ErrorActionPreference = "Stop"
Push-Location "$PSScriptRoot"
try {
    python -X utf8 "./tools/desktop_dev.py" dev
    if ($LASTEXITCODE -ne 0) { throw "桌面应用启动失败，请查看上方错误。" }
} finally {
    Pop-Location
}
