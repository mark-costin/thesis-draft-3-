#!/usr/bin/env bash
# First HTTPS certificate from Let's Encrypt (run once on the server, from the repo root).
#   ./deploy/init_https.sh
# Needs in .env: DOMAIN (its DNS A record already points to this server), LETSENCRYPT_EMAIL,
# and COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml. After this, the certbot container
# renews the certificate by itself and Nginx reloads it every 6 hours.
set -euo pipefail
cd "$(dirname "$0")/.."

value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"' ; }
DOMAIN="$(value DOMAIN)"
EMAIL="$(value LETSENCRYPT_EMAIL)"
if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Set DOMAIN and LETSENCRYPT_EMAIL in .env first." >&2
    exit 1
fi
STAGING="${STAGING:-0}"   # STAGING=1 ./deploy/init_https.sh for a test certificate (no rate limit)

live="/etc/letsencrypt/live/$DOMAIN"

echo "1/4 Temporary certificate, so Nginx can start"
docker compose run --rm --no-deps --entrypoint sh certbot -c "
  mkdir -p $live &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout $live/privkey.pem -out $live/fullchain.pem -subj /CN=localhost"

echo "2/4 Starting the system with Nginx"
docker compose up -d --build

echo "3/4 Asking Let's Encrypt for the real certificate"
docker compose run --rm --no-deps --entrypoint sh certbot -c "
  rm -rf $live /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf"
staging_flag=""
[ "$STAGING" = "1" ] && staging_flag="--staging"
docker compose run --rm --no-deps --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
    $staging_flag -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email --rsa-key-size 4096

echo "4/4 Reloading Nginx"
docker compose exec nginx nginx -s reload

echo "Done: open https://$DOMAIN"
