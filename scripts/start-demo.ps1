# Start the whole demo (FastAPI backend + React frontend) with one command.
#
#   .\scripts\start-demo.ps1              # Gemini if backend\.env has GEMINI_API_KEY, else local model
#   .\scripts\start-demo.ps1 -Local       # local model even when a Gemini key exists
#   .\scripts\start-demo.ps1 -EngineOnly  # no model at all: every answer from the engine, instant
#   .\scripts\start-demo.ps1 -Stop        # stop the servers this script started (nothing else)
#
# What it does, in order:
#   1. Python: uses backend\.venv. If it is missing or broken it builds a new one
#      from an installed Python 3.11-3.14 (the `py` launcher first, then `python`
#      on PATH; set ADVISOR_PYTHON to an interpreter path to choose one). A broken
#      venv is renamed aside, never deleted.
#   2. Backend packages: installed (`pip install -e .[ingestion]`) the first time
#      and again whenever backend\pyproject.toml changes.
#   3. Frontend packages: `npm install` when node_modules is missing or
#      package-lock.json changed.
#   4. Keys: backend\.env is read by the backend itself. This script only checks
#      that a Gemini key line exists; it never reads or prints the value. In a new
#      git worktree with no backend\.env, it copies the main checkout's file once
#      (never overwriting an existing one).
#   5. Ports: if 8000 or 5173 is taken it says by which process and stops. It
#      never kills a process it did not start.
#   6. Starts each server in its own window (logs also go to .demo\*.log), waits
#      until both answer, and prints the address. If one fails it shows its log.
#
# Gemini mode: Gemini reads each question AND writes the answer from the engine's
# verified facts; the consistency check rejects any answer that changes a fact.
# Local mode needs Ollama with qwen2.5:7b and qwen2.5vl:3b (`ollama list`).
# Transcript images are read by the local vision model in both modes.
# The server windows are independent: closing this terminal does not stop them.

param(
    [switch]$EngineOnly,
    [switch]$Local,
    [switch]$Stop,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$stateDir = Join-Path $repo '.demo'
$stateFile = Join-Path $stateDir 'servers.json'
$venv = Join-Path $backend '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

function Say($text) { Write-Host "[demo] $text" }
function Fail($text) { Write-Host "[demo] $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- stop ----
function Get-OwnServers {
    if (-not (Test-Path $stateFile)) { return @() }
    # Piping unrolls the array: Windows PowerShell 5.1's ConvertFrom-Json hands
    # back a JSON list as ONE object, which would otherwise be a single "entry".
    try { return @(Get-Content $stateFile -Raw | ConvertFrom-Json | ForEach-Object { $_ }) } catch { return @() }
}

function Test-OwnServer($entry) {
    # The PID must still exist AND have the start time we recorded, so a reused
    # PID belonging to some other program is never touched.
    try {
        $p = Get-Process -Id ([int]$entry.pid) -ErrorAction Stop
        return ($p.StartTime.ToString('o') -eq [string]$entry.started)
    } catch { return $false }
}

if ($Stop) {
    $stopped = 0
    foreach ($entry in Get-OwnServers) {
        if (Test-OwnServer $entry) {
            & taskkill.exe /PID $entry.pid /T /F | Out-Null
            Say "Stopped the $($entry.role) window (pid $($entry.pid))."
            $stopped++
        }
    }
    if (Test-Path $stateFile) { Remove-Item -LiteralPath $stateFile }
    if ($stopped -eq 0) { Say 'No servers started by this script are running.' }
    exit 0
}

# --------------------------------------------------------------- ports ----
foreach ($port in $BackendPort, $FrontendPort) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
        $what = if ($owner) { "$($owner.Name) (pid $($owner.ProcessId))" } else { "pid $($conn.OwningProcess)" }
        $ours = @(Get-OwnServers | Where-Object { (Test-OwnServer $_) -and $_.port -eq $port }).Count -gt 0
        Write-Host "[demo] Port $port is already in use by $what." -ForegroundColor Yellow
        if ($ours) {
            Say 'Those are servers this script started earlier. Run .\scripts\start-demo.ps1 -Stop first.'
        } else {
            Say 'Close that server window (or pick other ports with -BackendPort / -FrontendPort).'
            Say 'Nothing was stopped: this script only stops servers it started itself.'
        }
        exit 1
    }
}

# -------------------------------------------------------------- python ----
function Test-Python($exe) {
    try {
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $null }
        $minor = [int]($v.Trim().Split('.')[1])
        if ($v.Trim().StartsWith('3.') -and $minor -ge 11 -and $minor -le 14) { return $v.Trim() }
    } catch { }
    return $null
}

