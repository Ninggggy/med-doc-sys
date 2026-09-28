param(
    [string]$OutputFile = ".\backup\mysql_dump.sql"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $scriptDir $OutputFile))
$outputDir = Split-Path -Parent $outputPath

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
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

    Write-Host "Exporting MySQL database '$database' to $outputPath"
    $dump = docker compose exec -T mysql mysqldump `
        --single-transaction `
        --quick `
        --default-character-set=utf8mb4 `
        -u$user `
        "-p$password" `
        $database

    if ($LASTEXITCODE -ne 0) {
        throw "mysqldump failed with exit code $LASTEXITCODE"
    }

    [System.IO.File]::WriteAllText($outputPath, ($dump -join [Environment]::NewLine), [System.Text.Encoding]::UTF8)
    Write-Host "MySQL export completed: $outputPath"
}
finally {
    Pop-Location
}
