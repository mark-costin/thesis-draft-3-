# Running the backend with Docker

Two compose files:

| File | Starts | Use it when |
| --- | --- | --- |
| `docker-compose.yml` | PostgreSQL 18, Redis, the API and the OCR worker | You want the whole backend in containers (the way it will run on the server) |
| `docker-compose.dev.yml` | PostgreSQL 18 and Redis only | You run the API yourself with `uv run uvicorn ...` and only need the database |

The frontend container will be added when the frontend is in the repository.

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

## How it is protected

- **Secrets:** every password comes from `.env`. The compose files contain none, and `.dockerignore` keeps `.env` out of the images.
- **Least privilege:** the API connects as `med_app`, created by `00_roles.sh` with `APP_DB_PASSWORD`. It never connects as `postgres`.
- **Init scripts:** only the numbered scripts (`00_roles.sh`, `01`–`21`, `99`) are copied into the database image (`docker/Dockerfile.db`). Mounting the whole `database/` folder would also run `windows_setup.sql` and the draft templates.
- **Networks:** Postgres and Redis are only on the `data` network, which is `internal` (no way in or out). The API is on `data` and `web`. The frontend will join only `web`, so it can never reach the database directly.
- **Ports:** in `docker-compose.yml` only the API is published, and only on `127.0.0.1:8000`, so other computers cannot connect. Postgres and Redis are not published at all.
- **Redis** needs `REDIS_PASSWORD`.
- **Containers run as a normal user** (`app`, uid 10001), not root.
- **Model files** are mounted read-only from `ml/artifacts`, and the API still checks their SHA-256 before loading.
- **Line endings:** `.gitattributes` keeps `*.sh` files with LF endings. With Windows CRLF endings, `00_roles.sh` fails inside the container.

## Still to do (Sprint 8)

- Frontend container (`docker/Dockerfile.web`) on the `web` network.
- Nginx with Let's Encrypt HTTPS in front of the API and frontend, then start uvicorn with `--proxy-headers` so the rate limits see the real client IP.
- Chandra OCR inside the worker image. It needs a GPU, so it is for Costin's laptop only. Other machines keep `OCR_ENGINE=none`.
- Backups: `pg_dump` of the database and a copy of the `uploads` volume.
