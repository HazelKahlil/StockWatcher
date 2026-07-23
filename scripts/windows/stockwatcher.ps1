[CmdletBinding()]
param(
    [ValidateSet("Menu", "Setup", "Preflight", "Run", "Probe", "Build")]
    [string]$Action = "Menu",
    [string]$TdxInstallPath = "",
    [string]$Endpoint = "http://127.0.0.1:17709/"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$RuntimeRoot = Join-Path $env:LOCALAPPDATA "StockWatcher"
$ReportRoot = Join-Path $RuntimeRoot "reports"

function Write-Title([string]$Text) {
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Resolve-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3.12")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "未找到 Python。请从 python.org 安装 64 位 Python 3.11—3.14，并勾选加入 PATH。"
}

function Invoke-Setup {
    Write-Title "安装或更新 StockWatcher"
    if (-not (Test-Path $PythonPath)) {
        $launcher = Resolve-PythonLauncher
        $launcherArgs = @($launcher | Select-Object -Skip 1)
        & $launcher[0] @launcherArgs -m venv $VenvPath
    }
    & $PythonPath -m pip install --upgrade pip
    & $PythonPath -m pip install --upgrade -e "$ProjectRoot"
    Write-Host "StockWatcher 环境已就绪。" -ForegroundColor Green
}

function Ensure-Environment {
    if (-not (Test-Path $PythonPath)) {
        Invoke-Setup
    }
    New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
}

function Invoke-Preflight {
    Write-Title "检查 Windows / 通达信 / TQ 服务"
    Ensure-Environment
    $arguments = @(
        "-m", "stock_watcher.providers.tdxquant_preflight",
        "--endpoint", $Endpoint,
        "--output", (Join-Path $ReportRoot "tdxquant-preflight.json")
    )
    if ($TdxInstallPath) {
        $arguments += @("--terminal-path", $TdxInstallPath)
    }
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "预检未通过。请先安装并登录官方免费 64 位“金融终端（量化模拟）”，再确认 TQ 服务已开启。" -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
}

function Invoke-Run {
    Write-Title "启动 StockWatcher"
    Ensure-Environment
    & $PythonPath -m stock_watcher.ui.app --provider tdxquant --endpoint $Endpoint
}

function Invoke-Probe {
    Write-Title "执行脱敏 M0 探针"
    Ensure-Environment
    & $PythonPath -m stock_watcher.providers.tdxquant_m0 --endpoint $Endpoint --output $ReportRoot
    Write-Host "报告目录：$ReportRoot"
}

function Invoke-Build {
    Write-Title "构建 Windows 分发包"
    Ensure-Environment
    & $PythonPath -m pip install --upgrade "pyinstaller>=6,<7"
    Push-Location $ProjectRoot
    try {
        & $PythonPath -m PyInstaller --noconfirm "packaging\stockwatcher.spec"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($iscc) {
            & $iscc.Source "packaging\windows\StockWatcher.iss"
        } else {
            Write-Host "未找到 Inno Setup；已生成 dist\StockWatcher 文件夹，安装器步骤暂跳过。" -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
    }
}

if ($Action -eq "Menu") {
    Write-Title "StockWatcher Windows 一键入口"
    Write-Host "1. 安装/更新"
    Write-Host "2. 通达信预检"
    Write-Host "3. 启动应用"
    Write-Host "4. 执行 M0 探针"
    Write-Host "5. 构建分发包"
    $choice = Read-Host "请选择 1-5"
    $Action = @{"1"="Setup"; "2"="Preflight"; "3"="Run"; "4"="Probe"; "5"="Build"}[$choice]
    if (-not $Action) { throw "无效选项。" }
}

switch ($Action) {
    "Setup" { Invoke-Setup }
    "Preflight" { Invoke-Preflight }
    "Run" { Invoke-Run }
    "Probe" { Invoke-Probe }
    "Build" { Invoke-Build }
}
