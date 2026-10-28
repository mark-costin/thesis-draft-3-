# create_test_db.ps1
# Purpose: Windows version of create_test_db.sh. (Re)builds med_db_test from the scripts in
# database/, so pytest never touches med_db.
# Run from the repo root in PowerShell:
#     powershell -ExecutionPolicy Bypass -File tests\create_test_db.ps1
# Safe to rerun: it drops and recreates med_db_test only.
# Works in Windows PowerShell 5.1 and PowerShell 7.

$ErrorActionPreference = "Stop"
$DB = "med_db_test"

# --- Find psql: PATH first, then the newest PostgreSQL install in Program Files -------------
$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql) {
    $found = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
             Sort-Object { $_.Directory.Parent.Name -as [int] } -Descending |
             Select-Object -First 1
    if ($found) { $psql = $found.FullName }
}
if (-not $psql) {
    Write-Host "psql not found. Add PostgreSQL's bin folder to PATH, e.g. C:\Program Files\PostgreSQL\18\bin" -ForegroundColor Red
    exit 1
}
Write-Host "Using $psql"

# --- Ask for the postgres password once ----------------------------------------------------
$secure = Read-Host "postgres password" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
$env:PGCLIENTENCODING = "UTF8"   # read the .sql files as UTF-8, not the Windows code page

function Invoke-Psql {
    param([string]$Database, [string[]]$Arguments)
    & $psql -h localhost -U postgres -v ON_ERROR_STOP=1 -q -d $Database @Arguments
    if ($LASTEXITCODE -ne 0) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
        Write-Host "Stopped: psql failed (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

try {
    $dbDir = Join-Path $PSScriptRoot "..\database" | Resolve-Path
    Push-Location $dbDir

    Write-Host "Recreating $DB"
    Invoke-Psql "postgres" @("-c", "DROP DATABASE IF EXISTS $DB WITH (FORCE)")
    Invoke-Psql "postgres" @("-c", "CREATE DATABASE $DB")

    # 00_roles.sh creates med_app, but roles belong to the whole server and med_app already
    # exists from med_db. Only the database-level part of 00_roles.sh is needed here.
    $grants = @"
REVOKE CONNECT ON DATABASE $DB FROM PUBLIC;
GRANT CONNECT ON DATABASE $DB TO med_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
"@
    Invoke-Psql $DB @("-c", ($grants -replace "\r?\n", " "))

    # Numbered scripts in order (01 ... 21, then 99), then the draft care plan templates
    $scripts = Get-ChildItem -File | Where-Object { $_.Name -match '^\d\d_.*\.sql$' } | Sort-Object Name
    foreach ($f in $scripts) {
        Write-Host "  $($f.Name)"
        Invoke-Psql $DB @("-f", $f.Name)
    }
    Write-Host "  seed_care_plan_templates.sql"
    Invoke-Psql $DB @("-f", "seed_care_plan_templates.sql")

    $tables = & $psql -h localhost -U postgres -q -At -d $DB -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    Write-Host "Done: $DB has $tables tables (expected 19)" -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Pop-Location -ErrorAction SilentlyContinue
}