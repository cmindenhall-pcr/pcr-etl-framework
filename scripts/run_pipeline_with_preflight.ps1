param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe,
    [switch]$ProfileOnly,
    [switch]$StagingOnly,
    [switch]$ZenOnly,
    [switch]$All = $true,
    [string]$PipelineName,
    [switch]$AutoRestartSqlServer = $true,
    [switch]$PreflightOnly,
    [int]$ConnectTimeoutSeconds = 5,
    [int]$MaxParallelPipelines = 1
)

$ErrorActionPreference = "Stop"

function Test-ServiceRunning {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    $service = Get-Service -Name $ServiceName -ErrorAction Stop
    return $service.Status -eq "Running"
}

function Get-RepoConnectionSettings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $script = @"
from src.db_connection import get_connection_settings
settings = get_connection_settings()
for key in ("server", "database", "username", "password"):
    value = settings.get(key) or ""
    print(f"{key}={value}")
"@

    $output = $script | & $PythonExe -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to load SQL connection settings from repo."
    }

    $settings = @{}
    foreach ($line in $output) {
        if ($line -match "^(server|database|username|password)=(.*)$") {
            $settings[$Matches[1]] = $Matches[2]
        }
    }

    return $settings
}

function Invoke-PythonCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,
        [Parameter(Mandatory = $true)]
        [string]$ScriptText
    )

    $scriptFile = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), '.py')
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()

    try {
        Set-Content -Path $scriptFile -Value $ScriptText
        $process = Start-Process -FilePath $PythonExe -ArgumentList $scriptFile -NoNewWindow -PassThru -Wait -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
        $output = @()
        if (Test-Path $stdoutFile) {
            $output += Get-Content $stdoutFile
        }
        if (Test-Path $stderrFile) {
            $output += Get-Content $stderrFile
        }

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = @($output)
        }
    }
    finally {
        Remove-Item $scriptFile, $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Test-SqlConnection {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Settings,
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,
        [int]$TimeoutSeconds = 5
    )

    $server = $Settings["server"]
    $database = $Settings["database"]
    $username = $Settings["username"]
    $password = $Settings["password"]

    if (-not $server -or -not $database -or -not $username) {
        throw "Missing one or more SQL settings from .env (server/database/username)."
    }

    $passwordLiteral = if ($null -eq $password) { "" } else { $password.Replace('"', '\"') }
    $script = @"
import pyodbc
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=$server;"
    "DATABASE=$database;"
    "UID=$username;"
    "PWD=$passwordLiteral;"
    "Encrypt=no;"
    "TrustServerCertificate=yes;"
)
try:
    conn = pyodbc.connect(conn_str, timeout=$TimeoutSeconds)
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    cursor.fetchone()
    conn.close()
    print("ok")
except Exception as exc:
    print(type(exc).__name__)
    print(str(exc))
    raise
"@

    $result = Invoke-PythonCapture -PythonExe $PythonExe -ScriptText $script

    return [pscustomobject]@{
        Success = ($result.ExitCode -eq 0)
        ExitCode = $result.ExitCode
        Output = @($result.Output)
    }
}

function Test-KnownEncryptionFailure {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Output
    )

    $joined = $Output -join [Environment]::NewLine
    return (
        $joined -match "Encryption not supported on the client" -or
        $joined -match "No credentials are available in the security package"
    )
}

function Restart-SqlServerServiceIfAllowed {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$AutoRestartSqlServer
    )

    if (-not $AutoRestartSqlServer) {
        throw "SQL connection failed with the known encryption error and auto-restart is disabled."
    }

    Write-Host "Known SQL client encryption error detected. Restarting MSSQLSERVER..."
    Restart-Service -Name MSSQLSERVER -Force -ErrorAction Stop

    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 2
        $service = Get-Service -Name MSSQLSERVER -ErrorAction Stop
    } while ($service.Status -ne "Running" -and (Get-Date) -lt $deadline)

    if ($service.Status -ne "Running") {
        throw "MSSQLSERVER did not return to Running within 2 minutes."
    }
}

function Get-CommandArgs {
    param(
        [bool]$RunAll,
        [bool]$ProfileOnly,
        [bool]$StagingOnly,
        [bool]$ZenOnly,
        [string]$PipelineName,
        [int]$MaxParallelPipelines
    )

    $args = @("-u", "-m", "src.run_customer_pipeline")

    if ($ProfileOnly) {
        $args += "--profile-only"
    }

    if ($StagingOnly) {
        $args += "--staging-only"
    }

    if ($ZenOnly) {
        $args += "--zen-only"
    }

    if ($RunAll -and $MaxParallelPipelines -gt 1) {
        $args += "--max-parallel-pipelines"
        $args += [string]$MaxParallelPipelines
    }

    if ($RunAll) {
        $args += "--"
        $args += "--all"
    }
    elseif ($PipelineName) {
        $args += $PipelineName
    }
    else {
        throw "Provide -PipelineName when -All:`$false."
    }

    return $args
}

if (-not (Test-Path $RepoRoot)) {
    throw "Repo root not found: $RepoRoot"
}

if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$requiredServices = @("CryptSvc", "KeyIso", "MSSQLSERVER")
foreach ($serviceName in $requiredServices) {
    if (-not (Test-ServiceRunning -ServiceName $serviceName)) {
        throw "Required service '$serviceName' is not running."
    }
}

Push-Location $RepoRoot
try {
    $settings = Get-RepoConnectionSettings -PythonExe $PythonExe

    Write-Host "Testing SQL connectivity to $($settings['server']) / $($settings['database'])..."
    $connectionTest = Test-SqlConnection -Settings $settings -PythonExe $PythonExe -TimeoutSeconds $ConnectTimeoutSeconds

    if (-not $connectionTest.Success -and (Test-KnownEncryptionFailure -Output $connectionTest.Output)) {
        Restart-SqlServerServiceIfAllowed -AutoRestartSqlServer:$AutoRestartSqlServer
        Write-Host "Re-testing SQL connectivity after restart..."
        $connectionTest = Test-SqlConnection -Settings $settings -PythonExe $PythonExe -TimeoutSeconds $ConnectTimeoutSeconds
    }

    if (-not $connectionTest.Success) {
        Write-Host "SQL connectivity test failed."
        Write-Host ($connectionTest.Output -join [Environment]::NewLine)
        exit $connectionTest.ExitCode
    }

    Write-Host "SQL connectivity test passed."

    if ($PreflightOnly) {
        Write-Host "Preflight completed. Skipping pipeline launch because -PreflightOnly was provided."
        exit 0
    }

    $commandArgs = Get-CommandArgs -RunAll:$All -ProfileOnly:$ProfileOnly -StagingOnly:$StagingOnly -ZenOnly:$ZenOnly -PipelineName $PipelineName -MaxParallelPipelines $MaxParallelPipelines
    Write-Host "Starting pipeline command: $PythonExe $($commandArgs -join ' ')"
    & $PythonExe @commandArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

