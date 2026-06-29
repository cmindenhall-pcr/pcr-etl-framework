param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe,
    [switch]$ProfileOnly,
    [switch]$All = $true,
    [string]$PipelineName,
    [switch]$PreflightOnly,
    [int]$ConnectTimeoutSeconds = 5,
    [int]$MaxParallelPipelines = 1
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$preflightScript = Join-Path $RepoRoot 'scripts\run_pipeline_with_preflight.ps1'
if (-not (Test-Path $preflightScript)) {
    throw "Preflight script not found: $preflightScript"
}

$argList = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $preflightScript),
    '-RepoRoot', ('"{0}"' -f $RepoRoot),
    '-AutoRestartSqlServer'
)

if ($PythonExe) {
    $argList += @('-PythonExe', ('"{0}"' -f $PythonExe))
}
if ($ProfileOnly) {
    $argList += '-ProfileOnly'
}
if ($All) {
    $argList += '-All'
}
if ($PipelineName) {
    $argList += @('-PipelineName', ('"{0}"' -f $PipelineName))
}
if ($PreflightOnly) {
    $argList += '-PreflightOnly'
}
if ($ConnectTimeoutSeconds) {
    $argList += @('-ConnectTimeoutSeconds', $ConnectTimeoutSeconds)
}
if ($MaxParallelPipelines -gt 1) {
    $argList += @('-MaxParallelPipelines', $MaxParallelPipelines)
}

if (-not (Test-IsAdministrator)) {
    Write-Host 'Relaunching as Administrator so SQL Server can be restarted automatically if needed...'
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList | Out-Null
    exit 0
}

Write-Host 'Running preflight and pipeline from an elevated PowerShell session...'
& powershell.exe @argList
exit $LASTEXITCODE

