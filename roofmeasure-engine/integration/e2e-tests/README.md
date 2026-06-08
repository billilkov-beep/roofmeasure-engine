# E2E test suite (Playwright)

End-to-end browser tests for the canadasroofer.com customer flow + admin
dashboard. Built to run against a deployed instance, not the local dev server.

## What's covered

| Spec                             | What it exercises                                                 |
|----------------------------------|-------------------------------------------------------------------|
| `customer-flow.spec.ts`          | home -> address -> structure -> results -> PDF download           |
| `customer-validation.spec.ts`    | empty / short / bogus address rejection, invalid structure rejected |
| `admin-strategy.spec.ts`         | admin login -> flip strategy -> verify persistence                |
| `admin-usage.spec.ts`            | cost dashboard renders, /api/admin/lidar-worker/usage schema      |
| `admin-regenerate.spec.ts`       | per-estimate strategy override via the admin regenerate endpoint  |

## Layout in your repo

Put this folder at the root of your Next.js repo as `e2e-tests/`:

```
your-nextjs-repo/
  app/                         # existing Next.js app
  e2e-tests/                   # <-- this folder
    package.json
    playwright.config.ts
    tsconfig.json
    .env.example
    fixtures/
      admin-auth.ts
      test-data.ts
    tests/
      customer-flow.spec.ts
      customer-validation.spec.ts
      admin-strategy.spec.ts
      admin-usage.spec.ts
      admin-regenerate.spec.ts
```

Also copy `.github_workflows_e2e.yml` to `your-nextjs-repo/.github/workflows/e2e.yml`.

## Local run

```bash
cd e2e-tests
npm install
npx playwright install --with-deps chromium

cp .env.example .env
# edit .env with admin creds + BASE_URL

# Against staging
BASE_URL=https://staging.canadasroofer.com npm test

# Against production (read-only customer flow, admin tests too if you have a
# dedicated test admin)
BASE_URL=https://measure.canadasroofer.com npm test

# Run just one suite
npm run test:customer
npm run test:admin

# Debug a flaky test with the inspector
npm run test:debug

# View the last HTML report
npm run test:report
```

## CI

The included GitHub Actions workflow runs the full suite nightly at 08:00 UTC
and on every PR that touches `app/`, `lib/`, `components/`, or the tests
themselves. On failure it uploads the Playwright trace artifact for 14 days
(click the failing job in the Actions tab, scroll to "Artifacts").

Required secrets (Settings -> Secrets -> Actions):
- `E2E_BASE_URL` - the URL to test against
- `E2E_ADMIN_EMAIL` - admin login
- `E2E_ADMIN_PASSWORD` - admin password
- `E2E_TEST_ADDRESS` (optional) - the canary address (default: 624 Merrill Ave, Bedford, OH)
- `SLACK_WEBHOOK_URL` (optional) - posts a message on failure

## Notes on stability

- The customer tests use `624 Merrill Ave, Bedford, OH`, which is hardcoded as
  a regression anchor in `lib/estimate-engine.ts`. Even when the LiDAR engine
  is misbehaving the heuristic fallback will produce a deterministic result.
  Once the engine is fully wired, swap in a real high-coverage address.
- LiDAR fetches can legitimately take 30-60s. The default expectation timeout
  is set to 90 seconds in `playwright.config.ts`.
- The admin tests assume admin credentials work via the regular admin login
  flow (`/admin/login`). If you switch to OAuth or SSO, replace the body of
  `fixtures/admin-auth.ts`.
- Tests don't mutate production data beyond creating throwaway estimates,
  which is the same thing real customers do. The `admin-strategy` test flips
  the strategy briefly and flips it back to `auto` at the end.

## What's NOT covered

- Payment flow (Stripe/PayPal) - intentionally; you don't want CI hitting real
  payment processors. Cover this with manual smoke tests on staging.
- Phone verification (Twilio OTP) - same reason. Use the admin "skip OTP"
  toggle in test environments if needed.
- Multi-tenant flows - add a fixture that hits each tenant subdomain if you
  need this.
- Email delivery - test SMTP separately with a Mailpit or similar.
