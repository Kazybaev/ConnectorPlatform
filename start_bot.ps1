$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot ".env"

if (-not (Test-Path $pythonPath)) {
    Write-Error "Python from .venv was not found. Create the virtual environment first."
    exit 1
}

if (-not (Test-Path $envPath)) {
    Write-Error ".env was not found. Copy .env.example to .env and fill in your platform settings."
    exit 1
}

$existingApiProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app.main:app"
}

foreach ($process in $existingApiProcesses) {
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not stop API process $($process.ProcessId): $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 1
& $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000
