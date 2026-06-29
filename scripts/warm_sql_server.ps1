[CmdletBinding()]
param(
    [string]$Server = "localhost",
    [int]$TimeoutSeconds = 180,
    [int]$RetryDelaySeconds = 3
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$envFile = Join-Path $projectRoot ".env"

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        if ($line.TrimStart().StartsWith("#")) {
            continue
        }

        $parts = $line -split "=", 2
        if ($parts.Length -ne 2) {
            continue
        }

        if ($parts[0].Trim() -eq $Name) {
            return $parts[1].Trim()
        }
    }

    return $null
}

$serverFromEnv = Get-DotEnvValue -Path $envFile -Name "SQL_SERVER"
$database = Get-DotEnvValue -Path $envFile -Name "SQL_DATABASE"
$username = Get-DotEnvValue -Path $envFile -Name "SQL_USERNAME"
$password = Get-DotEnvValue -Path $envFile -Name "SQL_PASSWORD"

if ($serverFromEnv) {
    $Server = $serverFromEnv
}

if (-not $database -or -not $username -or -not $password) {
    throw "Missing SQL_DATABASE, SQL_USERNAME, or SQL_PASSWORD in $envFile."
}

$serviceName = "MSSQLSERVER"
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $service = Get-Service -Name $serviceName -ErrorAction Stop
} catch {
    throw "SQL Server service '$serviceName' was not found."
}

if ($service.Status -ne "Running") {
    Write-Host "Starting SQL Server service $serviceName..."
    try {
        Start-Service -Name $serviceName -ErrorAction Stop
    } catch {
        Write-Warning "Could not start $serviceName automatically. If needed, run this script as Administrator."
    }
} else {
    Write-Host "SQL Server service $serviceName is already running."
}

while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    try {
        & sqlcmd -S $Server -d $database -U $username -P $password -C -l 5 -Q "SET NOCOUNT ON; SELECT 1;" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "SQL Server is ready after $([int]$stopwatch.Elapsed.TotalSeconds) seconds."
            exit 0
        }
    } catch {
    }

    Write-Host "Waiting for SQL Server to accept connections..."
    Start-Sleep -Seconds $RetryDelaySeconds
}

throw "SQL Server did not become ready within $TimeoutSeconds seconds."
