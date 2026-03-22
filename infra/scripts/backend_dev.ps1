param(
    [int]$Port = 8090
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $repoRoot

# Replace already-running local webhook servers for this port so code changes always load.
$matchingWebhookProcs = Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
    Where-Object {
        $cmd = $_.CommandLine
        $cmd -and
        $cmd -match '(dev_serve_webhook\.py|-m\s+ast_indexer\.cli)\s+serve-webhook' -and
        $cmd -match "--port\s+$Port(\s|$)"
    }

if ($matchingWebhookProcs.Count -gt 0) {
    $matchingWebhookProcs | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
}

$remainingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $remainingListener) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($remainingListener.OwningProcess)"
    $commandLine = if ($null -ne $proc) { $proc.CommandLine } else { '' }
    if (-not ($commandLine -match '(dev_serve_webhook\.py|-m\s+ast_indexer\.cli)\s+serve-webhook')) {
        Write-Error "Port $Port is in use by PID $($remainingListener.OwningProcess) that is not the Reapo backend. Stop that process or use another port."
    }
}

if (-not $env:AST_INDEXER_OBSERVABILITY_BACKEND) {
    $env:AST_INDEXER_OBSERVABILITY_BACKEND = 'jsonl'
}

& .\.venv\Scripts\python.exe -B apps/worker-indexer-py/dev_serve_webhook.py serve-webhook --workspace-root . --state-root state --port $Port
exit $LASTEXITCODE