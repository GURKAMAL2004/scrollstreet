# ScrollStreet launcher - starts everything needed, then opens the app window.
# Run hidden via ScrollStreet.vbs (the desktop shortcut points there).

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
# this machine keeps Ollama models on D: - harmless no-op elsewhere
if (Test-Path "D:\ollama-models") { $env:OLLAMA_MODELS = "D:\ollama-models" }

function Test-Feed {
    try {
        Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

# 1. Ollama (the brains)
if (-not (Get-Process -Name "ollama*" -ErrorAction SilentlyContinue)) {
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
    if (Test-Path $ollama) { Start-Process $ollama -WindowStyle Hidden }
}

# 2. The ScrollStreet server (hidden console)
if (-not (Test-Feed)) {
    Start-Process "$root\.venv\Scripts\python.exe" -ArgumentList "`"$root\server.py`"" `
        -WorkingDirectory $root -WindowStyle Hidden
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 500
        if (Test-Feed) { break }
    }
}

# 3. Open as an app window (chromeless - feels native)
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) {
    Start-Process $edge -ArgumentList "--app=http://127.0.0.1:8765/"
} else {
    Start-Process "http://127.0.0.1:8765/"   # default browser fallback
}
