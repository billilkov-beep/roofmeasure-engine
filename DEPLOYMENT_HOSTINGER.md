# Deployment to Hostinger — `roofmeasure-engine`

This repo deploys to a **Hostinger VPS** (KVM 2 or higher). The companion
`roofmeasure-portal` deploys separately to Hostinger Node hosting and is
documented in its own repo.

## Target

| Component | Hostinger product | Domain |
|---|---|---|
| **Engine API** | VPS (KVM 2+) | `roofmeasure.canadasroofer.com` |
| **Build command** | None (Python, no compile step) | — |
| **Output folder** | None (runs in place from `/home/roofmeasure/engine/`) | — |
| **Production branch** | `main` | — |
| **Start command** | `systemctl start roofmeasure-engine` | — |
| **Service port** | 8080 (loopback only; nginx terminates TLS on 443) | — |
| **Health check path** | `GET /v1/health` | returns `{"ok": true}` |

## 1. One-time VPS setup

```bash
ssh root@roofmeasure.canadasroofer.com

# System packages
apt update
apt install -y python3.10 python3.10-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    pdal libpdal-dev \
    inotify-tools git curl unzip

# Service user
adduser --disabled-password --gecos "" roofmeasure
mkdir -p /var/lib/roofmeasure /var/cache/roofmeasure /var/log/roofmeasure
chown -R roofmeasure:roofmeasure /var/lib/roofmeasure /var/cache/roofmeasure /var/log/roofmeasure

# Clone + venv
su - roofmeasure -c "git clone <repo-url> /home/roofmeasure/engine"
su - roofmeasure -c "cd /home/roofmeasure/engine && \
    python3.10 -m venv venv && \
    venv/bin/pip install --upgrade pip && \
    venv/bin/pip install -r requirements.txt"

# Env file (fill in real values — see .env.example)
sudo -u roofmeasure tee /home/roofmeasure/engine/.env > /dev/null <<ENV
ENGINE_API_KEY=<generate-with-openssl-rand-hex-32>
GOOGLE_API_KEY=<from-google-cloud-console>
DEFAULT_STRATEGY=auto
LOG_LEVEL=INFO
USAGE_DB_PATH=/var/lib/roofmeasure/usage.db
CACHE_DIR_LIDAR=/var/cache/roofmeasure/lidar
CACHE_DIR_MS_BUILDINGS=/var/cache/roofmeasure/ms-buildings
ENV
chmod 600 /home/roofmeasure/engine/.env
```

## 2. systemd units

`/etc/systemd/system/roofmeasure-engine.service`:

```ini
[Unit]
Description=RoofMeasure FastAPI engine
After=network.target

[Service]
User=roofmeasure
WorkingDirectory=/home/roofmeasure/engine
EnvironmentFile=/home/roofmeasure/engine/.env
ExecStart=/home/roofmeasure/engine/venv/bin/uvicorn \
    roofmeasure.api.main:app \
    --host 127.0.0.1 --port 8080 \
    --workers 2 \
    --timeout-keep-alive 30 \
    --limit-max-requests 1000
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/roofmeasure/engine.log
StandardError=append:/var/log/roofmeasure/engine.log

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/roofmeasure-auto-test.service` (optional, dev-only):

```ini
[Unit]
Description=RoofMeasure auto-test watcher
After=network.target

[Service]
User=root
ExecStart=/usr/local/bin/roofmeasure-auto-test.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

The auto-test script is in `deploy/auto-test.sh` (gets installed to
`/usr/local/bin/roofmeasure-auto-test.sh` by `MASTER_DEPLOY.sh`).

Enable services:

```bash
systemctl daemon-reload
systemctl enable --now roofmeasure-engine
systemctl enable --now roofmeasure-auto-test   # dev VPS only
```

## 3. nginx vhost

`/etc/nginx/sites-available/roofmeasure`:

```nginx
limit_req_zone $binary_remote_addr zone=engine:10m rate=30r/m;

server {
    listen 443 ssl http2;
    server_name roofmeasure.canadasroofer.com;

    ssl_certificate     /etc/letsencrypt/live/roofmeasure.canadasroofer.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/roofmeasure.canadasroofer.com/privkey.pem;

    limit_req zone=engine burst=10 nodelay;
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}

server {
    listen 80;
    server_name roofmeasure.canadasroofer.com;
    return 301 https://$host$request_uri;
}
```

Enable + cert:

```bash
ln -s /etc/nginx/sites-available/roofmeasure /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d roofmeasure.canadasroofer.com
```

## 4. Ongoing deploys

The deploy is a single SSH paste. `deploy/MASTER_DEPLOY.sh` is idempotent.

```bash
# From your laptop:
scp deploy/MASTER_DEPLOY.sh root@roofmeasure.canadasroofer.com:/tmp/
ssh root@roofmeasure.canadasroofer.com 'bash /tmp/MASTER_DEPLOY.sh'
```

What `MASTER_DEPLOY.sh` does:
1. Snapshot current state to `/tmp/engine_pre_master_<ts>.tar.gz`
2. Pre-flight check of external providers (TNM, Overpass, Solar, NRCan, PC STAC)
3. `git pull` in `/home/roofmeasure/engine`
4. `venv/bin/pip install -r requirements.txt`
5. Apply any pending in-place patches (idempotent — uses marker comments)
6. `systemctl restart roofmeasure-engine`
7. Run the ground-truth harness
8. Print summary + per-address results

## 5. Environment variables

See `.env.example` for the full list. Never commit a real value. Use
`openssl rand -hex 32` to generate `ENGINE_API_KEY` and any other
secret-style variables.

## 6. Rollback

Every `MASTER_DEPLOY.sh` run creates a backup tarball.

```bash
ssh root@roofmeasure.canadasroofer.com
ls -lt /tmp/engine_pre_master_*.tar.gz | head -1
sudo tar xzf /tmp/engine_pre_master_<ts>.tar.gz -C /home/roofmeasure/engine
sudo systemctl restart roofmeasure-engine
```

For schema changes (rare), the SQLite usage.db is also snapshotted to
`/tmp/usage_pre_master_<ts>.db` during deploy.

## 7. Monitoring

- **Engine uptime:** point UptimeRobot at
  `https://roofmeasure.canadasroofer.com/v1/health` every 5 min.
- **Logs:** `journalctl -u roofmeasure-engine -f`
- **Auto-test log:** `tail -f /tmp/auto_test_watch.log`
- **Last harness summary:**
  `grep "SUMMARY" /tmp/auto_test_watch.log | tail -5`

## 8. Updating TLS certs

Let's Encrypt auto-renews via `certbot.timer` (installed by the
`certbot --nginx` command above). Verify with:

```bash
systemctl status certbot.timer
certbot renew --dry-run
```

## 9. What about the portal?

The portal repo (`roofmeasure-portal`) is a Next.js app and deploys to
Hostinger Node hosting (different product from VPS). Its build command,
environment variables, and deploy workflow are documented in
**`roofmeasure-portal/DEPLOYMENT_HOSTINGER.md`** — not here.
