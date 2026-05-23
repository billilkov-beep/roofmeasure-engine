// Client component for switching the LiDAR worker strategy.
//
// Place at: components/LidarWorkerStrategyForm.tsx in your Next.js app.
//
// Renders one button per valid strategy. The currently active one is highlighted
// and disabled. Click sends a POST to /api/admin/lidar-worker/strategy and
// reloads the page on success.

"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

type Props = {
  current: string;
  validStrategies: string[];
};

const STRATEGY_LABELS: Record<string, string> = {
  auto: "Auto (LiDAR -> Solar)",
  lidar_only: "LiDAR only (free)",
  solar_only: "Solar API only (paid)",
  solar_first: "Solar -> LiDAR fallback",
};

export function LidarWorkerStrategyForm({ current, validStrategies }: Props) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [pendingTarget, setPendingTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function switchTo(strategy: string) {
    setError(null);
    setPendingTarget(strategy);
    try {
      const res = await fetch("/api/admin/lidar-worker/strategy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.error || `HTTP ${res.status}`);
        return;
      }
      startTransition(() => {
        router.refresh();
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPendingTarget(null);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        {validStrategies.map((s) => {
          const active = s === current;
          const busy = pendingTarget === s || (isPending && pendingTarget === s);
          return (
            <button
              key={s}
              type="button"
              disabled={active || !!pendingTarget}
              onClick={() => switchTo(s)}
              style={{
                padding: "0.5rem 0.9rem",
                borderRadius: 6,
                border: "1px solid",
                borderColor: active ? "#0284c7" : "#cbd5e1",
                background: active ? "#0284c7" : "white",
                color: active ? "white" : "#0f172a",
                fontWeight: active ? 600 : 500,
                cursor: active ? "default" : pendingTarget ? "wait" : "pointer",
                opacity: pendingTarget && !busy ? 0.5 : 1,
              }}
              aria-pressed={active}
            >
              {busy ? "Switching..." : STRATEGY_LABELS[s] || s}
            </button>
          );
        })}
      </div>
      {error && (
        <div
          role="alert"
          style={{
            marginTop: "1rem",
            padding: "0.75rem 1rem",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#991b1b",
            borderRadius: 6,
            fontSize: "0.9rem",
          }}
        >
          Failed to switch: {error}
        </div>
      )}
    </div>
  );
}
