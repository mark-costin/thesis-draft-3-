# Deploying to a DigitalOcean server (Sprint 8)

This puts the whole system on one DigitalOcean droplet, as in Chapter 3 (3.2.1, Deployment and
Networking Layer; 3.2.3, Server Hardening):

- Every part runs as a Docker Compose container: PostgreSQL, Redis, the API, the OCR worker, the
  patient portal (Reflex) and **Nginx**.
- **Nginx is the only way in**, on ports 80 and 443, with an HTTPS certificate from **Let's Encrypt**.
  The portal, the API, PostgreSQL and Redis have no ports open to the internet.
- The firewall allows only **22 (SSH), 80 and 443**, and SSH accepts **keys only**.
- A **scheduled backup** (cron) saves the database and the encrypted files every night.

```
Browser ──HTTPS──> Nginx ──> web (Reflex: pages on 3000, WebSocket on 8001) ──> api (FastAPI)
                                                                                  │
                                       worker (OCR) <── Redis ──────────── PostgreSQL (RLS)
```

The browser never calls the API directly: Reflex calls it from Python inside Docker.

## What you need

- A DigitalOcean account, and a droplet: **Ubuntu 24.04**, at least **2 GB RAM** (4 GB if OCR runs
  on it), with your **SSH key** added when you create it.
- A domain name (for example `cdss.example.com`), with an **A record** pointing to the droplet's IP.
  Let's Encrypt only gives certificates to a domain name, not to an IP address.

## 1. Prepare the server (once)

From your own computer:

```
ssh root@<droplet IP>
git clone https://github.com/mark-costin/thesis-draft-3-.git /srv/thesis
cd /srv/thesis
bash deploy/server_setup.sh
```

This installs Docker, turns on the firewall (22, 80, 443), turns off password logins for SSH
(only if your key is on the server), and turns on automatic security updates.
Open a **second** terminal and check that `ssh root@<droplet IP>` still works before you close the first.

## 2. Settings (.env on the server)

```
cp .env.example .env
nano .env
```

Fill in, with new long random values (never the ones from your laptop):

| Setting | Value |
|---|---|
| `JWT_SECRET_KEY`, `DOCUMENT_ENCRYPTION_KEY` | New values (see the comments in `.env.example`) |
| `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `REDIS_PASSWORD` | New values |
| `DOMAIN` | `cdss.example.com` |
| `LETSENCRYPT_EMAIL` | Your email (Let's Encrypt writes before a certificate expires) |
| `PUBLIC_URL` | `https://cdss.example.com` |
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml` |
| `RISK_MODELS_ENABLED` | `true` once the models are in `ml/artifacts/` (step 5) |
| `SHOW_DRAFT_CARE_PLANS` | `false` for real patients; `true` only for a demo |
| `PRINT_RESET_LINKS`, `LOG_ERROR_DETAILS` | `false` (docker-compose.yml forces both anyway) |
| `GOOGLE_CLIENT_ID` | Optional. Add `https://cdss.example.com` to the client's JavaScript origins |

Keep a copy of `DOCUMENT_ENCRYPTION_KEY` somewhere safe and **outside** the server: without it the
uploaded files in a backup cannot be read.

## 3. Start with HTTPS (once)

```
./deploy/init_https.sh
```

It starts everything with a temporary certificate, asks Let's Encrypt for the real one, and reloads
Nginx. The first run builds the images, which takes several minutes. Then open `https://<DOMAIN>`.

To try it without using up Let's Encrypt's limit: `STAGING=1 ./deploy/init_https.sh` (the browser
will warn about the certificate), then run it again without `STAGING=1`.

After this, the `certbot` container renews the certificate by itself, and Nginx reloads it every 6 hours.

## 4. Care plan templates

Run by hand as `postgres`, as in `database/README.md`:

```
docker compose exec -T db psql -U postgres -d med_db < database/seed_care_plan_templates.sql
docker compose exec -T db psql -U postgres -d med_db < database/approve_care_plan_templates_v1_0.sql
```

Run the second one only after its placeholders are filled in from the physician's signed form.

## 5. Models

Copy the final model files from the training laptop into `ml/artifacts/` on the server
(for example with `scp`), register them as `postgres` with `ml/artifacts/register_models.sql`, set
`RISK_MODELS_ENABLED=true`, and restart the API:

```
docker compose exec -T db psql -U postgres -d med_db < ml/artifacts/register_models.sql
docker compose up -d api worker
```

The API checks every model file against its registered SHA-256 checksum at startup.

## 6. Nightly backup

```
crontab -e
```

Add this line (every night at 2:15):

```
15 2 * * * cd /srv/thesis && ./deploy/backup.sh >> backups/backup.log 2>&1
```

Copy the `backups/` folder to another computer from time to time. Restore: `docs/docker.md`, section 4.

## 7. Checks after deploying

| Check | Command or action | Expected |
|---|---|---|
| All containers up | `docker compose ps` | `db`, `redis`, `api` healthy; `web`, `worker`, `nginx`, `certbot` running |
| HTTPS | Open `https://<DOMAIN>` | The login page, with a padlock |
| HTTP goes to HTTPS | Open `http://<DOMAIN>` | Redirected to `https://` |
| Database not open | From your laptop: `nc -zv <droplet IP> 5432` | Refused or timed out |
| API not open | From your laptop: `nc -zv <droplet IP> 8000` | Refused or timed out |
| Firewall | On the server: `ufw status` | Only 22, 80, 443 allowed |
| SSH keys only | `ssh -o PubkeyAuthentication=no root@<droplet IP>` | `Permission denied (publickey)` |
| Certificate renewal | `docker compose run --rm --entrypoint certbot certbot renew --dry-run` | `Congratulations, all simulated renewals succeeded` |

## Updating the server

```
cd /srv/thesis
git pull --ff-only
docker compose up -d --build
```

Run any new numbered script in `database/` as `postgres` first, as its comment says.

## Known limits

- **Password reset emails**: there is no mail server yet, so reset links cannot be sent on the
  server (`PRINT_RESET_LINKS` is off there). Until then, the administrator resets a password by hand.
- **Rate limits**: every request reaches the API from the Reflex container, so all patients share
  one login limit (10 per minute). Enough for the evaluation; see docs/docker.md, "Still to do".
- **OCR**: the API image has no PaddleOCR (`OCR_ENGINE=pdf_text`): PDFs with a text layer are read,
  photos and scans ask the patient to type the values. PaddleOCR-VL needs a GPU or a bigger droplet.
