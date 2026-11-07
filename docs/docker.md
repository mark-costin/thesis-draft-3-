# Running the backend with Docker

Two compose files:

| File | Starts | Use it when |
| --- | --- | --- |
| `docker-compose.yml` | PostgreSQL 18, Redis, the API and the OCR worker | You want the whole backend in containers (the way it will run on the server) |
| `docker-compose.dev.yml` | PostgreSQL 18 and Redis only | You run the API yourself with `uv run uvicorn ...` and only need the database |

`docker-compose.yml` also starts the patient portal (`web`): open http://localhost:3000.
On the server, `docker-compose.prod.yml` adds Nginx with HTTPS: see `docs/deploy.md`.

## 1. Before the first start

1. Install Docker Desktop (Windows: it uses WSL 2) or Docker Engine (Linux).
2. Add the three Docker passwords to your `.env` (see the end of `.env.example`). Use your own long random values:

   ```
   uv run python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

   `.env` also needs `JWT_SECRET_KEY` and `DOCUMENT_ENCRYPTION_KEY`, the same as without Docker.
   Never commit `.env`.

## 2. Start the full backend

```
docker compose up -d --build
docker compose ps
```

- The first start builds the images (several minutes) and creates the 19 tables.
- `db` and `redis` should show `healthy`, and `api` should show `healthy` after about 20 seconds.
- Open http://localhost:8000/health. It should show `{"status":"ok"}`.
- Open http://localhost:8000/health/db. It should show `{"database":"ok"}`.

Logs: `docker compose logs -f api` (or `worker`, `db`). Stop: `docker compose down`.
The data stays in the `pgdata` and `uploads` volumes until you run `docker compose down -v`,
which **deletes the database and every uploaded file**.

## 3. Load the draft care plan templates (demos only)

Docker never loads the drafts by itself, because they are not yet approved by the physician.
For a demo, load them by hand:

```
docker compose exec -T db psql -U postgres -d med_db < database/seed_care_plan_templates.sql
```

## 4. Backup and restore

| | Windows | Linux / server |
| --- | --- | --- |
| Back up | `powershell -ExecutionPolicy Bypass -File deploy\backup.ps1` | `./deploy/backup.sh` |
| Restore | `powershell -ExecutionPolicy Bypass -File deploy\restore.ps1 backups\<date-time>` | `./deploy/restore.sh backups/<date-time>` |

- A backup is a folder `backups/<date-time>/` with `med_db.dump` (the whole database, `pg_dump -Fc`),
  `uploads.tar.gz` (the encrypted files) and `SHA256SUMS`.
- The restore checks the checksums first, asks you to type `RESTORE`, stops the API and worker,
  replaces the database in one transaction (all or nothing), and puts back the files. Then it
  prints the number of tables, patients and saved reports, and checks the audit chain
  (`verify_audit_chain()`).
- **The backup holds health data.** `backups/` is in `.gitignore`. Copy it to a second place
  (another disk or computer), not to a public cloud folder.
- **`DOCUMENT_ENCRYPTION_KEY` is not in the backup, on purpose.** Keep it somewhere else. Without
  the same key in `.env`, the restored files cannot be opened.
- On the server, `backup.sh` deletes backups older than 14 days (`KEEP_DAYS`). Run it every
  night with cron (see the top of the script).

**Restore test (Sprint 8 "Done when"):** make a backup on one computer, copy the folder to
another computer that runs the same compose files with the same `DOCUMENT_ENCRYPTION_KEY`,
restore it there, and check that the counts match and an uploaded file still downloads.

## How it is protected

- **Secrets:** every password comes from `.env`. The compose files contain none, and `.dockerignore` keeps `.env` out of the images.
- **Least privilege:** the API connects as `med_app`, created by `00_roles.sh` with `APP_DB_PASSWORD`. It never connects as `postgres`.
- **No superuser password in the app containers:** `env_file: .env` passes the whole `.env`, so `docker-compose.yml` overrides `POSTGRES_PASSWORD` with an empty value for the API and the worker. Someone who gets code running in the API container cannot read it and connect as `postgres`.
- **OCR in Docker:** the image has no PaddleOCR, so the containers use `OCR_ENGINE=pdf_text` (lab PDFs that already contain text), whatever the local `.env` says; photos and scanned PDFs are marked "please type the values in". Set `DOCKER_OCR_ENGINE=none` in `.env` to switch OCR off in Docker.
- **No health data in the container logs:** in a container the console is the log, so `docker-compose.yml` sets `PRINT_RESET_LINKS=false` and `LOG_ERROR_DETAILS=false` for the API and the worker, whatever the local `.env` says (Sprint 6, PR #38).
- **Init scripts:** only the numbered scripts (`00_roles.sh`, `01`–`21`, `99`) are copied into the database image (`docker/Dockerfile.db`). Mounting the whole `database/` folder would also run `windows_setup.sql` and the draft templates.
- **Networks:** Postgres and Redis are only on the `data` network, which is `internal` (no way in or out). The API is on `data` and `web`. The frontend will join only `web`, so it can never reach the database directly.
- **Ports:** in `docker-compose.yml` only the API is published, and only on `127.0.0.1:8000`, so other computers cannot connect. Postgres and Redis are not published at all.
- **Redis** needs `REDIS_PASSWORD`.
- **Containers run as a normal user** (`app`, uid 10001), not root.
- **Model files** are mounted read-only from `ml/artifacts`, and the API still checks their SHA-256 before loading.
- **Line endings:** `.gitattributes` keeps `*.sh` files with LF endings. With Windows CRLF endings, `00_roles.sh` fails inside the container.

## Still to do (Sprint 8)

- Done: the frontend container (`docker/Dockerfile.web`) on the `web` network, and Nginx with
  Let's Encrypt HTTPS in front of it (`docker-compose.prod.yml`, `docs/deploy.md`). The API is not
  published at all: only Reflex calls it, from inside Docker.
- The API's rate limits count requests per IP address. Every request comes from the Reflex
  container, so all patients share one limit (login 10 per minute). Fine for a demo and the
  evaluation; for real use, Reflex should pass the browser's address (X-Forwarded-For) and the
  API should trust it from the web container only.
- PaddleOCR-VL inside the worker image (docs/ocr.md). It needs an NVIDIA GPU, so it is for Costin's laptop only. `OCR_ENGINE=pdf_text` already works in the image (no GPU); other machines keep `none` or `pdf_text`.
- Copy the nightly backups off the server automatically.
