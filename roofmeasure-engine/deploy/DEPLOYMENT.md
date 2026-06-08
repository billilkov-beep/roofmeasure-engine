# Production Deployment — RoofMeasure LiDAR Worker

Stand up the Python engine behind nginx + Let's Encrypt + systemd, with API key
auth and rate limiting. This is what your `LIDAR_WORKER_URL` will point at.

Target: Ubuntu 22.04 / Debian 12 on a small VPS (Hostinger KVM2, DigitalOcean
$6/mo droplet, Hetzner CX22 - all sufficient). Adjust paths for other distros.

## 1. Pick a hostname and DNS record

Pick something like `lidar-worker.canadasroofer.com` (or use `measure-api.<domain>`).
Point an `A` record at your VPS public IP. Verify with `dig lidar-worker.<domain>`.

## 2. Provision the server

```bash
# As root or sudo:
apt update && apt -y upgrade
apt -y install python3 python3-pip nginx certbot python3-certbot-nginx ufw git
```

## 3. Lock down the firewall

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
# Do NOT open 8088 - that stays bound to 127.0.0.1
ufw enable
```

## 4. Create the unprivileged service user

```bash
useradd -r -s /usr/sbin/nologin roofmeasure
mkdir -p /opt/roofmeasure /var/cache/roofmeasure /opt/roofmeasure/data
chown -R roofmeasure:roofmeasure /opt/roofmeasure /var/cache/roofmeasure
```

## 5. Install the engine

```bash
# Copy the engine onto the box (scp, git clone, or unzip)
cd /opt/roofmeasure
# extract roofmeasure_engine.zip here, then:
chown -R roofmeasure:roofmeasure /opt/roofmeasure

# Install Python deps as the service user
sudo -u roofmeasure pip3 install --user numpy requests laspy
```

## 6. Generate the API key and env file

```bash
# Generate a strong API key:
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# Copy the output - you'll paste it into TWO places (env file and nginx map).

cp /opt/roofmeasure/deploy/roofmeasure-engine.env.example /etc/roofmeasure-engine.env
# Edit /etc/roofmeasure-engine.env:
#   - paste the API key into ROOFMEASURE_API_KEY=
#   - paste your GOOGLE_SOLAR_API_KEY=
#   - set ROOFMEASURE_STRATEGY=auto  (or solar_only, lidar_only, solar_first)
chmod 600 /etc/roofmeasure-engine.env
chown root:roofmeasure /etc/roofmeasure-engine.env
```

## 7. Install the systemd unit

```bash
cp /opt/roofmeasure/deploy/roofmeasure-engine.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now roofmeasure-engine

# Verify it's up:
systemctl status roofmeasure-engine
curl -s http://127.0.0.1:8088/health
# Should print {"status":"ok","auth_required":true,...}
```

If the service won't start, check logs:
```bash
journalctl -u roofmeasure-engine -n 50 --no-pager
```

## 8. Configure nginx

```bash
# Create the API key map
cp /opt/roofmeasure/deploy/lidar_api_keys.map.example /etc/nginx/lidar_api_keys.map
# Edit /etc/nginx/lidar_api_keys.map and paste your API key:
#   "<your-token>"   1;
chmod 600 /etc/nginx/lidar_api_keys.map
chown root:root /etc/nginx/lidar_api_keys.map

# Install the site config
cp /opt/roofmeasure/deploy/nginx.conf /etc/nginx/sites-available/lidar-worker.conf
# Edit the file and replace `lidar-worker.canadasroofer.com` with your hostname
ln -s /etc/nginx/sites-available/lidar-worker.conf /etc/nginx/sites-enabled/

# Test and reload (will fail until cert exists - that's OK, we get the cert next)
nginx -t 2>&1 | tail -3
```

## 9. Get a TLS cert with certbot

```bash
certbot --nginx -d lidar-worker.canadasroofer.com -m you@canadasroofer.com --agree-tos --redirect
systemctl reload nginx
```

Certbot autorenews via a systemd timer. Verify:
```bash
systemctl list-timers | grep certbot
```

## 10. End-to-end smoke test

```bash
# /health should work without a key
curl -s https://lidar-worker.canadasroofer.com/health
# -> {"status":"ok","auth_required":true,"version":"RoofMeasureEngine/0.2"}

