param(
    [Parameter(Mandatory = $true)]
    [string]$PipelineName,
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe,
    [switch]$StagingOnly,
    [switch]$ZenOnly
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $RepoRoot)) {
    throw "Repo root not found: $RepoRoot"
}

if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$resolvedConfigPath = if ([System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
}
else {
    Join-Path $RepoRoot $ConfigPath
}

if (-not (Test-Path $resolvedConfigPath)) {
    throw "Pipeline config not found: $resolvedConfigPath"
}

$previousPipelineConfigPath = $env:PIPELINE_CONFIG_PATH
$previousSqlDatabase = $env:SQL_DATABASE

Push-Location $RepoRoot
try {
    $env:PIPELINE_CONFIG_PATH = $resolvedConfigPath
    $env:SQL_DATABASE = $DatabaseName

    Write-Host "Using pipeline config: $resolvedConfigPath"
    Write-Host "Using SQL database: $DatabaseName"

    & "scripts\run_pipeline_with_preflight.ps1" -RepoRoot $RepoRoot -PythonExe $PythonExe -All:$false -PipelineName $PipelineName -StagingOnly:$StagingOnly -ZenOnly:$ZenOnly
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousPipelineConfigPath) {
        Remove-Item Env:\PIPELINE_CONFIG_PATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PIPELINE_CONFIG_PATH = $previousPipelineConfigPath
    }

    if ($null -eq $previousSqlDatabase) {
        Remove-Item Env:\SQL_DATABASE -ErrorAction SilentlyContinue
    }
    else {
        $env:SQL_DATABASE = $previousSqlDatabase
    }

    Pop-Location
}
