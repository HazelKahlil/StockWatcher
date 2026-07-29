param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "PublishSuccess", "PublishFailure")]
    [string]$Mode,
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$FixturePath = "",
    [int]$ChildExitCode = 0,
    [string]$ExpectedTerminalPath = "",
    [string]$ShortRoot = ""
)

$ErrorActionPreference = "Stop"
$env:LOCALAPPDATA = $WorkRoot
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $projectRoot "scripts\windows\stockwatcher.ps1") -LoadFunctionsOnly

if ($Mode -eq "Preflight") {
    $script:ReportRoot = Join-Path $WorkRoot "reports with spaces"
    $script:TdxInstallPath = $ExpectedTerminalPath
    $script:ArgumentsPreserved = $false
    New-Item -ItemType Directory -Force -Path $script:ReportRoot | Out-Null
    Write-FallbackPreflightReport -Path (Join-Path $script:ReportRoot "tdxquant-preflight.json")

    function Ensure-Environment {
        New-Item -ItemType Directory -Force -Path $script:ReportRoot | Out-Null
    }

    function Invoke-PreflightProcess([string[]]$Arguments) {
        $outputIndex = [Array]::IndexOf($Arguments, "--output")
        $terminalIndex = [Array]::IndexOf($Arguments, "--terminal-path")
        if ($outputIndex -lt 0) {
            throw "missing output argument"
        }
        if (
            $ExpectedTerminalPath -and
            ($terminalIndex -lt 0 -or $Arguments[$terminalIndex + 1] -ne $ExpectedTerminalPath)
        ) {
            throw "terminal argument was not preserved"
        }
        $script:ArgumentsPreserved = $true
        if ($FixturePath -eq "START_FAILURE") {
            throw "synthetic start failure"
        }
        if ($FixturePath -and $FixturePath -ne "MISSING") {
            [System.IO.File]::WriteAllBytes(
                $Arguments[$outputIndex + 1],
                [System.IO.File]::ReadAllBytes($FixturePath)
            )
        }
        return $ChildExitCode
    }

    $threw = $false
    $caughtMessage = $null
    try {
        Invoke-Preflight
    } catch {
        $threw = $true
        $caughtMessage = $_.Exception.Message
    }
    $reportPath = Join-Path $script:ReportRoot "tdxquant-preflight.json"
    $report = $null
    if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    $temporaryCount = @(
        Get-ChildItem -LiteralPath $script:ReportRoot -Filter "tdxquant-preflight*.tmp" -ErrorAction SilentlyContinue
    ).Count
    [ordered]@{
        threw = $threw
        caught_message = $caughtMessage
        arguments_preserved = $script:ArgumentsPreserved
        report_exists = Test-Path -LiteralPath $reportPath -PathType Leaf
        temporary_count = $temporaryCount
        report = $report
    } | ConvertTo-Json -Depth 8 -Compress
    exit 0
}

$sourceRoot = Join-Path $ShortRoot ".swb\source"
$transactionParent = Join-Path $ShortRoot ".swb"
$installerSource = Join-Path $sourceRoot "StockWatcher-setup.exe"
$portableSource = Join-Path $sourceRoot "StockWatcher-portable.zip"
$installerDestination = Join-Path $ShortRoot "dist\installer\StockWatcher-setup.exe"
$portableDestination = Join-Path $ShortRoot "dist\StockWatcher-portable.zip"
$artifacts = @(
    [PSCustomObject]@{ Source = $installerSource; Destination = $installerDestination },
    [PSCustomObject]@{ Source = $portableSource; Destination = $portableDestination }
)
$threw = $false
try {
    $hook = $null
    if ($Mode -eq "PublishFailure") {
        $hook = {
            param($Index)
            if ($Index -eq 2) {
                throw "synthetic second publish failure"
            }
        }
    }
    Publish-BuildArtifactsTransaction `
        -Artifacts $artifacts `
        -TransactionParent $transactionParent `
        -RunId "1234567890abcdef1234567890abcdef" `
        -BeforePublishArtifact $hook
} catch {
    $threw = $true
}

[ordered]@{
    threw = $threw
    installer = Get-Content -LiteralPath $installerDestination -Raw
    portable = Get-Content -LiteralPath $portableDestination -Raw
    transaction_count = @(
        Get-ChildItem -LiteralPath $transactionParent -Filter "publish-h449-*" -ErrorAction SilentlyContinue
    ).Count
} | ConvertTo-Json -Compress
