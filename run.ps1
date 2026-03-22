# Build and start the stack from repo root.
#   .\run.ps1           # local (reapo-local)
#   .\run.ps1 -Stack remote

param(
    [ValidateSet('local', 'remote')]
    [string] $Stack = 'local'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$composeFile = if ($Stack -eq 'remote') {
    'infra/docker/docker-compose.remote.yml'
} else {
    'infra/docker/docker-compose.local.yml'
}
$projectName = if ($Stack -eq 'remote') { 'reapo-remote' } else { 'reapo-local' }
$envFile = Join-Path $PSScriptRoot '.env'

if (Test-Path $envFile) {
    docker compose --env-file $envFile -f $composeFile --project-name $projectName up -d --build
} else {
    docker compose -f $composeFile --project-name $projectName up -d --build
}
