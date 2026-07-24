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
    $candidates = @(
        [PSCustomObject]@{ Command = "python"; Arguments = @() },
        [PSCustomObject]@{ Command = "py"; Arguments = @("-3.12") },
        [PSCustomObject]@{ Command = "py"; Arguments = @("-3.11") }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
            continue
        }
        & $candidate.Command @($candidate.Arguments) -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return $candidate
        }
    }
    throw "未找到受支持的 Python。请从 python.org 安装 64 位 Python 3.11 或 3.12，并勾选加入 PATH。"
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    & $Command @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$FailureMessage（退出码 $exitCode）。"
    }
}

function Invoke-Setup {
    Write-Title "安装或更新 StockWatcher"
    if (-not (Test-Path $PythonPath)) {
        $launcher = Resolve-PythonLauncher
        $arguments = @($launcher.Arguments) + @("-m", "venv", $VenvPath)
        Invoke-CheckedNative -Command $launcher.Command -Arguments $arguments -FailureMessage "创建 Python 环境失败"
    }
    Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "pip", "install", "--upgrade", "pip") -FailureMessage "升级 pip 失败"
    Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "pip", "install", "--upgrade", "-e", $ProjectRoot) -FailureMessage "安装 StockWatcher 失败"
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
    Invoke-CheckedNative -Command $PythonPath -Arguments $arguments -FailureMessage "预检未通过；请先安装并登录官方免费 64 位“金融终端（量化模拟）”，再确认 TQ 服务已开启"
}

function Invoke-Run {
    Write-Title "启动 StockWatcher"
    Ensure-Environment
    Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "stock_watcher.ui.app", "--provider", "tdxquant", "--endpoint", $Endpoint) -FailureMessage "StockWatcher 启动失败"
}

function Invoke-Probe {
    Write-Title "执行脱敏 M0 探针"
    Ensure-Environment
    Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "stock_watcher.providers.tdxquant_m0", "--endpoint", $Endpoint, "--output", $ReportRoot) -FailureMessage "M0 探针失败"
    Write-Host "报告目录：$ReportRoot"
}

function Invoke-Build {
    Write-Title "构建 Windows 分发包"
    Ensure-Environment
    Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "pip", "install", "--upgrade", "pyinstaller>=6,<7") -FailureMessage "安装 PyInstaller 失败"
    Push-Location $ProjectRoot
    try {
        Invoke-CheckedNative -Command $PythonPath -Arguments @("-m", "PyInstaller", "--noconfirm", "packaging\stockwatcher.spec") -FailureMessage "PyInstaller 构建失败"
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($iscc) {
            Invoke-CheckedNative -Command $iscc.Source -Arguments @("packaging\windows\StockWatcher.iss") -FailureMessage "Inno Setup 安装器编译失败"
        } else {
            throw "未找到 Inno Setup（ISCC.exe）；无法完成 Windows 安装器编译。"
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

try {
    switch ($Action) {
        "Setup" { Invoke-Setup }
        "Preflight" { Invoke-Preflight }
        "Run" { Invoke-Run }
        "Probe" { Invoke-Probe }
        "Build" { Invoke-Build }
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
