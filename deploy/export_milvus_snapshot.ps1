param(
    [string]$Collection = "agent_collection",
    [string]$SnapshotFile = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $SnapshotFile) {
    $SnapshotFile = ".\..\agent_backend\data\vector_snapshots\$Collection.snapshot.json"
}

$snapshotPath = [System.IO.Path]::GetFullPath((Join-Path $scriptDir $SnapshotFile))
$snapshotDir = Split-Path -Parent $snapshotPath
$mountedRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".\..\agent_backend\data"))
$normalizedSnapshot = $snapshotPath.Replace('/', '\')
$normalizedMountedRoot = $mountedRoot.Replace('/', '\')

if (-not $normalizedSnapshot.StartsWith($normalizedMountedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "SnapshotFile must be under agent\\agent_backend\\data so the backend container can access it. Current: $snapshotPath"
}

$relativeSnapshot = $snapshotPath.Substring($mountedRoot.Length).TrimStart('\')
$containerSnapshot = "/app/agent/agent_backend/data/" + ($relativeSnapshot -replace '\\','/')

if (-not (Test-Path $snapshotDir)) {
    New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
}

Push-Location $scriptDir
try {
    Write-Host "Exporting Milvus collection '$Collection' to $snapshotPath"
    docker compose exec -T agent-backend python -m agent.agent_backend.database.vector.vector_migration `
        export `
        --collection $Collection `
        --snapshot $containerSnapshot

    if ($LASTEXITCODE -ne 0) {
        throw "Milvus export failed with exit code $LASTEXITCODE"
    }

    Write-Host "Milvus snapshot exported to $snapshotPath"
}
finally {
    Pop-Location
}
