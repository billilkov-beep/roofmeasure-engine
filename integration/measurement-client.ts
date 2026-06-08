// Drop-in client for the RoofMeasure LiDAR + Solar API worker.
//
// Place at `lib/measurement-client.ts` in your Next.js codebase. Reads either
// the LIDAR_WORKER_* env vars (matches your existing config) or the
// MEASUREMENT_ENGINE_* vars (legacy) and falls back gracefully.
//
// Usage from `lib/estimate-engine.ts`:
//
//   import { fetchMeasurementFromEngine } from "./measurement-client";
//
//   export async function generatePreliminaryMeasurement(address, structure, options) {
//     if (process.env.ENABLE_LIDAR_WORKER === "true") {
//       const real = await fetchMeasurementFromEngine(address, structure, options);
//       if (real) return real;
//     }
//     // ... existing hash-based heuristic stays as fallback ...
//   }

import type { PreliminaryMeasurement, StructureSelection, Tenant } from "./types";

const ENGINE_URL =
  process.env.LIDAR_WORKER_URL ||
  process.env.MEASUREMENT_ENGINE_URL ||
  "http://127.0.0.1:8088";

const ENGINE_API_KEY =
  process.env.LIDAR_WORKER_API_KEY ||
  process.env.MEASUREMENT_ENGINE_API_KEY ||
  "";

const ENGINE_TIMEOUT_MS = Number(
  process.env.LIDAR_WORKER_TIMEOUT_MS ||
  process.env.MEASUREMENT_ENGINE_TIMEOUT_MS ||
  90_000
);

const ENGINE_STRATEGY = process.env.LIDAR_WORKER_STRATEGY || ""; // "" = let server decide

type EngineResponse = {
  roofAreaSqFt: number;
  footprintSqFt: number;
  roofingSquares: number;
  suggestedWastePercent: number;
  quoteReadySqFt: number;
  quoteReadySquares: number;
  estimatedCostLow: number;
  estimatedCostHigh: number;
  pitchSummary: string;
  predominantPitch: string;
  facetCount: number;
  confidenceScore: number;
  sourceSummary: string;
  locationSummary: {
    lat?: number;
    lng?: number;
    countrySupport: string;
    propertyClass: string;
  };
  disclaimer: string;
  facets: Array<{
    id: number;
    areaSqFt: number;
    pitch: string;
    pitchExact?: number;
    azimuthDeg: number;
    centroid?: [number, number, number];
  }>;
  edges: Array<{
    facetA: number;
    facetB: number | null;
    lengthFt: number;
    kind: string;
  }>;
  obstructions: Array<{
    kind: string;
    estimatedAreaSqFt: number;
    heightAbovePlaneFt: number;
  }>;
  lineMeasurements: {
    ridgesFt: number;
    hipsFt: number;
    valleysFt: number;
    eavesFt: number;
    rakesFt: number;
    ridgesHipsFt: number;
    dripEdgeFt: number;
    flashingFt: number;
    stepFlashingFt: number;
    penetrations: number;
    penetrationsAreaSqFt: number;
  };
  pitchAreas: Array<{ pitch: string; areaSqFt: number; percent: number }>;
  facetAreas: number[];
  measurementNotes: string[];
  dataSources: {
    geocoder: string;
    footprint: string;
    lidar: string;
    lidarTile?: string;
    lidarYear?: number;
    imageryDate?: string;
    imageryQuality?: string;
  };
};

export async function fetchMeasurementFromEngine(
  address: string,
  structure: StructureSelection,
  options?: {
    tenant?: Tenant;
    lat?: number;
    lng?: number;
    synthetic?: boolean;
    strategy?: "auto" | "lidar_only" | "solar_only" | "solar_first";
  },
): Promise<PreliminaryMeasurement | null> {
  if (process.env.ENABLE_LIDAR_WORKER === "false") {
    return null;
  }

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ENGINE_TIMEOUT_MS);

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (ENGINE_API_KEY) {
      headers["X-API-Key"] = ENGINE_API_KEY;
    }

    const body: Record<string, unknown> = {
      address,
      synthetic: !!options?.synthetic,
    };
    const strategy = options?.strategy || ENGINE_STRATEGY;
    if (strategy) body.strategy = strategy;

    const response = await fetch(`${ENGINE_URL.replace(/\/$/, "")}/measure`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (response.status === 401) {
      console.error(
        "[measurement-client] worker returned 401 - LIDAR_WORKER_API_KEY does not match server. Aborting.",
      );
      return null;
    }
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      console.warn(
        "[measurement-client] worker returned %d: %s",
        response.status,
        detail.slice(0, 200),
      );
      return null;
    }

    const data = (await response.json()) as EngineResponse;
    return engineToPreliminary(data, address, structure, options);
  } catch (error) {
    const msg = (error as Error).message;
    if (msg.includes("aborted")) {
      console.warn(
        "[measurement-client] worker request aborted after %dms timeout",
        ENGINE_TIMEOUT_MS,
      );
    } else {
      console.warn("[measurement-client] worker call failed:", msg);
    }
    return null;
  }
}

