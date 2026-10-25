<#
  Start the local Ollama server with Ragnarok's settings and a log file (docs/DECISIONS.md D-004, D-021).

  Why this exists: the tray app inherits whatever environment launched it, so settings can silently be missing.
  This script loads the OLLAMA_* user variables into the process, starts `ollama serve`, and logs to
  %LOCALAPPDATA%\Ragnarok\logs so the applied settings can be verified.

  Usage:  powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1
#>
$names = 'OLLAMA_NO_CLOUD', 'OLLAMA_KEEP_ALIVE', 'OLLAMA_MAX_LOADED_MODELS', 'OLLAMA_NUM_PARALLEL',
         'OLLAMA_CONTEXT_LENGTH', 'OLLAMA_FLASH_ATTENTION', 'OLLAMA_KV_CACHE_TYPE'
$defaults = @{ OLLAMA_NO_CLOUD = '1'; OLLAMA_KEEP_ALIVE = '-1'; OLLAMA_MAX_LOADED_MODELS = '2';
               OLLAMA_NUM_PARALLEL = '1'; OLLAMA_CONTEXT_LENGTH = '16384'; OLLAMA_FLASH_ATTENTION = '1';
               OLLAMA_KV_CACHE_TYPE = 'q8_0' }
foreach ($n in $names) {
    $v = [Environment]::GetEnvironmentVariable($n, 'User')
    if (-not $v) { $v = $defaults[$n]; [Environment]::SetEnvironmentVariable($n, $v, 'User') }
    Set-Item "env:$n" $v
}

Get-Process 'ollama', 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

$logdir = Join-Path $env:LOCALAPPDATA 'Ragnarok\logs'
New-Item -ItemType Directory -Force $logdir | Out-Null
$err = Join-Path $logdir 'ollama-serve.err.log'
$out = Join-Path $logdir 'ollama-serve.out.log'
Remove-Item $err, $out -ErrorAction SilentlyContinue
Start-Process -FilePath (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe') -ArgumentList 'serve' `
    -WindowStyle Hidden -RedirectStandardError $err -RedirectStandardOutput $out

$up = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; $up = $true; break } catch {}
}
if (-not $up) { Write-Error "Ollama did not come up; see $err"; exit 1 }

$lines = Get-Content $err, $out -ErrorAction SilentlyContinue
$cfg = $lines | Select-String 'server config' | Select-Object -Last 1
[regex]::Matches($cfg.Line, 'OLLAMA_(NO_CLOUD|KEEP_ALIVE|MAX_LOADED_MODELS|NUM_PARALLEL|CONTEXT_LENGTH|FLASH_ATTENTION|KV_CACHE_TYPE):\S+') |
    ForEach-Object { $_.Value }
$lines | Select-String 'cloud disabled' | Select-Object -Last 1 | ForEach-Object { $_.Line.Substring($_.Line.IndexOf('msg=')) }
$lines | Select-String 'inference compute' | Select-Object -Last 1 | ForEach-Object { 'GPU: ' + [regex]::Match($_.Line, 'description="[^"]+"').Value }
