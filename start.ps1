# start.ps1 — 从源码一键启动 vision-relay 服务（Windows PowerShell）
# 用法:  powershell -ExecutionPolicy Bypass -File .\start.ps1
# 效果:  自动建 venv(若缺失) -> 安装本项目(-e) -> vision-relay start 前台常驻
#        服务运行中按 Ctrl+C 停止（等价于关闭服务，也会回滚接线）
$ErrorActionPreference = "Stop"

# 定位脚本所在目录，并切过去（保证双击 / 任意 cwd 下都能跑）
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "✗ $msg" -ForegroundColor Red
    exit 1
}

# 1) 找 Python（优先 py 启动器，其次 python；Windows 上通常没有 python3）
$hasPy = $null -ne (Get-Command py -ErrorAction SilentlyContinue)

# 2) venv 不存在则创建（.venv 已在 .gitignore 里，不入库）
if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..." -ForegroundColor Cyan
    if ($hasPy) { py -3 -m venv .venv } else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Fail "创建 .venv 失败，请确认已安装 Python 3.10+ 并勾选 Add to PATH" }
}
$pyExe = Join-Path $root ".venv\Scripts\python.exe"
$relayExe = Join-Path $root ".venv\Scripts\vision-relay.exe"

# 3) 以可编辑模式安装本项目（含 httpx 依赖）；幂等，重复跑很快
Write-Host "安装本项目依赖 ..." -ForegroundColor Cyan
& $pyExe -m pip install -e . --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install -e . 失败，请检查网络或依赖" }

# 4) 前置校验：没有 proxy.json 时给出引导而不是报错崩溃
$cfg = Join-Path $env:USERPROFILE ".vision-relay\proxy.json"
if (-not (Test-Path $cfg)) {
    Fail "未找到配置文件 $cfg  — 首次使用请先按 README「三步开工」第 1 步创建它（填入你的 VLM / 上游 key）"
}

# 5) 前台启动服务；Ctrl+C 退出后自动收尾
Write-Host "启动 vision-relay ..." -ForegroundColor Cyan
Write-Host "（服务运行中，按 Ctrl+C 停止）" -ForegroundColor DarkGray
& $relayExe start
exit $LASTEXITCODE
