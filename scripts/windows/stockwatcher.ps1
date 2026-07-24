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

function Write-StrictUtf8Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false, $true
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-FallbackPreflightReport([string]$Path) {
    $report = [ordered]@{
        status = "FAIL"
        platform = "Windows"
        python_version = "unknown"
        endpoint = "http://127.0.0.1:17709/"
        checks = @(
            [ordered]@{
                name = "api_session"
                status = "FAIL"
                message = "TdxQuant 返回了无法识别的数据，已安全停止候选输出。"
                reason = "invalid_response"
            }
        )
        fund_module = "unavailable"
        windows_live_verified = $false
    }
    Write-StrictUtf8Text -Path $Path -Content ($report | ConvertTo-Json -Depth 6)
}

function Read-ValidPreflightReport([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "预检报告未生成。"
    }
    $encoding = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false, $true
    $raw = $encoding.GetString([System.IO.File]::ReadAllBytes($Path))
    if ($raw -match "(?i)(ErrorMsg|ErrorInfo|token|password|account|username|hostname|computername|HKEY_|[A-Z]:\\Users\\)") {
        throw "预检报告包含禁止字段。"
    }
    try {
        $report = $raw | ConvertFrom-Json
    } catch {
        throw "预检报告不是严格 UTF-8 JSON。"
    }
    $reportFields = @("status", "platform", "python_version", "endpoint", "checks", "fund_module", "windows_live_verified")
    $actualReportFields = @($report.PSObject.Properties.Name)
    if ($actualReportFields.Count -ne $reportFields.Count) {
        throw "预检报告 schema 非固定结构。"
    }
    foreach ($field in $reportFields) {
        if ($field -notin $actualReportFields) {
            throw "预检报告缺少固定字段。"
        }
    }
    if ($report.status -notin @("PASS", "WARN", "FAIL")) {
        throw "预检报告终态非法。"
    }
    if ($report.endpoint -ne "http://127.0.0.1:17709/" -or $report.fund_module -ne "unavailable") {
        throw "预检报告固定边界非法。"
    }
    if ($report.platform -notin @("Windows", "non-Windows") -or $report.python_version -notmatch "^(unknown|\d+\.\d+\.\d+)$") {
        throw "预检报告环境字段非法。"
    }
    if (-not ($report.windows_live_verified -is [bool]) -or @($report.checks).Count -eq 0) {
        throw "预检报告验证字段非法。"
    }
    $allowedNames = @("operating_system", "python", "terminal_install", "python_client", "tq_service", "api_session")
    $allowedReasons = @(
        "dependency_missing", "terminal_not_installed", "terminal_not_running",
        "not_logged_in", "service_unreachable", "method_unavailable",
        "field_unavailable", "timeout", "non_trading_session", "data_stale",
        "data_interrupted", "user_paused", "invalid_response"
    )
    $allowedMessages = @(
        "Windows 环境已就绪。",
        "当前不是 Windows；只能验证离线契约，不能证明 TdxQuant 真机可用。",
        "未指定终端路径；请用 -TdxInstallPath 指向官方金融终端安装目录。",
        "已找到指定的官方终端目录。",
        "已发现官方 tqcenter Python 客户端。",
        "未发现 tqcenter；可继续使用官方 127.0.0.1:17709 HTTP 模式。",
        "TQ 本机端口可达。",
        "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。",
        "未找到可选的 tqcenter Python 客户端，请改用本机 HTTP 模式或安装官方组件。",
        "未找到官方通达信金融终端，请先安装免费的 64 位“金融终端（量化模拟）”。",
        "通达信终端尚未启动，请先启动终端并保持运行。",
        "通达信终端尚未登录或行情权限未就绪，请在官方终端内完成登录。",
        "TQ 本机服务不可达，请确认终端支持 TQ，且 127.0.0.1:17709 已启动。",
        "当前终端未提供所需的官方 TdxQuant 接口，请检查终端版本与权限。",
        "接口未返回所需字段；该能力保持未就绪，不会用替代字段冒充。",
        "TQ 本机服务响应超时，请确认终端行情已连接后重试。",
        "当前不在 A 股连续交易时段；可执行预检，但不会产生新候选。",
        "行情已过期，系统已停止产生新候选。",
        "行情数据中断，系统已停止产生新候选。",
        "用户已暂停实时观察；恢复前不会产生新候选。",
        "TdxQuant 返回了无法识别的数据，已安全停止候选输出。"
    )
    $seenNames = @{}
    foreach ($check in @($report.checks)) {
        $checkFields = @("name", "status", "message", "reason")
        $actualCheckFields = @($check.PSObject.Properties.Name)
        if ($actualCheckFields.Count -ne $checkFields.Count) {
            throw "预检报告检查项 schema 非固定结构。"
        }
        foreach ($field in $checkFields) {
            if ($field -notin $actualCheckFields) {
                throw "预检报告检查项缺少固定字段。"
            }
        }
        if ($check.name -notin $allowedNames -or $check.status -notin @("PASS", "WARN", "FAIL")) {
            throw "预检报告检查项非法。"
        }
        if (-not ($check.message -is [string]) -or -not $check.message) {
            throw "预检报告检查消息非法。"
        }
        $pythonMessage = $check.name -eq "python" -and $check.message -match "^Python \d+\.\d+\.\d+（项目要求 3\.11 或 3\.12）。$"
        if (-not $pythonMessage -and $check.message -notin $allowedMessages) {
            throw "预检报告检查消息不在固定集合中。"
        }
        if ($null -ne $check.reason -and $check.reason -notin $allowedReasons) {
            throw "预检报告 reason 非法。"
        }
        if ($seenNames.ContainsKey($check.name)) {
            throw "预检报告包含重复检查项。"
        }
        $seenNames[$check.name] = $true
    }
    $apiPasses = @($report.checks | Where-Object { $_.name -eq "api_session" -and $_.status -eq "PASS" })
    if ($report.status -eq "PASS" -and $apiPasses.Count -ne 1) {
        throw "预检报告违反 api_session PASS 不变量。"
    }
    if ($report.windows_live_verified -eq $true -and $apiPasses.Count -ne 1) {
        throw "预检报告伪造 Windows live 验证。"
    }
    return $report
}

