# Windows version of restore.sh. Restores a backup into the running Docker containers:
#   powershell -ExecutionPolicy Bypass -File deploy\restore.ps1 backups\20261105-140000
# THIS REPLACES the current database and uploaded files. The .env on this computer must have
# the same DOCUMENT_ENCRYPTION_KEY as the computer the backup came from.
param([Parameter(Mandatory = $true)][string]$Source)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Run([string[]]$cmd) {
    & docker @cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: docker $($cmd -join ' ')" }
}

foreach ($name in "med_db.dump", "uploads.tar.gz", "SHA256SUMS") {
    if (-not (Test-Path (Join-Path $Source $name))) { throw "No backup in $Source (missing $name)" }
}

Write-Host "Checking the backup files..."
foreach ($line in Get-Content (Join-Path $Source "SHA256SUMS") | Where-Object { $_.Trim() }) {
    $expected, $name = $line -split "\s+", 2
    $actual = (Get-FileHash (Join-Path $Source $name) -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $expected) { throw "$name is damaged (checksum does not match)" }
    Write-Host "  $name OK"
}

$answer = Read-Host "This REPLACES the database and uploads with $Source. Type RESTORE to continue"
if ($answer -cne "RESTORE") { Write-Host "Cancelled."; exit 1 }

# Stop the app so nothing writes during the restore
Run @("compose", "stop", "api", "worker")

Run @("compose", "cp", (Join-Path $Source "med_db.dump"), "db:/tmp/restore.dump")
Run @("compose", "exec", "-T", "db", "pg_restore", "-U", "postgres", "-d", "med_db", "--clean", "--if-exists",
      "--single-transaction", "--exit-on-error", "/tmp/restore.dump")
Run @("compose", "exec", "-T", "db", "rm", "-f", "/tmp/restore.dump")

Run @("compose", "start", "api", "worker")

Run @("compose", "cp", (Join-Path $Source "uploads.tar.gz"), "api:/tmp/restore.tar.gz")
Run @("compose", "exec", "-T", "api", "sh", "-c",
      "find /app/backend/uploads -mindepth 1 -delete && tar xzf /tmp/restore.tar.gz -C /app/backend/uploads")
Run @("compose", "exec", "-T", "-u", "root", "api", "rm", "-f", "/tmp/restore.tar.gz")

Write-Host "Checking the restored database..."
Run @("compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "med_db", "-Atc",
      "SELECT 'tables: ' || count(*) FROM pg_tables WHERE schemaname = 'public' UNION ALL SELECT 'patients: ' || count(*) FROM patients UNION ALL SELECT 'saved reports: ' || count(*) FROM health_reports UNION ALL SELECT 'audit chain broken at entry: ' || coalesce(verify_audit_chain()::text, 'none (intact)')")
Write-Host "Restore finished."
