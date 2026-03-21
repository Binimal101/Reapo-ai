Param(
    [switch]$Strict
)

$ErrorActionPreference = "Stop"

function Test-SecretValue {
    Param(
        [string]$Name,
        [string]$Value,
        [int]$MinLength = 16
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "missing"
    }

    $trimmed = $Value.Trim()
    $blocked = @(
        "change-me",
        "change-me-in-prod",
        "example",
        "password",
        "secret",
        "langfuse",
        "dev-secret"
    )

    if ($blocked -contains $trimmed.ToLower()) {
        return "placeholder"
    }

    if ($trimmed.Length -lt $MinLength) {
        return "too_short"
    }

    return $null
}

$checks = @(
    @{ Name = "POSTGRES_PASSWORD"; MinLength = 12 },
    @{ Name = "LANGFUSE_NEXTAUTH_SECRET"; MinLength = 24 },
    @{ Name = "LANGFUSE_SALT"; MinLength = 16 },
    @{ Name = "AST_INDEXER_WEBHOOK_SECRET"; MinLength = 16 },
    @{ Name = "GITHUB_APP_WEBHOOK_SECRET"; MinLength = 16 }
)

$failures = @()

foreach ($check in $checks) {
    $name = $check.Name
    $value = [Environment]::GetEnvironmentVariable($name)
    $result = Test-SecretValue -Name $name -Value $value -MinLength $check.MinLength
    if ($null -ne $result) {
        $failures += "${name}: ${result}"
    }
}

if ($failures.Count -gt 0) {
    Write-Output "[phase1-env-validate] failed"
    $failures | ForEach-Object { Write-Output " - $_" }
    exit 1
}

Write-Output "[phase1-env-validate] all required secrets look valid"

if ($Strict) {
    $optionalChecks = @(
        @{ Name = "LANGFUSE_PUBLIC_KEY"; MinLength = 12 },
        @{ Name = "LANGFUSE_SECRET_KEY"; MinLength = 12 },
        @{ Name = "GITHUB_APP_CLIENT_SECRET"; MinLength = 16 },
        @{ Name = "AST_INDEXER_OAUTH_ENCRYPTION_KEY"; MinLength = 40 }
    )

    $optionalFailures = @()
    foreach ($check in $optionalChecks) {
        $name = $check.Name
        $value = [Environment]::GetEnvironmentVariable($name)
        $result = Test-SecretValue -Name $name -Value $value -MinLength $check.MinLength
        if ($null -ne $result) {
            $optionalFailures += "${name}: ${result}"
        }
    }

    if ($optionalFailures.Count -gt 0) {
        Write-Output "[phase1-env-validate] strict mode failed"
        $optionalFailures | ForEach-Object { Write-Output " - $_" }
        exit 1
    }

    Write-Output "[phase1-env-validate] strict mode checks passed"
}
