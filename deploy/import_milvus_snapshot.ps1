param(
    [string]$Collection = "agent_collection",
    [string]$SnapshotFile = "",
    [switch]$Replace
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $SnapshotFile) {
    $SnapshotFile = ".\..\agent_backend\data\vector_snapshots\$Collection.snapshot.json"
}

$snapshotPath = [System.IO.Path]::GetFullPath((Join-Path $scriptDir $SnapshotFile))
$mountedRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".\..\agent_backend\data"))
$normalizedSnapshot = $snapshotPath.Replace('/', '\')
$normalizedMountedRoot = $mountedRoot.Replace('/', '\')

if (-not $normalizedSnapshot.StartsWith($normalizedMountedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "SnapshotFile must be under agent\\agent_backend\\data so the backend container can access it. Current: $snapshotPath"
}

$relativeSnapshot = $snapshotPath.Substring($mountedRoot.Length).TrimStart('\')
$containerSnapshot = "/app/agent/agent_backend/data/" + ($relativeSnapshot -replace '\\','/')

if (-not (Test-Path $snapshotPath)) {
    throw "Milvus snapshot not found: $snapshotPath"
}

Push-Location $scriptDir
try {
    Write-Host "Importing Milvus collection '$Collection' from $snapshotPath"
    $cmd = @(
        "compose", "exec", "-T", "agent-backend",
        "python", "-m", "agent.agent_backend.database.vector.vector_migration",
        "import",
        "--collection", $Collection,
        "--snapshot", $containerSnapshot
    )
    if ($Replace) {
        $cmd += "--replace"
    }
    & docker @cmd

    if ($LASTEXITCODE -ne 0) {
        throw "Milvus import failed with exit code $LASTEXITCODE"
    }

    Write-Host "Milvus snapshot import completed: $snapshotPath"
}
finally {
    Pop-Location
}