# /measure WITHOUT key -> 401
curl -s -w '\nHTTP %{http_code}\n' -X POST https://lidar-worker.canadasroofer.com/measure \
  -H 'Content-Type: application/json' -d '{"address":"123 Main"}'
# -> {"error":"invalid or missing X-API-Key"}  HTTP 401

# /measure WITH key -> real measurement
curl -s -w '\nHTTP %{http_code}\n' -X POST https://lidar-worker.canadasroofer.com/measure \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: <your-key>" \
  -d '{"address":"624 Merrill Ave, Bedford, OH"}'
# -> full RoofMeasurement JSON, HTTP 200
```

## 11. Wire into your Next.js app

In your Hostinger app's `.env`:

```
ENABLE_LIDAR_WORKER=true
LIDAR_WORKER_URL=https://lidar-worker.canadasroofer.com
LIDAR_WORKER_API_KEY=<your-key>
LIDAR_WORKER_TIMEOUT_MS=90000
```

The TypeScript client in `integration/measurement-client.ts` reads those vars
and adds the `X-API-Key` header automatically.

## 12. Rotating the API key

Zero-downtime rotation:

1. Generate a new key: `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`
2. Add the NEW key to `/etc/nginx/lidar_api_keys.map` (don't remove the old one yet)
3. `systemctl reload nginx`
4. Update `ROOFMEASURE_API_KEY=` in `/etc/roofmeasure-engine.env` to the new key
5. `systemctl restart roofmeasure-engine`
6. Update `LIDAR_WORKER_API_KEY=` in your Next.js app's env, redeploy
7. Once all clients are using the new key, remove the old one from the nginx map and reload nginx

## 13. Monitoring suggestions

- **Uptime ping**: hit `/health` from UptimeRobot / BetterStack every 1-5 minutes
- **Solar API quota**: enable budget alerts in Google Cloud Console (Billing -> Budgets)
- **nginx logs**: `tail -F /var/log/nginx/lidar-worker.access.log` shows per-request status
- **engine logs**: `journalctl -u roofmeasure-engine -f`

## 14. Capacity tuning

A single-threaded Python process can handle ~10-20 LiDAR measurements per minute.
If you outgrow that:

- Scale vertically: bigger VPS, plus run multiple engine workers behind nginx with
  upstream load balancing. Use gunicorn or uwsgi for proper multi-worker hosting:
  ```
  pip install gunicorn
  # in the systemd unit:
  ExecStart=/usr/bin/gunicorn -w 4 -b 127.0.0.1:8088 measure_wsgi:app
  ```
  (the current stdlib server is fine for prototype scale; gunicorn is a follow-up)
- Cache aggressively: same address measured twice should return the cached
  result. Add a Redis layer that keys on `sha1(address)`.
- Push Solar API requests onto a queue: many addresses can be measured in
  parallel since Solar API is a simple HTTPS call.

## 15. Troubleshooting

| Symptom                            | Cause                              | Fix                                                |
|------------------------------------|------------------------------------|----------------------------------------------------|
| `/health` -> 502 Bad Gateway       | Engine not running                 | `systemctl status roofmeasure-engine`, check logs  |
| `/measure` -> 401 with real key    | Key in nginx map differs from header | Compare exact bytes, beware trailing whitespace  |
| `/measure` -> 500 "no LiDAR found" | Address outside US 3DEP coverage   | Switch strategy to `solar_first`, ensure Solar key set |
| `/measure` -> 500 Solar 403         | Solar API key not enabled         | Enable Solar API in Google Cloud Console, billing  |
| `/measure` slow (>60s)             | First-time LAZ download           | `ROOFMEASURE_CACHE_DIR` should be persistent       |
| nginx rate-limits real customers   | `30r/m` per IP too aggressive      | Raise `rate=` in the `limit_req_zone` directive    |
