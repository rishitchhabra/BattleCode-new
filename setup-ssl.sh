#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SSL Setup — Let's Encrypt via Certbot
# ═══════════════════════════════════════════════════════════════
# Run this AFTER deploy.sh and AFTER pointing your domain to VPS
# Usage: chmod +x setup-ssl.sh && ./setup-ssl.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

DOMAIN="battlecodearena.gispilibhit.com"
EMAIL="admin@gispilibhit.com"

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
NC="\033[0m"

log()   { echo -e "${GREEN}[✔]${NC} $1"; }
warn()  { echo -e "${YELLOW}[⚠]${NC} $1"; }
error() { echo -e "${RED}[✖]${NC} $1"; exit 1; }
header(){ echo -e "\n${BOLD}═══ $1 ═══${NC}\n"; }

# ── Step 1: Verify domain points to this server ──────────────
header "Step 1: Domain Verification"

VPS_IP=$(curl -s ifconfig.me || echo "unknown")
DOMAIN_IP=$(dig +short "$DOMAIN" 2>/dev/null || echo "unresolved")

log "Your VPS IP:    $VPS_IP"
log "Domain points:  $DOMAIN_IP"

if [ "$VPS_IP" != "$DOMAIN_IP" ]; then
    warn "Domain $DOMAIN does not point to this server!"
    warn "Make sure DNS A record points to: $VPS_IP"
    read -p "Continue anyway? (y/N): " CONTINUE
    [[ ! "$CONTINUE" =~ ^[Yy]$ ]] && exit 1
fi

# ── Step 2: Get certificates ────────────────────────────────
header "Step 2: Obtaining SSL Certificate"

read -p "Enter your email for Let's Encrypt notifications [$EMAIL]: " USER_EMAIL
EMAIL="${USER_EMAIL:-$EMAIL}"

# Make sure nginx is running (HTTP mode)
docker compose up -d nginx

# Get the certificate
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"

if [ $? -ne 0 ]; then
    error "Failed to obtain SSL certificate!"
fi

log "SSL certificate obtained!"

# ── Step 3: Switch nginx to HTTPS mode ───────────────────────
header "Step 3: Enabling HTTPS in Nginx"

NGINX_CONF="nginx/nginx.conf"

# Backup current config
cp "$NGINX_CONF" "${NGINX_CONF}.bak"

# Write the HTTPS-enabled nginx config
cat > "$NGINX_CONF" << 'NGINX_EOF'
upstream django {
    server web:8000;
}

# ── Rate limiting ─────────────────────────────────────────────
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=login:10m rate=3r/s;

# ── HTTP → HTTPS redirect ────────────────────────────────────
server {
    listen 80;
    server_name battlecodearena.gispilibhit.com www.battlecodearena.gispilibhit.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# ── HTTPS server ─────────────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name battlecodearena.gispilibhit.com www.battlecodearena.gispilibhit.com;

    ssl_certificate     /etc/letsencrypt/live/battlecodearena.gispilibhit.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/battlecodearena.gispilibhit.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_stapling on;
    ssl_stapling_verify on;

    # ── Security headers ─────────────────────────────────────
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    client_max_body_size 50M;

    # ── Gzip ─────────────────────────────────────────────────
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript image/svg+xml;

    location /static/ {
        alias /app/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias /app/media/;
        expires 7d;
    }

    location /accounts/login/ {
        proxy_pass         http://django;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_redirect     off;
        limit_req zone=login burst=5 nodelay;
    }

    location / {
        proxy_pass         http://django;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_redirect     off;
        proxy_read_timeout 300;
        limit_req zone=general burst=20 nodelay;
    }
}
NGINX_EOF

log "Nginx HTTPS config written"

# ── Step 4: Enable secure cookies in Django ──────────────────
header "Step 4: Enabling Secure Cookies"

ENV_FILE=".env.prod"
sed -i 's/SECURE_SSL_REDIRECT=False/SECURE_SSL_REDIRECT=True/' "$ENV_FILE"
sed -i 's/SESSION_COOKIE_SECURE=False/SESSION_COOKIE_SECURE=True/' "$ENV_FILE"
sed -i 's/CSRF_COOKIE_SECURE=False/CSRF_COOKIE_SECURE=True/' "$ENV_FILE"

log "Secure cookies enabled in .env.prod"

# ── Step 5: Restart services ────────────────────────────────
header "Step 5: Restarting Services"

docker compose restart nginx web
sleep 3

# ── Step 6: Set up auto-renewal cron ────────────────────────
header "Step 6: Auto-Renewal Cron"

CRON_CMD="0 3 * * * cd $(pwd) && docker compose run --rm certbot renew --quiet && docker compose restart nginx"
(crontab -l 2>/dev/null | grep -v "certbot renew"; echo "$CRON_CMD") | crontab -
log "Auto-renewal cron job added (runs daily at 3 AM)"

# ── Done ─────────────────────────────────────────────────────
header "SSL Setup Complete!"

echo ""
log "🔒 HTTPS is now active!"
echo ""
echo "  Site:  https://$DOMAIN"
echo "  Admin: https://$DOMAIN/admin/"
echo ""
log "Certificate will auto-renew via cron."
echo ""
