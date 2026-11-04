# Start the backend and frontend for the demo, each in its own PowerShell window.
#
#   .\scripts\start-demo.ps1            # AI mode: local model reads questions and transcript images
#   .\scripts\start-demo.ps1 -EngineOnly  # no model at all: every answer from the engine, instant
#
# AI mode needs Ollama running with qwen2.5:7b and qwen2.5vl:3b pulled
# (`ollama list` to check). Keep both windows open while presenting.

param([switch]$EngineOnly)

$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'

if ($EngineOnly) {
    $envs = ''
} else {
    # Model picks which engine answer a question wants (seconds); its rephrasing
    # step is off because on this laptop it takes a minute or more per answer.
    $envs = "`$env:LLM_PROVIDER='ollama'; `$env:OLLAMA_CHAT_MODEL='qwen2.5:7b'; " +
            "`$env:ADVISOR_REPHRASE='false'; `$env:VISION_PROVIDER='ollama'; " +
            "`$env:OLLAMA_VISION_MODEL='qwen2.5vl:3b'; "
}

Start-Process powershell -WorkingDirectory $backend -ArgumentList '-NoExit', '-Command',
    "$envs.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Start-Process powershell -WorkingDirectory $frontend -ArgumentList '-NoExit', '-Command', 'npm run dev'

Write-Host 'Starting... open http://localhost:5173 in about 10 seconds.'
