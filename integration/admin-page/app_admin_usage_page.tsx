// Next.js admin page: cost + usage dashboard.
//
// Place at: app/admin/usage/page.tsx in your Next.js app.
//
// Shows: month-to-date Solar API spend, daily bar chart (inline SVG, no deps),
// engine breakdown, recent failures. Refreshes on page load.

import { requireAdminSession } from "@/lib/admin-auth";

type DayRow = {
  day: string;
  c: number;          // calls
  cents: number;      // cost
  solar_calls: number;
  lidar_calls: number;
};

type EngineRow = {
  engine: string;
  c: number;
  cents: number;
  avg_latency_ms: number;
  avg_confidence: number;
};

type Failure = {
  ts: string;
  address_hash: string;
  engine: string;
  error: string | null;
  latency_ms: number;
};

type UsageResponse = {
  fromEpoch: number;
  toEpoch: number;
  totals: { c: number; cents: number; ok: number; fail: number };
  byEngine: EngineRow[];
  byDay: DayRow[];
  recentFailures: Failure[];
  lifetimeCalls: number;
  solarCostCentsPerCall: number;
  error?: string;
};

async function fetchUsage(): Promise<UsageResponse> {
  const base = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";
  try {
    const res = await fetch(`${base}/api/admin/lidar-worker/usage`, {
      cache: "no-store",
    });
    return (await res.json()) as UsageResponse;
  } catch (err) {
    return {
      fromEpoch: 0, toEpoch: 0,
      totals: { c: 0, cents: 0, ok: 0, fail: 0 },
      byEngine: [], byDay: [], recentFailures: [], lifetimeCalls: 0,
      solarCostCentsPerCall: 50,
      error: `unreachable: ${(err as Error).message}`,
    };
  }
}

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default async function UsageDashboardPage() {
  await requireAdminSession();
  const usage = await fetchUsage();

  const maxDayCalls = Math.max(1, ...usage.byDay.map((d) => d.c));
  const chartWidth = 600;
  const chartHeight = 180;
  const barW = usage.byDay.length > 0
    ? Math.max(6, Math.floor((chartWidth - 40) / usage.byDay.length) - 2)
    : 8;

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: "2rem 1rem" }}>
      <h1 style={{ fontSize: "1.6rem", fontWeight: 600, marginBottom: "0.5rem" }}>
        LiDAR worker usage & cost
      </h1>
      <p style={{ color: "#475569", marginBottom: "1.5rem" }}>
        Tracks every measurement call across the LiDAR engine and Google Solar API.
        Solar API calls are billed at {dollars(usage.solarCostCentsPerCall)} each.
      </p>

      {usage.error && (
        <div style={{
          padding: "1rem", background: "#fef2f2", border: "1px solid #fecaca",
          borderRadius: 8, color: "#991b1b", marginBottom: "1.5rem",
        }}>
          {usage.error}
        </div>
      )}

      {/* Top metric cards */}
      <section style={{
        display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
        gap: "0.75rem", marginBottom: "1.5rem",
      }}>
        <MetricCard label="Month-to-date spend" value={dollars(usage.totals.cents)} accent />
        <MetricCard label="Calls this month" value={String(usage.totals.c)} />
        <MetricCard label="Failed calls" value={String(usage.totals.fail)} danger={usage.totals.fail > 0} />
        <MetricCard label="Lifetime calls" value={String(usage.lifetimeCalls)} />
      </section>

      {/* Daily bar chart (inline SVG, no deps) */}
      <section style={{
        padding: "1rem 1.25rem", border: "1px solid #e2e8f0", borderRadius: 8,
        marginBottom: "1.5rem", background: "white",
      }}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: "0.75rem" }}>
          Calls per day (this month)
        </h2>
        {usage.byDay.length === 0 ? (
          <p style={{ color: "#64748b" }}>No calls yet.</p>
        ) : (
          <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} style={{ width: "100%", height: "auto" }}>
            <rect x="0" y="0" width={chartWidth} height={chartHeight} fill="white" />
            <line x1="30" y1={chartHeight - 28} x2={chartWidth - 10} y2={chartHeight - 28}
                  stroke="#cbd5e1" strokeWidth="1" />
            {usage.byDay.map((d, i) => {
              const lidarH = Math.round(((d.lidar_calls) / maxDayCalls) * (chartHeight - 50));
              const solarH = Math.round(((d.solar_calls) / maxDayCalls) * (chartHeight - 50));
              const x = 32 + i * (barW + 2);
              const yLidar = chartHeight - 28 - lidarH;
              const ySolar = yLidar - solarH;
              return (
                <g key={d.day}>
                  <title>
                    {d.day}: {d.c} calls · LiDAR {d.lidar_calls} · Solar {d.solar_calls} · {dollars(d.cents)}
                  </title>
                  {/* LiDAR (green) on bottom */}
                  <rect x={x} y={yLidar} width={barW} height={lidarH} fill="#16a34a" />
                  {/* Solar (orange) on top */}
                  <rect x={x} y={ySolar} width={barW} height={solarH} fill="#ea580c" />
                </g>
              );
            })}
            {usage.byDay.length > 0 && (
              <>
                <text x="32" y={chartHeight - 12} fontSize="9" fill="#64748b">
                  {usage.byDay[0].day}
                </text>
                <text x={chartWidth - 10} y={chartHeight - 12} fontSize="9"
                      textAnchor="end" fill="#64748b">
                  {usage.byDay[usage.byDay.length - 1].day}
                </text>
              </>
            )}
            <text x="30" y="14" fontSize="10" fill="#475569">{maxDayCalls}</text>
            <text x="30" y={chartHeight - 32} fontSize="10" fill="#475569">0</text>
          </svg>
        )}
        <div style={{ display: "flex", gap: "1rem", marginTop: "0.5rem", fontSize: "0.85rem" }}>
          <span><span style={{
            display: "inline-block", width: 12, height: 12, background: "#16a34a",
            marginRight: 6, verticalAlign: "middle",
          }} /> LiDAR (free)</span>
          <span><span style={{
            display: "inline-block", width: 12, height: 12, background: "#ea580c",
            marginRight: 6, verticalAlign: "middle",
          }} /> Solar API ({dollars(usage.solarCostCentsPerCall)}/call)</span>
        </div>
      </section>

      {/* Engine breakdown */}
      <section style={{
        padding: "1rem 1.25rem", border: "1px solid #e2e8f0", borderRadius: 8,
        marginBottom: "1.5rem", background: "white",
      }}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: "0.75rem" }}>
          By engine
        </h2>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0" }}>
              <th style={{ padding: "0.4rem" }}>Engine</th>
              <th style={{ padding: "0.4rem", textAlign: "right" }}>Calls</th>
              <th style={{ padding: "0.4rem", textAlign: "right" }}>Cost</th>
              <th style={{ padding: "0.4rem", textAlign: "right" }}>Avg latency</th>
              <th style={{ padding: "0.4rem", textAlign: "right" }}>Avg confidence</th>
            </tr>
          </thead>
          <tbody>
            {usage.byEngine.map((e) => (
              <tr key={e.engine} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "0.4rem", fontWeight: 500 }}>{e.engine}</td>
                <td style={{ padding: "0.4rem", textAlign: "right" }}>{e.c}</td>
                <td style={{ padding: "0.4rem", textAlign: "right" }}>{dollars(e.cents)}</td>
                <td style={{ padding: "0.4rem", textAlign: "right" }}>
                  {Math.round(e.avg_latency_ms)} ms
                </td>
                <td style={{ padding: "0.4rem", textAlign: "right" }}>
                  {e.avg_confidence ? Math.round(e.avg_confidence) : "-"}
                </td>
              </tr>
            ))}
            {usage.byEngine.length === 0 && (
              <tr><td colSpan={5} style={{ padding: "0.5rem", color: "#64748b" }}>No data.</td></tr>
            )}
          </tbody>
        </table>
      </section>

      {/* Recent failures */}
      {usage.recentFailures.length > 0 && (
        <section style={{
          padding: "1rem 1.25rem", border: "1px solid #fecaca", borderRadius: 8,
          background: "#fef2f2", marginBottom: "1.5rem",
        }}>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: "0.75rem",
                       color: "#991b1b" }}>
            Recent failures ({usage.recentFailures.length})
          </h2>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #fecaca" }}>
                <th style={{ padding: "0.3rem" }}>Time</th>
                <th style={{ padding: "0.3rem" }}>Engine</th>
                <th style={{ padding: "0.3rem" }}>Error</th>
                <th style={{ padding: "0.3rem", textAlign: "right" }}>Latency</th>
              </tr>
            </thead>
            <tbody>
              {usage.recentFailures.map((f, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #fee2e2" }}>
                  <td style={{ padding: "0.3rem", fontFamily: "monospace" }}>{f.ts}</td>
                  <td style={{ padding: "0.3rem" }}>{f.engine}</td>
                  <td style={{ padding: "0.3rem", color: "#7f1d1d" }}>
                    {f.error?.slice(0, 100) || "-"}
                  </td>
                  <td style={{ padding: "0.3rem", textAlign: "right" }}>{f.latency_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

function MetricCard({ label, value, accent, danger }: {
  label: string; value: string; accent?: boolean; danger?: boolean;
}) {
  const bg = danger ? "#fef2f2" : accent ? "#f0f9ff" : "white";
  const border = danger ? "#fecaca" : accent ? "#bae6fd" : "#e2e8f0";
  const fg = danger ? "#991b1b" : accent ? "#075985" : "#0f172a";
  return (
    <div style={{
      padding: "1rem", background: bg, border: `1px solid ${border}`,
      borderRadius: 8,
    }}>
      <div style={{ fontSize: "0.8rem", color: "#475569", marginBottom: "0.25rem" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4rem", fontWeight: 600, color: fg }}>{value}</div>
    </div>
  );
}
