// Next.js admin page for flipping the LiDAR worker strategy.
//
// Place at: app/admin/lidar-worker/page.tsx in your Next.js app.
//
// Uses your existing admin layout/auth. Renders the current strategy plus a
// row of buttons. Posts to /api/admin/lidar-worker/strategy on click.

import { redirect } from "next/navigation";
import { requireAdminSession } from "@/lib/admin-auth";
import { LidarWorkerStrategyForm } from "@/components/LidarWorkerStrategyForm";

type EngineStatus = {
  strategy?: string;
  envDefault?: string;
  persisted?: Record<string, unknown>;
  validStrategies?: string[];
  error?: string;
};

async function fetchStatus(): Promise<EngineStatus> {
  // Build an absolute URL because Server Components fetch needs origin
  const base = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";
  try {
    const res = await fetch(`${base}/api/admin/lidar-worker/strategy`, {
      cache: "no-store",
      // Forward the admin session cookie. In Next 14 Server Components this
      // is handled automatically when fetching same-origin.
    });
    return await res.json();
  } catch (err) {
    return { error: `unreachable: ${(err as Error).message}` };
  }
}

export default async function LidarWorkerAdminPage() {
  await requireAdminSession();

  const status = await fetchStatus();
  if (status.error === "engine unreachable" as unknown) {
    // graceful display
  }

  const valid = status.validStrategies ?? [
    "auto",
    "lidar_only",
    "solar_only",
    "solar_first",
  ];
  const current = status.strategy ?? "unknown";
  const persisted = status.persisted ?? {};
  const updatedAt = (persisted as { _updatedAt?: string })._updatedAt;
  const updatedBy = (persisted as { _updatedBy?: string })._updatedBy;

  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "2rem 1rem" }}>
      <h1 style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: "0.5rem" }}>
        LiDAR worker strategy
      </h1>
      <p style={{ color: "#475569", marginBottom: "1.5rem" }}>
        Controls which measurement engine the worker uses. Changes take effect
        immediately, no restart needed. Persisted in <code>/var/lib/roofmeasure/runtime.json</code>.
      </p>

      {status.error ? (
        <div
          style={{
            padding: "1rem",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 8,
            color: "#991b1b",
            marginBottom: "1.5rem",
          }}
        >
          <strong>Worker unreachable:</strong> {status.error}
        </div>
      ) : (
        <div
          style={{
            padding: "1rem 1.25rem",
            background: "#f0f9ff",
            border: "1px solid #bae6fd",
            borderRadius: 8,
            marginBottom: "1.5rem",
          }}
        >
          <div style={{ fontSize: "0.9rem", color: "#075985" }}>Current strategy</div>
          <div style={{ fontSize: "1.5rem", fontWeight: 600 }}>{current}</div>
          <div style={{ fontSize: "0.75rem", color: "#475569", marginTop: "0.5rem" }}>
            Env default: <code>{status.envDefault ?? "auto"}</code>
            {updatedAt && (
              <>
                {" "}
                · Last changed: {updatedAt} by {updatedBy ?? "admin"}
              </>
            )}
          </div>
        </div>
      )}

      <LidarWorkerStrategyForm current={current} validStrategies={valid} />

      <section style={{ marginTop: "2rem", color: "#475569", fontSize: "0.9rem" }}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#0f172a" }}>
          What the strategies do
        </h2>
        <dl style={{ marginTop: "0.75rem" }}>
          <dt style={{ fontWeight: 600 }}>auto</dt>
          <dd style={{ marginBottom: "0.5rem" }}>
            LiDAR first, Google Solar API as fallback. Recommended default.
          </dd>
          <dt style={{ fontWeight: 600 }}>lidar_only</dt>
          <dd style={{ marginBottom: "0.5rem" }}>
            Free engine only. Fails on addresses outside US 3DEP coverage; no Solar API costs.
          </dd>
          <dt style={{ fontWeight: 600 }}>solar_only</dt>
          <dd style={{ marginBottom: "0.5rem" }}>
            Google Solar API only. Charged per request (~$0.50). Use for stress tests or when LiDAR is down.
          </dd>
          <dt style={{ fontWeight: 600 }}>solar_first</dt>
          <dd style={{ marginBottom: "0.5rem" }}>
            Solar API first (faster), LiDAR as fallback. Use when you need speed at the cost of every successful request.
          </dd>
        </dl>
      </section>
    </main>
  );
}
