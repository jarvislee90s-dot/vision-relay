# stop.ps1 — 从源码一键停止 vision-relay 服务（Windows PowerShell）
# 用法:  powershell -ExecutionPolicy Bypass -File .\stop.ps1
# 效果:  按 PID 文件终止服务进程，并回滚三个 harness 的 base_url 接线
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pyExe = Join-Path $root ".venv\Scripts\python.exe"
$relayExe = Join-Path $root ".venv\Scripts\vision-relay.exe"

# 从未构建过 venv 时，直接提示而不是空报错
if (-not (Test-Path $relayExe)) {
    Write-Host "尚未构建 .venv —— 服务可能没启动过，或请先运行 .\start.ps1。" -ForegroundColor Yellow
    exit 1
}

& $relayExe stop
exit $LASTEXITCODE
