# setup_windows.ps1
# Purpose: Builds med_db (or med_db_test) on Windows from the real scripts in this folder.
# Windows version of 00_roles.sh + running every script in order. No Docker, no copied SQL:
# a new or changed script in database/ is picked up automatically.
#
# Run from the repo root in PowerShell:
#     powershell -ExecutionPolicy Bypass -File database\setup_windows.ps1          # main database: med_db
#     powershell -ExecutionPolicy Bypass -File database\setup_windows.ps1 -Test    # test database: med_db_test
#
# med_db:      created only if it does not exist yet (your data is never dropped).
#              Add -Recreate to drop and rebuild it on purpose.
# med_db_test: always dropped and rebuilt (safe: tests create and delete their own data).
# Works in Windows PowerShell 5.1 and PowerShell 7.

param(
    [switch]$Test,
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$DB = "med_db"
if ($Test) { $DB = "med_db_test" }

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

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Stop-Setup([string]$Message) {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Write-Host $Message -ForegroundColor Red
    exit 1
}

# Runs psql against one database; stops the whole setup on the first error
function Invoke-Psql {
    param([string]$Database, [string[]]$Arguments)
    & $psql -h localhost -U postgres -v ON_ERROR_STOP=1 -q -d $Database @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-Setup "Stopped: psql failed (exit code $LASTEXITCODE)" }
}

# Runs one query and returns its single value
function Get-PsqlValue([string]$Database, [string]$Query) {
    $value = & $psql -h localhost -U postgres -q -At -d $Database -c $Query
    if ($LASTEXITCODE -ne 0) { Stop-Setup "Stopped: could not run: $Query" }
    return "$value".Trim()
}

$env:PGPASSWORD = Read-Secret "postgres password"
$env:PGCLIENTENCODING = "UTF8"   # read the .sql files as UTF-8, not the Windows code page

try {
    Push-Location $PSScriptRoot

    # --- 1. med_app role (server-wide, shared by med_db and med_db_test) -------------------
    $roleExists = Get-PsqlValue "postgres" "SELECT count(*) FROM pg_roles WHERE rolname = 'med_app'"
    if ($roleExists -eq "0") {
        Write-Host "Creating role med_app (this password goes in DATABASE_URL in your .env)"
        $appPw = Read-Secret "new med_app password"
        $appPw2 = Read-Secret "repeat med_app password"
        if ($appPw -ne $appPw2) { Stop-Setup "Passwords do not match." }
        if ($appPw.Length -lt 8) { Stop-Setup "Use at least 8 characters." }
        # Same role settings as 00_roles.sh. The password is passed as a psql variable
        # (:'app_pw' quotes it safely) and is never written to a file.
        $createRole = "CREATE ROLE med_app LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;"
        $createRole | & $psql -h localhost -U postgres -v ON_ERROR_STOP=1 -q -d postgres -v "app_pw=$appPw"
        if ($LASTEXITCODE -ne 0) { Stop-Setup "Stopped: could not create med_app" }
        $appPw = $null; $appPw2 = $null
    } else {
        Write-Host "Role med_app already exists (password unchanged)"
    }

    # --- 2. The database ------------------------------------------------------------------
    $dbExists = Get-PsqlValue "postgres" "SELECT count(*) FROM pg_database WHERE datname = '$DB'"
    if ($dbExists -ne "0") {
        if ($Test -or $Recreate) {
            Write-Host "Dropping $DB"
            Invoke-Psql "postgres" @("-c", "DROP DATABASE $DB WITH (FORCE)")
        } else {
            Stop-Setup "$DB already exists, so nothing was changed. To rebuild it (this DELETES its data), add -Recreate."
        }
    }
    Write-Host "Creating $DB"
    Invoke-Psql "postgres" @("-c", "CREATE DATABASE $DB")

    # Database-level part of 00_roles.sh
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

    # --- 3. Every numbered script in order (01 ... 21, then 99), then the draft templates ---
    $scripts = Get-ChildItem -File | Where-Object { $_.Name -match '^\d\d_.*\.sql$' } | Sort-Object Name
    foreach ($f in $scripts) {
        Write-Host "  $($f.Name)"
        Invoke-Psql $DB @("-f", $f.Name)
    }
    Write-Host "  seed_care_plan_templates.sql"
    Invoke-Psql $DB @("-f", "seed_care_plan_templates.sql")

    $tables = Get-PsqlValue $DB "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    $templates = Get-PsqlValue $DB "SELECT count(*) FROM care_plan_templates"
    Write-Host "Done: $DB has $tables tables (expected 19) and $templates care plan templates (expected 12)" -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Pop-Location -ErrorAction SilentlyContinue
}