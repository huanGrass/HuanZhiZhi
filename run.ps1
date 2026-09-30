$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw '请先按 README 创建 .venv 并安装项目。'
}
Start-Process -FilePath $taskPython -ArgumentList '-m', 'huanzhizhi' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
