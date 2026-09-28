param(
    [string]$Platform = "linux/amd64",
    [string]$OutputDir = ".\\dist\\linux-bundle",
    [string]$PythonBaseImage = "docker.m.daocloud.io/library/python:3.10-slim",
    [string]$InstallAptRuntime = "false",
    [string]$AptPrimaryMirror = "http://deb.debian.org/debian",
    [string]$AptSecurityMirror = "http://security.debian.org/debian-security",
    [string]$PipIndexUrl = "https://pypi.org/simple",
    [string]$PipExtraIndexUrl = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir "..\\.."))
$bundleDir = [System.IO.Path]::GetFullPath((Join-Path $scriptDir $OutputDir))
$imagesDir = Join-Path $bundleDir "images"
$deployDir = Join-Path $bundleDir "deploy"

New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
New-Item -ItemType Directory -Force -Path $imagesDir | Out-Null
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null

function Build-And-ExportImage {
    param(
        [string]$Tag,
        [string]$Dockerfile,
        [string]$TarName,
        [hashtable]$BuildArgs = @{}
    )

    $tarPath = Join-Path $imagesDir $TarName
    $dockerfilePath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Dockerfile))

    if (-not (Test-Path $dockerfilePath)) {
        throw "Dockerfile not found: $dockerfilePath"
    }

    Write-Host "Building $Tag -> $tarPath"
    $cmd = @(
        "build",
        "--platform", $Platform,
        "--pull=false",
        "--file", $dockerfilePath,
        "--tag", $Tag,
        $repoRoot
    )
    foreach ($entry in $BuildArgs.GetEnumerator()) {
        $cmd = @("build") + @("--build-arg", "$($entry.Key)=$($entry.Value)") + $cmd[1..($cmd.Length-1)]
    }
    & docker $cmd

    if ($LASTEXITCODE -ne 0) {
        throw "docker build failed for $Tag"
    }

    Write-Host "Saving $Tag -> $tarPath"
    docker save -o $tarPath $Tag

    if ($LASTEXITCODE -ne 0) {
        throw "docker save failed for $Tag"
    }
}

function Resolve-PythonBaseImage {
    param(
        [string]$SourceImage
    )

    $localAlias = "local/agent-python-base:3.10-slim"
    docker image inspect $SourceImage *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Using local Python base image: $SourceImage"
        docker tag $SourceImage $localAlias
        if ($LASTEXITCODE -ne 0) {
            throw "docker tag failed for Python base image $SourceImage"
        }
        return $localAlias
    }

    Write-Host "Local Python base image not found, fallback to remote source: $SourceImage"
    return $SourceImage
}

Push-Location $scriptDir
try {
    $resolvedPythonBaseImage = Resolve-PythonBaseImage -SourceImage $PythonBaseImage
    Build-And-ExportImage -Tag "local/agent-backend:linux-amd64" -Dockerfile "agent\deploy\backend.Dockerfile" -TarName "agent-backend-linux-amd64.tar" -BuildArgs @{
        PYTHON_BASE_IMAGE = $resolvedPythonBaseImage
        INSTALL_APT_RUNTIME = $InstallAptRuntime
        APT_PRIMARY_MIRROR = $AptPrimaryMirror
        APT_SECURITY_MIRROR = $AptSecurityMirror
        PIP_INDEX_URL = $PipIndexUrl
        PIP_EXTRA_INDEX_URL = $PipExtraIndexUrl
    }

    Copy-Item ".env.example" (Join-Path $deployDir ".env.example") -Force
    Copy-Item "docker-compose.images.yaml" (Join-Path $deployDir "docker-compose.images.yaml") -Force
    Copy-Item "load_linux_bundle.sh" (Join-Path $deployDir "load_linux_bundle.sh") -Force
    Copy-Item "..\\DEPLOY_BACKEND_ONLY.md" (Join-Path $bundleDir "DEPLOY_BACKEND_ONLY.md") -Force

    Write-Host "Linux deployment bundle created at: $bundleDir"
}
finally {
    Pop-Location
}