function Publish-PreflightReport {
    param(
        [Parameter(Mandatory = $true)][string]$AttemptPath,
        [Parameter(Mandatory = $true)][string]$ReportPath
    )
    $null = Read-ValidPreflightReport -Path $AttemptPath
    Move-Item -LiteralPath $AttemptPath -Destination $ReportPath -Force
    $validated = Read-ValidPreflightReport -Path $ReportPath
    return $validated
}

function Get-AvailableBuildDriveName {
    $substMappings = @(& subst.exe 2>$null)
    foreach ($letter in @("Z", "Y", "X", "W", "V", "U", "T")) {
        $driveName = "${letter}:"
        $alreadySubstituted = @($substMappings | Where-Object { $_ -match "^$letter`:\\" }).Count -gt 0
        if (-not $alreadySubstituted -and -not (Get-PSDrive -Name $letter -ErrorAction SilentlyContinue)) {
            return $driveName
        }
    }
    throw "没有可用于安全短路径构建的临时盘符。"
}

function Assert-IsccPathBudget {
    param(
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$InstallerScript,
        [int]$MaximumLength = 240
    )
    $paths = @($BundleRoot, $InstallerScript)
    $paths += @(Get-ChildItem -LiteralPath $BundleRoot -Recurse -File | ForEach-Object { $_.FullName })
    $longest = ($paths | ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum
    if ($null -eq $longest -or $longest -gt $MaximumLength) {
        throw "Inno Setup 输入路径超过保守预算（最大 $MaximumLength，实测 $longest）。"
    }
}

function Publish-BuildArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$RunId
    )
    $destinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    $temporary = Join-Path $destinationDirectory (".haz447-$RunId-" + (Split-Path -Leaf $Destination) + ".tmp")
    try {
        Copy-Item -LiteralPath $Source -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $Destination -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
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
    New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
    $reportPath = Join-Path $ReportRoot "tdxquant-preflight.json"
    $attemptPath = Join-Path $ReportRoot ("tdxquant-preflight.haz447-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $exitCode = 1
    try {
        try {
            Ensure-Environment
            $arguments = @(
                "-m", "stock_watcher.providers.tdxquant_preflight",
                "--endpoint", $Endpoint,
                "--output", $attemptPath
            )
            if ($TdxInstallPath) {
                $arguments += @("--terminal-path", $TdxInstallPath)
            }
            & $PythonPath @arguments
            $exitCode = $LASTEXITCODE
            $null = Read-ValidPreflightReport -Path $attemptPath
        } catch {
            Write-FallbackPreflightReport -Path $attemptPath
            $exitCode = 1
        }
        $report = Publish-PreflightReport -AttemptPath $attemptPath -ReportPath $reportPath
        if ($report.status -eq "FAIL" -and $exitCode -eq 0) {
            $exitCode = 1
        }
        if ($exitCode -ne 0) {
            throw "预检未通过；报告已安全写出，请先安装并登录官方免费 64 位“金融终端（量化模拟）”，再确认 TQ 服务已开启（退出码 $exitCode）。"
        }
    } finally {
        if (Test-Path -LiteralPath $attemptPath) {
            Remove-Item -LiteralPath $attemptPath -Force
        }
    }
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
    $driveName = $null
    $driveMapped = $false
    $stageRoot = $null
    $runId = [Guid]::NewGuid().ToString("N")
    try {
        $driveName = Get-AvailableBuildDriveName
        Invoke-CheckedNative -Command "subst.exe" -Arguments @($driveName, $ProjectRoot) -FailureMessage "创建短路径构建盘失败"
        $driveMapped = $true
        $mappedRoot = "$driveName\"
        $stageId = $runId.Substring(0, 12)
        $stageParent = Join-Path $mappedRoot ".swb"
        $stageRoot = Join-Path $stageParent "h447-$stageId"
        $stageDist = Join-Path $stageRoot "dist"
        $stageWork = Join-Path $stageRoot "work"
        $stageInstaller = Join-Path $stageRoot "installer"
        New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
        $specPath = Join-Path $mappedRoot "packaging\stockwatcher.spec"
        Invoke-CheckedNative -Command $PythonPath -Arguments @(
            "-m", "PyInstaller", "--noconfirm",
            "--distpath", $stageDist,
            "--workpath", $stageWork,
            $specPath
        ) -FailureMessage "PyInstaller 构建失败"
        $bundleRoot = Join-Path $stageDist "StockWatcher"
        if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot "StockWatcher.exe") -PathType Leaf)) {
            throw "PyInstaller 未生成完整的 StockWatcher bundle。"
        }
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($iscc) {
            $installerScript = Join-Path $mappedRoot "packaging\windows\StockWatcher.iss"
            Assert-IsccPathBudget -BundleRoot $bundleRoot -InstallerScript $installerScript
            New-Item -ItemType Directory -Force -Path $stageInstaller | Out-Null
            Invoke-CheckedNative -Command $iscc.Source -Arguments @(
                "/DStockWatcherBundleDir=$bundleRoot",
                "/DStockWatcherOutputDir=$stageInstaller",
                $installerScript
            ) -FailureMessage "Inno Setup 安装器编译失败"
        } else {
            throw "未找到 Inno Setup（ISCC.exe）；无法完成 Windows 安装器编译。"
        }
        $installer = Join-Path $stageInstaller "StockWatcher-0.3.0-alpha-setup.exe"
        if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
            throw "Inno Setup 未生成安装器。"
        }
        $portable = Join-Path $stageRoot "StockWatcher-0.3.0-alpha-portable.zip"
        Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $portable -CompressionLevel Optimal
        Publish-BuildArtifact -Source $installer -Destination (Join-Path $ProjectRoot "dist\installer\StockWatcher-0.3.0-alpha-setup.exe") -RunId $runId
        Publish-BuildArtifact -Source $portable -Destination (Join-Path $ProjectRoot "dist\StockWatcher-0.3.0-alpha-portable.zip") -RunId $runId
        Write-Host "安装器与 portable ZIP 已发布到 dist；短路径 staging 已清理。" -ForegroundColor Green
    } finally {
        if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) {
            $expectedParent = Join-Path "$driveName\" ".swb"
            if ((Split-Path -Parent $stageRoot) -eq $expectedParent -and (Split-Path -Leaf $stageRoot) -eq "h447-$stageId") {
                Remove-Item -LiteralPath $stageRoot -Recurse -Force
            }
        }
        if ($driveMapped) {
            Invoke-CheckedNative -Command "subst.exe" -Arguments @($driveName, "/D") -FailureMessage "清理短路径构建盘失败"
        }
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