function engineToPreliminary(
  e: EngineResponse,
  address: string,
  structure: StructureSelection,
  options?: { tenant?: Tenant; lat?: number; lng?: number },
): PreliminaryMeasurement {
  const tenantPrice = tenantPriceSettings(options?.tenant);

  return {
    roofAreaSqFt: e.roofAreaSqFt,
    footprintSqFt: e.footprintSqFt,
    roofingSquares: e.roofingSquares,
    suggestedWastePercent: e.suggestedWastePercent,
    quoteReadySqFt: e.quoteReadySqFt,
    quoteReadySquares: e.quoteReadySquares,
    estimatedCostLow: e.estimatedCostLow,
    estimatedCostHigh: e.estimatedCostHigh,
    pitchSummary: e.pitchSummary,
    predominantPitch: e.predominantPitch,
    facetCount: e.facetCount,
    lineMeasurements: {
      ridgesHipsFt: e.lineMeasurements.ridgesHipsFt,
      valleysFt: e.lineMeasurements.valleysFt,
      rakesFt: e.lineMeasurements.rakesFt,
      eavesFt: e.lineMeasurements.eavesFt,
      dripEdgeFt: e.lineMeasurements.dripEdgeFt,
      flashingFt: e.lineMeasurements.flashingFt,
      penetrations: e.lineMeasurements.penetrations,
      penetrationsAreaSqFt: e.lineMeasurements.penetrationsAreaSqFt,
    },
    priceBreakdown: {
      lowPerSqFtCents: tenantPrice.lowPerSqFtCents,
      highPerSqFtCents: tenantPrice.highPerSqFtCents,
      minimumProjectPriceCents: tenantPrice.minimumProjectPriceCents,
      lowTotalCents: Math.round(e.estimatedCostLow * 100),
      highTotalCents: Math.round(e.estimatedCostHigh * 100),
    },
    sourceSummary: e.sourceSummary,
    locationSummary: {
      lat: e.locationSummary.lat ?? options?.lat,
      lng: e.locationSummary.lng ?? options?.lng,
      countrySupport: e.locationSummary.countrySupport,
      propertyClass: (e.locationSummary.propertyClass || "residential") as
        | "residential"
        | "commercial"
        | "multi_unit",
    },
    disclaimer: e.disclaimer,
    ...({
      facets: e.facets,
      edges: e.edges,
      obstructions: e.obstructions,
      pitchAreas: e.pitchAreas,
      facetAreas: e.facetAreas,
      measurementNotes: e.measurementNotes,
      dataSources: e.dataSources,
      confidenceScore: e.confidenceScore,
      structureComplexity:
        e.facetCount >= 16 ? "Complex" : e.facetCount >= 10 ? "Normal" : "Simple",
    } as unknown as Partial<PreliminaryMeasurement>),
  } as PreliminaryMeasurement;
}

function tenantPriceSettings(tenant?: Tenant) {
  return {
    lowPerSqFtCents:
      tenant?.roofPriceLowPerSqFtCents ??
      Number(process.env.ROOF_PRICE_LOW_PER_SQFT_CENTS || 450),
    highPerSqFtCents:
      tenant?.roofPriceHighPerSqFtCents ??
      Number(process.env.ROOF_PRICE_HIGH_PER_SQFT_CENTS || 850),
    minimumProjectPriceCents:
      tenant?.minimumProjectPriceCents ??
      Number(process.env.MINIMUM_PROJECT_PRICE_CENTS || 250000),
  };
}
