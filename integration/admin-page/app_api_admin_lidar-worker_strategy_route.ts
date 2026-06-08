// Next.js API route: GET / POST /api/admin/lidar-worker/strategy
//
// Place at: app/api/admin/lidar-worker/strategy/route.ts in your Next.js app.
//
// Proxies to the Python engine's /admin/strategy endpoint, using the admin
// session check from `lib/admin-auth.ts` for AuthZ on the Next.js side, and
// the engine's X-Admin-Key header for AuthZ on the engine side.
//
// Env vars (read at request time so no Next.js restart needed when rotating):
//   LIDAR_WORKER_URL          - same one used by measurement-client.ts
//   LIDAR_WORKER_ADMIN_KEY    - matches ROOFMEASURE_ADMIN_KEY on the engine
//                               (separate from LIDAR_WORKER_API_KEY)

import { NextResponse } from "next/server";
import { requireAdminSession } from "@/lib/admin-auth"; // your existing helper

const ENGINE_URL =
  process.env.LIDAR_WORKER_URL ||
  process.env.MEASUREMENT_ENGINE_URL ||
  "http://127.0.0.1:8088";

const ENGINE_ADMIN_KEY =
  process.env.LIDAR_WORKER_ADMIN_KEY ||
  process.env.MEASUREMENT_ENGINE_ADMIN_KEY ||
  "";

const VALID_STRATEGIES = ["auto", "lidar_only", "solar_only", "solar_first"] as const;
type Strategy = (typeof VALID_STRATEGIES)[number];

function isStrategy(s: unknown): s is Strategy {
  return typeof s === "string" && (VALID_STRATEGIES as readonly string[]).includes(s);
}

async function callEngine(method: "GET" | "POST", body?: Record<string, unknown>) {
  if (!ENGINE_ADMIN_KEY) {
    return {
      status: 500,
      data: { error: "LIDAR_WORKER_ADMIN_KEY env var not set on Next.js side" },
    };
  }
  const url = `${ENGINE_URL.replace(/\/$/, "")}/admin/strategy`;
  const headers: Record<string, string> = { "X-Admin-Key": ENGINE_ADMIN_KEY };
  if (body) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({ error: "non-JSON response" }));
    return { status: res.status, data };
  } catch (err) {
    return {
      status: 502,
      data: { error: "engine unreachable", detail: (err as Error).message },
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(_request: Request) {
  await requireAdminSession();
  const result = await callEngine("GET");
  return NextResponse.json(result.data, { status: result.status });
}

export async function POST(request: Request) {
  const session = await requireAdminSession();
  const body = await request.json().catch(() => ({}));
  const strategy = body?.strategy;
  if (!isStrategy(strategy)) {
    return NextResponse.json(
      { error: "invalid strategy", validStrategies: VALID_STRATEGIES },
      { status: 400 },
    );
  }
  const who = (session?.email as string | undefined) || "admin";
  const result = await callEngine("POST", { strategy, who });
  if (result.status === 200) {
    console.log("[admin] %s changed LiDAR worker strategy to %s", who, strategy);
  }
  return NextResponse.json(result.data, { status: result.status });
}
