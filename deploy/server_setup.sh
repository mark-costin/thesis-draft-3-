#!/usr/bin/env bash
# One-time setup of a new Ubuntu server (DigitalOcean droplet or AWS EC2), as root. See docs/deploy.md.
#   sudo bash deploy/server_setup.sh
# Installs Docker, turns on the firewall (only SSH 22, HTTP 80, HTTPS 443), allows SSH with keys
# only, and turns on automatic security updates. Safe to run twice.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

echo "1/4 System updates"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install ufw unattended-upgrades curl git

echo "2/4 Docker"
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
# The login user (e.g. "ubuntu" on AWS) can run docker without sudo after logging in again
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    usermod -aG docker "$SUDO_USER"
fi
# 2 GB swap: building the portal (Bun + Node) and the API image needs more than a 2 GB server has
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "3/4 Firewall: 22, 80, 443 only"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
# Note: Docker publishes ports past ufw. docker-compose.prod.yml publishes only Nginx (80, 443);
# PostgreSQL and Redis have no published ports and live on an internal Docker network.

echo "4/4 SSH: keys only"
# DigitalOcean puts the key in root's folder; AWS puts it in the login user's (ubuntu)
keyfile=/root/.ssh/authorized_keys
[ -n "${SUDO_USER:-}" ] && [ -s "/home/$SUDO_USER/.ssh/authorized_keys" ] && keyfile="/home/$SUDO_USER/.ssh/authorized_keys"
if [ -s "$keyfile" ]; then
    cat > /etc/ssh/sshd_config.d/99-keys-only.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
    systemctl reload ssh || systemctl reload sshd
    echo "Password logins are now off. Keep your SSH key safe."
else
    echo "No SSH key found: password logins left ON." >&2
    echo "Add your key and run this script again." >&2
fi

dpkg-reconfigure -f noninteractive unattended-upgrades
ufw status verbose
echo "Server ready. Next: docs/deploy.md, step 3."
