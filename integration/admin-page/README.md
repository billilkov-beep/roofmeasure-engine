# Next.js admin integration (v4)

Drop-in admin pages + API routes that expose the engine to your existing
admin dashboard. All use your existing `requireAdminSession()` for AuthZ.

## File-to-destination map

| Source in this bundle                                       | Destination in your Next.js project                                     |
|-------------------------------------------------------------|-------------------------------------------------------------------------|
| `app_admin_lidar-worker_page.tsx`                           | `app/admin/lidar-worker/page.tsx`                                       |
| `app_admin_usage_page.tsx`                                  | `app/admin/usage/page.tsx`                                              |
| `app_api_admin_lidar-worker_strategy_route.ts`              | `app/api/admin/lidar-worker/strategy/route.ts`                          |
| `app_api_admin_lidar-worker_usage_route.ts`                 | `app/api/admin/lidar-worker/usage/route.ts`                             |
| `app_api_admin_estimates_regenerate_route.ts`               | `app/api/admin/estimates/[estimateId]/regenerate/route.ts`              |
| `components_LidarWorkerStrategyForm.tsx`                    | `components/LidarWorkerStrategyForm.tsx`                                |
| `components_RegenerateEstimateButtons.tsx`                  | `components/RegenerateEstimateButtons.tsx`                              |
| `estimate-detail-snippet.tsx`                               | (instructions to add to your existing estimate-detail page)             |

## What you get

### 1. `/admin/lidar-worker` — Strategy switcher
Pick the active engine strategy (auto / lidar_only / solar_only / solar_first)
from the browser. Persisted on the engine side in
`/var/lib/roofmeasure/runtime.json`. Takes effect on next request, no restart.

### 2. `/admin/usage` — Cost & usage dashboard
- Month-to-date Google Solar API spend
- Daily stacked bar chart (LiDAR vs Solar calls)
- Per-engine table: calls, cost, avg latency, avg confidence
- Recent failures table (last 25)
- Inline SVG, no Chart.js dependency

### 3. Regenerate button on existing estimate detail page
Drop the `RegenerateEstimateButtons` component into your estimate detail page
(see `estimate-detail-snippet.tsx`). Admin can re-run a single estimate with
an explicit strategy override — useful when a customer complains about a
specific report or the engine returned low confidence on a known-good property.

## Env vars to add to your Next.js `.env`

```
# Engine connection (already set if you followed v1-v3)
LIDAR_WORKER_URL=https://lidar-worker.canadasroofer.com
LIDAR_WORKER_API_KEY=<api-key>
LIDAR_WORKER_TIMEOUT_MS=90000
ENABLE_LIDAR_WORKER=true

# New in v4: separate admin key for /admin/* endpoints
LIDAR_WORKER_ADMIN_KEY=<different-key>
```

Generate the admin key with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`
and paste the SAME value into `/etc/roofmeasure-engine.env` as
`ROOFMEASURE_ADMIN_KEY` on the VPS.

## Engine env additions

Add to `/etc/roofmeasure-engine.env`:

```
ROOFMEASURE_ADMIN_KEY=<same-as-LIDAR_WORKER_ADMIN_KEY>
ROOFMEASURE_USAGE_DB=/var/lib/roofmeasure/usage.db
ROOFMEASURE_CONFIG_FILE=/var/lib/roofmeasure/runtime.json
SOLAR_API_COST_CENTS=50           # adjust if Google pricing changes
```

Ensure `/var/lib/roofmeasure/` exists and is owned by the service user:

```bash
sudo mkdir -p /var/lib/roofmeasure
sudo chown roofmeasure:roofmeasure /var/lib/roofmeasure
sudo systemctl restart roofmeasure-engine
```

## Add navigation links

In your existing admin nav (likely `app/admin/page.tsx` or an `AdminNav`
component), add:

```tsx
<Link href="/admin/lidar-worker">LiDAR worker</Link>
<Link href="/admin/usage">API usage & cost</Link>
```

## Verifying it works

After deploying:

1. Visit `/admin/lidar-worker` — should show current strategy + 4 buttons.
   Click "Solar only", page reloads showing "solar_only" as active.
2. Place a test estimate from a normal user account. The estimate should
   succeed (via Solar API since you just switched).
3. Visit `/admin/usage` — should show 1 Solar API call at $0.50, today's
   bar chart with an orange bar.
4. On the admin estimate detail page for that test estimate, click
   "Regenerate" -> "LiDAR only". The page reloads showing the LiDAR-based
   measurement.
5. Switch the strategy back to "auto" before going to production.

## Defense in depth

Two layers of admin auth:
1. Next.js: `requireAdminSession()` on every admin route handler.
2. Engine: `X-Admin-Key` header on every `/admin/*` endpoint.

The admin key NEVER reaches the browser — Next.js holds it server-side and
adds it to the proxied request. Even if someone bypassed the Next.js admin
session check, they'd still need the engine admin key, which lives only in
the Next.js server env and the engine env file.
