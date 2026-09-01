[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
if ($args.Count -lt 1) {
    throw "utf8_invoke.ps1 requires a script path"
}
$script = [string]$args[0]
$rest = @()
if ($args.Count -gt 1) {
    $rest = $args[1..($args.Count - 1)]
}
& $script @rest
exit $LASTEXITCODE
