# Windows version of backup.sh. Backs up the Docker database and the encrypted uploads into
# backups\<date-time>\. Run from the repo root while docker compose is running:
#   powershell -ExecutionPolicy Bypass -File deploy\backup.ps1
#
# The backup holds health data: keep backups\ out of git (it is in .gitignore).
# DOCUMENT_ENCRYPTION_KEY is NOT in the backup on purpose; keep the key somewhere else.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Run([string[]]$cmd) {
    & docker @cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: docker $($cmd -join ' ')" }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path "backups" $stamp
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Write-Host "Backing up to $dest"

# Files are written inside the containers and copied out with "docker compose cp",
# because PowerShell's ">" would damage binary data.
Run @("compose", "exec", "-T", "db", "pg_dump", "-U", "postgres", "-d", "med_db", "-Fc", "-f", "/tmp/med_db.dump")
Run @("compose", "cp", "db:/tmp/med_db.dump", (Join-Path $dest "med_db.dump"))
Run @("compose", "exec", "-T", "db", "rm", "-f", "/tmp/med_db.dump")

Run @("compose", "exec", "-T", "api", "tar", "czf", "/tmp/uploads.tar.gz", "-C", "/app/backend/uploads", ".")
Run @("compose", "cp", "api:/tmp/uploads.tar.gz", (Join-Path $dest "uploads.tar.gz"))
Run @("compose", "exec", "-T", "api", "rm", "-f", "/tmp/uploads.tar.gz")

# Checksums in the same format as sha256sum, so restore.sh can check a Windows backup too
$lines = foreach ($name in "med_db.dump", "uploads.tar.gz") {
    "{0}  {1}" -f (Get-FileHash (Join-Path $dest $name) -Algorithm SHA256).Hash.ToLower(), $name
}
# LF line endings, so sha256sum -c on Linux can read it
[IO.File]::WriteAllText((Join-Path (Resolve-Path $dest) "SHA256SUMS"), (($lines -join "`n") + "`n"))

$size = (Get-ChildItem $dest | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Done: {0:N1} MB in {1}" -f $size, $dest)
