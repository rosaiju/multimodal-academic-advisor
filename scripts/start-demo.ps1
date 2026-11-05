# Start the backend and frontend for the demo, each in its own PowerShell window.
#
#   .\scripts\start-demo.ps1              # Gemini if backend\.env has GEMINI_API_KEY, else local model
#   .\scripts\start-demo.ps1 -Local       # local model even when a Gemini key exists
#   .\scripts\start-demo.ps1 -EngineOnly  # no model at all: every answer from the engine, instant
#
# Gemini mode: Gemini reads each question AND writes the answer from the engine's
# verified facts; the consistency check rejects any answer that changes a fact.
# Local mode needs Ollama with qwen2.5:7b and qwen2.5vl:3b (`ollama list`). Its
# answer-writing step is off, because on this laptop it takes a minute or more.
# Transcript images are read by the local vision model in both modes.
# Keep both windows open while presenting.

param([switch]$EngineOnly, [switch]$Local)

$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'

foreach ($port in 8000, 5173) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Warning "Port $port is already in use - close the old server windows first."
        return
    }
}

# Only checks that a non-empty key is present; the value is never read into this script.
$hasGemini = (Test-Path (Join-Path $backend '.env')) -and
    [bool](Select-String -Path (Join-Path $backend '.env') -Pattern '^\s*(GEMINI|GOOGLE)_API_KEY\s*=\s*\S+' -Quiet)

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
    if (-not $Local -and -not $hasGemini) { Write-Host 'No GEMINI_API_KEY in backend\.env - using the local model.' }
}

Start-Process powershell -WorkingDirectory $backend -ArgumentList '-NoExit', '-Command',
    "$envs.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Start-Process powershell -WorkingDirectory $frontend -ArgumentList '-NoExit', '-Command', 'npm run dev'

Write-Host "Mode: $mode"
Write-Host 'Starting... open http://localhost:5173 in about 10 seconds.'
