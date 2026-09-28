param(
    [string]$InputFile = ".\backup\mysql_dump.sql"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$inputPath = [System.IO.Path]::GetFullPath((Join-Path $scriptDir $InputFile))

if (-not (Test-Path $inputPath)) {
    throw "SQL dump not found: $inputPath"
}

Push-Location $scriptDir
try {
    $envFile = Join-Path $scriptDir ".env"
    if (-not (Test-Path $envFile)) {
        throw "Missing .env file: $envFile"
    }

    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $key, $value = $_ -split '=', 2
        Set-Item -Path "env:$($key.Trim())" -Value $value.Trim()
    }

    $database = $env:MYSQL_DATABASE
    $user = $env:MYSQL_USER
    $password = $env:MYSQL_PASSWORD

    if (-not $database -or -not $user -or -not $password) {
        throw "MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD must be set in agent/deploy/.env"
    }

    Write-Host "Importing MySQL database '$database' from $inputPath"
    Get-Content $inputPath | docker compose exec -T mysql mysql `
        -u$user `
        "-p$password" `
        $database

    if ($LASTEXITCODE -ne 0) {
        throw "mysql import failed with exit code $LASTEXITCODE"
    }

    Write-Host "MySQL import completed: $inputPath"
}
finally {
    Pop-Location
}
