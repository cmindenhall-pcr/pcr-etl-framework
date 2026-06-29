param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$logDir = Join-Path $RepoRoot "logs"
$tmpDir = Join-Path $RepoRoot "tmp"
$logPath = Join-Path $logDir "data_cockpit.log"
$stdoutPath = Join-Path $logDir "preload_profile_stdout.log"
$stderrPath = Join-Path $logDir "preload_profile_stderr.log"
$pidPath = Join-Path $tmpDir "preload_profile.pid"
$commandArgs = @("-u", "-m", "src.run_customer_pipeline", "--all", "--profile-only")

if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not (Test-Path $RepoRoot)) {
    throw "Repo root not found: $RepoRoot"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

if (-not (Test-Path $logPath)) {
    New-Item -ItemType File -Path $logPath -Force | Out-Null
}

if (Test-Path $pidPath) {
    $existingPid = (Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($existingPid) {
        $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existingProcess) {
            Write-Host "A preload profile run is already active (PID $existingPid)."
            Write-Host "Monitor progress with: Get-Content `"$logPath`" -Wait"
            exit 0
        }
    }

    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
}

if ($NoWait) {
    Write-Host "Starting preload profile run in background..."
    $backgroundProcess = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $commandArgs `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    $backgroundProcess.Id | Set-Content -Path $pidPath -NoNewline
    Write-Host "Preload profile started in background (PID $($backgroundProcess.Id))."
    Write-Host "Progress log: $logPath"
    Write-Host "Stdout log: $stdoutPath"
    Write-Host "Stderr log: $stderrPath"
    exit 0
}

$initialLength = (Get-Item $logPath).Length

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $PythonExe
$startInfo.Arguments = [string]::Join(" ", $commandArgs)
$startInfo.WorkingDirectory = $RepoRoot
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.CreateNoWindow = $true
$startInfo.Environment["PYTHONUNBUFFERED"] = "1"

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo

Write-Host "Starting preload profile run..."
[void]$process.Start()
$process.Id | Set-Content -Path $pidPath -NoNewline

$stdoutWriter = [System.IO.StreamWriter]::new($stdoutPath, $true)
$stderrWriter = [System.IO.StreamWriter]::new($stderrPath, $true)
$currentPipeline = ""
$completedPipeline = ""
$activity = "ETL preload profile"
$status = "Starting"
$percentComplete = 0

$stream = [System.IO.File]::Open(
    $logPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::ReadWrite
)

try {
    $stream.Seek($initialLength, [System.IO.SeekOrigin]::Begin) | Out-Null
    $reader = New-Object System.IO.StreamReader($stream)

    while (-not $process.HasExited) {
        while (-not $process.StandardOutput.EndOfStream) {
            $stdoutLine = $process.StandardOutput.ReadLine()
            $stdoutWriter.WriteLine($stdoutLine)
            $stdoutWriter.Flush()
        }

        while (-not $process.StandardError.EndOfStream) {
            $stderrLine = $process.StandardError.ReadLine()
            $stderrWriter.WriteLine($stderrLine)
            $stderrWriter.Flush()
        }

        $line = $reader.ReadLine()

        if ($null -eq $line) {
            Start-Sleep -Milliseconds 300
            continue
        }

        if ($line -match "Batch progress: (\d+) of (\d+) pipelines .* - (.+)$") {
            $index = [int]$Matches[1]
            $total = [int]$Matches[2]
            $currentPipeline = $Matches[3]
            $percentComplete = [int](($index - 1) / $total * 100)
            $status = "Running $currentPipeline ($index of $total)"
            Write-Progress -Activity $activity -Status $status -PercentComplete $percentComplete
            continue
        }

        if ($line -match "Profile-only mode complete for pipeline: (.+)$") {
            $completedPipeline = $Matches[1]
            if ($currentPipeline -eq $completedPipeline) {
                $status = "Completed $completedPipeline"
            }
            else {
                $status = "Completed $completedPipeline; running $currentPipeline"
            }
            Write-Progress -Activity $activity -Status $status -PercentComplete $percentComplete
            continue
        }

        if ($line -match "\| ERROR \|") {
            Write-Host $line
        }
    }

    while (-not $reader.EndOfStream) {
        $line = $reader.ReadLine()
        if ($line -match "\| ERROR \|") {
            Write-Host $line
        }
    }

    while (-not $process.StandardOutput.EndOfStream) {
        $stdoutWriter.WriteLine($process.StandardOutput.ReadLine())
    }

    while (-not $process.StandardError.EndOfStream) {
        $stderrWriter.WriteLine($process.StandardError.ReadLine())
    }
}
finally {
    if ($stdoutWriter) {
        $stdoutWriter.Dispose()
    }
    if ($stderrWriter) {
        $stderrWriter.Dispose()
    }
    if ($reader) {
        $reader.Dispose()
    }
    else {
        $stream.Dispose()
    }

    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
}

$process.WaitForExit()

if ($process.ExitCode -eq 0) {
    Write-Progress -Activity $activity -Completed
    Write-Host "Preload profile completed successfully."
}
else {
    Write-Progress -Activity $activity -Completed
    Write-Host "Preload profile failed with exit code $($process.ExitCode)."
    if (Test-Path $stderrPath) {
        $stderrTail = Get-Content $stderrPath -Tail 20
        if ($stderrTail) {
            Write-Host ($stderrTail -join [Environment]::NewLine)
        }
    }
    exit $process.ExitCode
}
