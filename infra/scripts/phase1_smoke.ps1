Param(
    [string]$RepoRoot = "c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai",
    [string]$WorkspaceRoot = "c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/apps",
    [string]$StateRoot = "./.mvp-webhook-live-phase1-smoke",
    [string]$WebhookSecret = "dev-secret",
    [int]$WebhookPort = 8093,
    [int]$PostgresPort = 55432,
    [int]$RedisPort = 6381,
    [int]$LangfusePort = 3300
)

$ErrorActionPreference = "Stop"

Set-Location $RepoRoot
$env:POSTGRES_PORT = "$PostgresPort"
$env:REDIS_PORT = "$RedisPort"
$env:LANGFUSE_PORT = "$LangfusePort"
$env:POSTGRES_DB = "langfuse"
$env:POSTGRES_USER = "langfuse"
$env:POSTGRES_PASSWORD = "langfuse-local-smoke"
$env:LANGFUSE_NEXTAUTH_SECRET = "phase1-local-nextauth-secret"
$env:LANGFUSE_SALT = "phase1-local-salt"

Write-Output "[phase1-smoke] starting compose core services"
docker compose -f infra/docker/docker-compose.phase1.yml up -d postgres redis langfuse | Out-Null

Write-Output "[phase1-smoke] starting webhook server"
$serverCmd = "$RepoRoot/.venv/Scripts/python.exe -m ast_indexer serve-webhook --workspace-root $WorkspaceRoot --state-root $StateRoot --webhook-secret $WebhookSecret --host 127.0.0.1 --port $WebhookPort --queue-backend redis --redis-url redis://localhost:$RedisPort/0 --observability-backend langfuse --langfuse-host http://localhost:$LangfusePort --embedding-backend hash"
$serverJob = Start-Job -ScriptBlock {
    Param($RepoRootInner, $CmdInner)
    Set-Location "$RepoRootInner/apps/worker-indexer-py"
    $env:PYTHONPATH = "src"
    Invoke-Expression $CmdInner
} -ArgumentList $RepoRoot, $serverCmd

Start-Sleep -Seconds 3

try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$WebhookPort/healthz"
    $ready = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$WebhookPort/readyz"
    if ($health.status -ne "ok") { throw "healthz failed" }
    if ($ready.status -ne "ready") { throw "readyz failed" }

    $body = '{"repository":{"name":"worker-indexer-py"},"sender":{"login":"phase1-smoke-user"},"commits":[{"modified":["src/ast_indexer/cli.py"],"removed":[]}]}'
    $hmac = New-Object System.Security.Cryptography.HMACSHA256
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($WebhookSecret)
    $sig = 'sha256=' + ([System.BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace('-', '').ToLower())
    $headers = @{
        'X-GitHub-Event' = 'push'
        'X-GitHub-Delivery' = 'phase1-smoke-delivery'
        'X-Hub-Signature-256' = $sig
        'X-Correlation-ID' = 'corr-phase1-smoke'
        'Content-Type' = 'application/json'
    }
    $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$WebhookPort/webhooks/github" -Headers $headers -Body $body
    $payload = $response.Content | ConvertFrom-Json

    if ($response.StatusCode -ne 202) { throw "webhook status was not 202" }
    if (-not $payload.processed) { throw "webhook was not processed" }
    if ($payload.correlation_id -ne 'corr-phase1-smoke') { throw "correlation id mismatch" }

    Write-Output "[phase1-smoke] success"
    Write-Output ($payload | ConvertTo-Json -Depth 5)
}
finally {
    Write-Output "[phase1-smoke] stopping webhook server job"
    Stop-Job $serverJob -ErrorAction SilentlyContinue
    Remove-Job $serverJob -ErrorAction SilentlyContinue

    Write-Output "[phase1-smoke] tearing down compose services"
    Set-Location $RepoRoot
    docker compose -f infra/docker/docker-compose.phase1.yml down | Out-Null
}