function Find-BasePython {
    if ($env:ADVISOR_PYTHON) {
        if (Test-Python $env:ADVISOR_PYTHON) { return $env:ADVISOR_PYTHON }
        Fail "ADVISOR_PYTHON=$env:ADVISOR_PYTHON is not a working Python 3.11-3.14."
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($minor in 14, 13, 12, 11) {
            try {
                $exe = & py "-3.$minor" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Python $exe.Trim())) { return $exe.Trim() }
            } catch { }
        }
    }
    foreach ($name in 'python', 'python3') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*' -and (Test-Python $cmd.Source)) { return $cmd.Source }
    }
    return $null
}

if ((Test-Path $venvPython) -and -not (Test-Python $venvPython)) {
    # Typically the Python it was built from was uninstalled or upgraded.
    $aside = "$venv.broken-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Say "backend\.venv no longer runs; moving it aside to $(Split-Path -Leaf $aside)."
    Rename-Item -LiteralPath $venv -NewName (Split-Path -Leaf $aside)
}

if (-not (Test-Path $venvPython)) {
    $base = Find-BasePython
    if (-not $base) {
        Fail 'No Python 3.11-3.14 found. Install Python 3.14 from python.org (or set ADVISOR_PYTHON to python.exe) and rerun.'
    }
    Say "Creating backend\.venv with $base ..."
    & $base -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { Fail 'Could not create backend\.venv.' }
}

# Reinstall backend packages when pyproject.toml changes (or after a fresh venv).
$pyproject = Join-Path $backend 'pyproject.toml'
$depsStamp = Join-Path $venv '.advisor-deps'
$wantDeps = (Get-FileHash $pyproject -Algorithm SHA256).Hash
$haveDeps = if (Test-Path $depsStamp) { (Get-Content $depsStamp -Raw).Trim() } else { '' }
if ($wantDeps -ne $haveDeps) {
    Say 'Installing backend packages (first run takes a few minutes) ...'
    Push-Location $backend
    try {
        & $venvPython -m pip install --disable-pip-version-check -q -e '.[ingestion]'
        $pipExit = $LASTEXITCODE
    } finally { Pop-Location }
    if ($pipExit -ne 0) { Fail 'pip install failed - see the messages above (is the internet connected?).' }
    Set-Content -LiteralPath $depsStamp -Value $wantDeps -Encoding ascii
}
# Import the whole app once, so a missing package fails here with its real
# error instead of as a silent 502 in the browser later.
Push-Location $backend
try {
    $importErr = & cmd /c "`"$venvPython`" -c `"import app.main`" 2>&1"
    $importOk = ($LASTEXITCODE -eq 0)
} finally { Pop-Location }
if (-not $importOk) {
    $importErr | Select-Object -Last 6 | ForEach-Object { Write-Host "    $_" }
    if ("$importErr" -match 'ModuleNotFoundError|ImportError') {
        # Force a package reinstall on the next run.
        Remove-Item -LiteralPath $depsStamp -ErrorAction SilentlyContinue
        Fail 'A backend package is missing (see above). Rerun this script to reinstall the packages.'
    }
    Fail 'The backend cannot start (see the error above). Check the settings in backend\.env, then rerun.'
}

# ------------------------------------------------------------ frontend ----
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail 'npm not found. Install Node.js 20+ from nodejs.org and rerun.'
}
$lock = Join-Path $frontend 'package-lock.json'
$installedLock = Join-Path $frontend 'node_modules\.package-lock.json'
$needNpm = -not (Test-Path (Join-Path $frontend 'node_modules\vite')) -or
    ((Test-Path $lock) -and (Test-Path $installedLock) -and
     (Get-Item $lock).LastWriteTime -gt (Get-Item $installedLock).LastWriteTime)
if ($needNpm) {
    Say 'Installing frontend packages ...'
    Push-Location $frontend
    try { & npm install --no-audit --no-fund; $npmExit = $LASTEXITCODE } finally { Pop-Location }
    if ($npmExit -ne 0) { Fail 'npm install failed - see the messages above.' }
}

# ---------------------------------------------------------------- keys ----
$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    # A new git worktree has no .env (it is git-ignored). Reuse the main
    # checkout's once. The file is copied, never read or printed here.
    try { $common = (& git -C $repo rev-parse --path-format=absolute --git-common-dir 2>$null) } catch { $common = $null }
    if ($common) {
        $mainEnv = Join-Path (Split-Path -Parent $common.Trim()) 'backend\.env'
        if ((Test-Path $mainEnv) -and ((Resolve-Path $mainEnv).Path -ne (Join-Path $backend '.env'))) {
            Copy-Item -LiteralPath $mainEnv -Destination $envFile
            Say 'Copied backend\.env (API keys) from the main checkout. Its contents were not displayed.'
        }
    }
    if (-not (Test-Path $envFile)) {
        Say 'No backend\.env found: Gemini and Deepgram voice are off until you add GEMINI_API_KEY / DEEPGRAM_API_KEY there.'
    }
}

# Only checks that a non-empty key line is present; the value is never shown.
$hasGemini = (Test-Path $envFile) -and
    [bool](Select-String -Path $envFile -Pattern '^\s*(GEMINI|GOOGLE)_API_KEY\s*=\s*\S+' -Quiet)
