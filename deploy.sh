#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# BattleCode Arena — Hostinger VPS Deployment Script
# ═══════════════════════════════════════════════════════════════
# SSL certs already exist from previous deployment.
# This script rebuilds and restarts all services.
#
# Usage:
#   1. SSH into your Hostinger VPS
#   2. cd /path/to/battlecode
#   3. chmod +x deploy.sh && ./deploy.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
NC="\033[0m"

log()   { echo -e "${GREEN}[✔]${NC} $1"; }
warn()  { echo -e "${YELLOW}[⚠]${NC} $1"; }
error() { echo -e "${RED}[✖]${NC} $1"; exit 1; }
header(){ echo -e "\n${BOLD}═══ $1 ═══${NC}\n"; }

# ── Pre-flight checks ────────────────────────────────────────
header "Pre-flight Checks"

if ! command -v docker &> /dev/null; then
    warn "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    log "Docker installed. You may need to re-login for group changes."
fi

if ! docker compose version &> /dev/null; then
    warn "Docker Compose plugin not found. Installing..."
    sudo apt-get update && sudo apt-get install -y docker-compose-plugin
    log "Docker Compose installed."
fi

log "Docker: $(docker --version)"
log "Docker Compose: $(docker compose version)"

# ── Validate .env.prod ───────────────────────────────────────
header "Environment Check"

if [ ! -f .env.prod ]; then
    error ".env.prod not found!"
fi

log ".env.prod found"

# ── Create required directories ──────────────────────────────
header "Creating Directories"

mkdir -p certbot/www certbot/conf nginx logs media staticfiles
log "Directories created"

# ── Check SSL certs ──────────────────────────────────────────
header "SSL Certificate Check"

if [ -d "certbot/conf/live/battlecodearena.gispilibhit.com" ]; then
    log "SSL certificates found from previous deployment ✓"
else
    warn "SSL certificates NOT found in certbot/conf/"
    warn "Make sure to copy them from your old server or run certbot manually"
    warn "  docker compose run --rm certbot certonly --webroot --webroot-path=/var/www/certbot -d battlecodearena.gispilibhit.com -d www.battlecodearena.gispilibhit.com"
fi

# ── Pull executor images ─────────────────────────────────────
header "Pulling Docker Images"

docker pull python:3.11-alpine || warn "Could not pull python:3.11-alpine (will retry on first use)"
log "Python executor image ready"

docker pull openjdk:17-alpine || warn "Could not pull openjdk:17-alpine (will retry on first use)"
log "Java executor image ready"

# ── Stop old containers ──────────────────────────────────────
header "Stopping Old Containers"

docker compose down --remove-orphans 2>/dev/null || true
log "Old containers stopped"

# ── Build & Start ────────────────────────────────────────────
header "Building & Starting Services"

docker compose build --no-cache
docker compose up -d

# ── Wait for services ────────────────────────────────────────
header "Waiting for Services"

echo -n "Waiting for database"
for i in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U codearena &>/dev/null; then
        echo ""
        log "Database is ready!"
        break
    fi
    echo -n "."
    sleep 2
done

sleep 5  # Let Django finish migrations

# ── Health check ─────────────────────────────────────────────
header "Health Checks"

echo -n "Checking web server"
for i in $(seq 1 15); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost 2>/dev/null || echo "000")
    if echo "$HTTP_CODE" | grep -qE "200|301|302"; then
        echo ""
        log "Web server responding (HTTP $HTTP_CODE)"
        break
    fi
    echo -n "."
    sleep 2
done

# ── Status ───────────────────────────────────────────────────
header "Deployment Status"

docker compose ps

VPS_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
log "🚀 BattleCode Arena is LIVE!"
echo ""
echo "  HTTPS: https://battlecodearena.gispilibhit.com"
echo "  Admin: https://battlecodearena.gispilibhit.com/admin/"
echo "  VPS:   http://$VPS_IP"
echo ""

# ── Certbot auto-renewal cron ────────────────────────────────
CRON_CMD="0 3 * * * cd $(pwd) && docker compose run --rm certbot renew --quiet && docker compose restart nginx"
if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    log "SSL auto-renewal cron added (daily 3 AM)"
else
    log "SSL auto-renewal cron already exists ✓"
fi

echo ""
