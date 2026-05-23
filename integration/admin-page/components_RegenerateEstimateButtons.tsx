// Client component: regenerate-with-strategy buttons for a single estimate.
//
// Place at: components/RegenerateEstimateButtons.tsx in your Next.js app.
//
// Drop into your existing admin estimate detail page (the one at
// app/admin/orders/[orderId]/page.tsx or app/admin/estimates/[id]/page.tsx).

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Props = {
  estimateId: string;
  currentEngine?: string;       // shown above the buttons, e.g. "google_solar"
  currentConfidence?: number;   // shown above the buttons
};

const STRATEGIES: { value: string; label: string; description: string }[] = [
  {
    value: "auto",
    label: "Auto",
    description: "LiDAR first, Solar API fallback",
  },
  {
    value: "lidar_only",
    label: "LiDAR only",
    description: "Free, no API cost",
  },
  {
    value: "solar_only",
    label: "Solar API only",
    description: "Billed ~$0.50",
  },
  {
    value: "solar_first",
    label: "Solar first",
    description: "Faster, LiDAR fallback",
  },
];

export function RegenerateEstimateButtons({
  estimateId, currentEngine, currentConfidence,
}: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{
    strategy: string;
    usedFallback: boolean;
    regeneratedBy: string;
    regeneratedAt: string;
  } | null>(null);

  async function regenerate(strategy: string) {
    setError(null);
    setBusy(strategy);
    try {
      const res = await fetch(
        `/api/admin/estimates/${estimateId}/regenerate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ strategy }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.error || `HTTP ${res.status}`);
        return;
      }
      setLastResult({
        strategy: data.strategy,
        usedFallback: !!data.usedFallback,
        regeneratedBy: data.regeneratedBy,
        regeneratedAt: data.regeneratedAt,
      });
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{
      padding: "1rem 1.25rem", border: "1px solid #e2e8f0", borderRadius: 8,
      background: "white", marginTop: "1rem",
    }}>
      <h3 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "0.25rem" }}>
        Regenerate measurement
      </h3>
      <p style={{ color: "#475569", fontSize: "0.85rem", marginBottom: "0.75rem" }}>
        Re-run this estimate's measurement with a different engine.
        {currentEngine && (
          <>
            {" "}Currently produced by <strong>{currentEngine}</strong>
            {currentConfidence !== undefined &&
              <> at confidence <strong>{currentConfidence}</strong>/100</>
            }.
          </>
        )}
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        {STRATEGIES.map((s) => (
          <button
            key={s.value}
            type="button"
            disabled={!!busy}
            onClick={() => regenerate(s.value)}
            title={s.description}
            style={{
              padding: "0.5rem 0.9rem",
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: busy === s.value ? "#cbd5e1" : "white",
              color: "#0f172a",
              cursor: busy ? "wait" : "pointer",
              fontWeight: 500,
              opacity: busy && busy !== s.value ? 0.5 : 1,
            }}
          >
            {busy === s.value ? "Regenerating..." : s.label}
            <div style={{ fontSize: "0.7rem", color: "#64748b", fontWeight: 400 }}>
              {s.description}
            </div>
          </button>
        ))}
      </div>

      {lastResult && (
        <div style={{
          marginTop: "0.75rem", padding: "0.5rem 0.75rem",
          background: lastResult.usedFallback ? "#fef9c3" : "#dcfce7",
          border: `1px solid ${lastResult.usedFallback ? "#fef08a" : "#bbf7d0"}`,
          borderRadius: 6, fontSize: "0.85rem",
        }}>
          {lastResult.usedFallback ? "Worker unreachable" : "Regenerated"} with{" "}
          <strong>{lastResult.strategy}</strong> by {lastResult.regeneratedBy} at{" "}
          {new Date(lastResult.regeneratedAt).toLocaleString()}.
          {lastResult.usedFallback && (
            <> The hash heuristic was used as fallback.</>
          )}
        </div>
      )}

      {error && (
        <div role="alert" style={{
          marginTop: "0.75rem", padding: "0.5rem 0.75rem",
          background: "#fef2f2", border: "1px solid #fecaca",
          color: "#991b1b", borderRadius: 6, fontSize: "0.85rem",
        }}>
          Failed: {error}
        </div>
      )}
    </div>
  );
}
