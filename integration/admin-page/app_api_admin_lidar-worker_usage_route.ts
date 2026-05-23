// Next.js API route: GET /api/admin/lidar-worker/usage
//
// Place at: app/api/admin/lidar-worker/usage/route.ts in your Next.js app.
//
// Proxies to the Python engine's /admin/usage endpoint with the X-Admin-Key
// header server-side, so the admin key never reaches the browser.

import { NextResponse } from "next/server";
import { requireAdminSession } from "@/lib/admin-auth";

const ENGINE_URL =
  process.env.LIDAR_WORKER_URL ||
  process.env.MEASUREMENT_ENGINE_URL ||
  "http://127.0.0.1:8088";

const ENGINE_ADMIN_KEY =
  process.env.LIDAR_WORKER_ADMIN_KEY ||
  process.env.MEASUREMENT_ENGINE_ADMIN_KEY ||
  "";

export async function GET(request: Request) {
  await requireAdminSession();

  if (!ENGINE_ADMIN_KEY) {
    return NextResponse.json(
      { error: "LIDAR_WORKER_ADMIN_KEY env var not set on Next.js side" },
      { status: 500 },
    );
  }

  // Forward `from` and `to` query params if present
  const url = new URL(request.url);
  const params = new URLSearchParams();
  for (const k of ["from", "to"]) {
    const v = url.searchParams.get(k);
    if (v) params.set(k, v);
  }
  const qs = params.toString();
  const target = `${ENGINE_URL.replace(/\/$/, "")}/admin/usage${qs ? "?" + qs : ""}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const res = await fetch(target, {
      headers: { "X-Admin-Key": ENGINE_ADMIN_KEY },
      signal: controller.signal,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({ error: "non-JSON" }));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: "engine unreachable", detail: (err as Error).message },
      { status: 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