$hasDeepgram = (Test-Path $envFile) -and
    [bool](Select-String -Path $envFile -Pattern '^\s*DEEPGRAM_API_KEY\s*=\s*\S+' -Quiet)

# ---------------------------------------------------------------- mode ----
$vision = "`$env:VISION_PROVIDER='ollama'; `$env:OLLAMA_VISION_MODEL='qwen2.5vl:3b'; "
if ($EngineOnly) {
    $envs = ''
    $mode = 'engine only (no model)'
} elseif ($hasGemini -and -not $Local) {
    $envs = "`$env:LLM_PROVIDER='gemini'; `$env:ADVISOR_REPHRASE='true'; " + $vision
    $mode = 'Gemini writes the answers (checked by the engine)'
} else {
    $envs = "`$env:LLM_PROVIDER='ollama'; `$env:OLLAMA_CHAT_MODEL='qwen2.5:7b'; " +
            "`$env:ADVISOR_REPHRASE='false'; " + $vision
    $mode = 'local model reads questions; engine writes the answers'
    if (-not $Local -and -not $hasGemini) { Say 'No GEMINI_API_KEY in backend\.env - using the local model.' }
}

# --------------------------------------------------------------- start ----
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$backendLog = Join-Path $stateDir 'backend.log'
$frontendLog = Join-Path $stateDir 'frontend.log'

# cmd /c merges stderr into stdout before PowerShell sees it, so the window
# shows plain server output and Tee-Object copies it to the log file.
$backendCmd = "`$host.UI.RawUI.WindowTitle = 'Advisor backend :$BackendPort'; $envs" +
    "cmd /c '.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort 2>&1' | Tee-Object -FilePath '$backendLog'"
$frontendCmd = "`$host.UI.RawUI.WindowTitle = 'Advisor frontend :$FrontendPort'; " +
    "`$env:VITE_API_TARGET='http://127.0.0.1:$BackendPort'; " +
    "cmd /c 'npx vite --port $FrontendPort --strictPort 2>&1' | Tee-Object -FilePath '$frontendLog'"

$servers = @()
foreach ($spec in @(
        @{ role = 'backend'; dir = $backend; cmd = $backendCmd; port = $BackendPort },
        @{ role = 'frontend'; dir = $frontend; cmd = $frontendCmd; port = $FrontendPort })) {
    $proc = Start-Process powershell -WorkingDirectory $spec.dir -PassThru `
        -ArgumentList '-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $spec.cmd
    $servers += [pscustomobject]@{
        role = $spec.role; pid = $proc.Id; port = $spec.port
        started = (Get-Process -Id $proc.Id).StartTime.ToString('o')
    }
}
ConvertTo-Json -InputObject $servers | Set-Content -LiteralPath $stateFile -Encoding ascii

Say "Mode: $mode"
if (-not $EngineOnly -and -not $hasDeepgram) { Say 'No DEEPGRAM_API_KEY in backend\.env - voice is off.' }
Say 'Waiting for both servers ...'

function Test-Url($url) {
    try { return (Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3).StatusCode -eq 200 } catch { return $false }
}
function Test-Running($entry) {
    # The server runs under `cmd /c` inside its window; when it exits, cmd exits.
    return [bool](Get-CimInstance Win32_Process -Filter "ParentProcessId=$($entry.pid) AND Name='cmd.exe'" -ErrorAction SilentlyContinue)
}
function Show-Log($role, $log) {
    Write-Host "[demo] The $role did not start. Last lines of $log :" -ForegroundColor Red
    if (Test-Path $log) { Get-Content $log -Tail 25 | ForEach-Object { Write-Host "    $_" } }
    Say "Its window is still open with the full output. Fix the problem, close both windows (or run -Stop), and rerun."
}

$backendUrl = "http://127.0.0.1:$BackendPort/health"
$proxyUrl = "http://localhost:$FrontendPort/api/health"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$backendUp = $false; $frontendUp = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if (-not $backendUp) { $backendUp = Test-Url $backendUrl }
    if ($backendUp -and -not $frontendUp) { $frontendUp = Test-Url $proxyUrl }
    if ($backendUp -and $frontendUp) { break }
    $age = ((Get-Date) - [datetime]$servers[0].started).TotalSeconds
    if ($age -gt 5) {
        if (-not $backendUp -and -not (Test-Running $servers[0])) { Show-Log 'backend' $backendLog; exit 1 }
        if (-not $frontendUp -and -not (Test-Running $servers[1])) { Show-Log 'frontend' $frontendLog; exit 1 }
    }
}
if (-not $backendUp) { Show-Log 'backend' $backendLog; exit 1 }
if (-not $frontendUp) { Show-Log 'frontend' $frontendLog; exit 1 }

Say "Ready: open http://localhost:$FrontendPort"
Say "Backend http://127.0.0.1:$BackendPort  |  logs in .demo\  |  stop with .\scripts\start-demo.ps1 -Stop"
