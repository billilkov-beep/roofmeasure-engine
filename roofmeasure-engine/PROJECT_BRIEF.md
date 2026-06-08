# RoofMeasure — Project Brief

This document is shared across both repos. The engine perspective lives here.

## What this is

**RoofMeasure** is a white-label SaaS roof-measurement service. A roofer
enters a customer address, and the system returns an EagleView-quality
roof report (area, pitch, facets, ridges, valleys, accessories) in under
60 seconds with no human-in-the-loop.

Live at `roofmeasure.canadasroofer.com`.

## The problem we're solving

US and Canadian roofers currently pay **$25–$80 per report** to EagleView,
Hover, Roofr, or GAF QuickMeasure. These services:

- Charge per-report (no flat fee)
- Take 30 min to 24 hours to return
- Lock measurement data behind their own software
- Require manual review on rural / complex roofs

**Our offer:** a flat monthly subscription that returns the same data in
**under 60 seconds**, fully automated, branded under the roofer's own
domain.

## Who it's for

**Primary market: US roofing contractors.** ~90% of revenue projection.
That's where volume and willingness-to-pay are.

**Secondary market: Canadian roofing contractors.** ~10%. Smaller market
but the founder has direct distribution via `canadasroofer.com`.

The product is sold to mid-size and small roofing companies (5–50
employees) who do enough volume to feel EagleView's per-report fees but
not enough to justify in-house measurement teams.

## What "finished" looks like

A roofer logs into their branded portal, types an address, and within 60
seconds gets:

1. An **aerial image** with facet polygons overlaid
2. A **PDF report** matching EagleView's industry-standard layout:
   - Street View hero shot
   - Aerial measurements page
   - Per-facet area, pitch, azimuth
   - Total area, predominant pitch, ridges/hips/valleys/rakes/eaves
   - Accessory take-off (vents, pipes, skylights from formula)
3. **Editable line measurements** so the roofer can tweak the result
4. A **regenerate** button if the result looks wrong

This engine repo (`roofmeasure-engine`) is the brains of that flow — it
produces the measurement JSON from a lat/lon. The portal repo
(`roofmeasure-portal`) wraps it with UI, PDF rendering, billing, and
white-label branding.

## Engine's job in one paragraph

Given a `(lat, lon)`, return an EagleView-quality measurement: total area,
predominant pitch, per-facet polygons with area + pitch + azimuth,
classified edges (ridge/hip/valley/rake/eave), accessory counts (vents,
pipes, skylights), and aerial + street view image URLs. Use LIDAR
(USGS 3DEP for US, NRCan HRDEM for Canada) when available, fall back to
Google Solar API when not. Return a confidence score so the portal can
warn the user when the result is uncertain.

## Quality bar

**Mean area error ≤ 5%** on a ground-truth set of 11 EagleView reports.
Anything worse than "Good" (within 10%) is a failure case requiring
engineering attention.

**No EagleView in the pipeline.** The whole point is to eliminate that
line item. We win or lose on our own algorithms.

## Business model (context, not engine concern)

- Tier 1: Free — 3 reports/month (lead gen)
- Tier 2: $99/month — 50 reports
- Tier 3: $299/month — unlimited + white-label domain
- Enterprise: custom

Cost-per-report is ~$0.40 (LIDAR free; Google Solar API ~$0.05; Static
Maps + Street View ~$0.02 each; hosting fixed). Gross margin >95% at
Tier 3.

## Where we are today (engine state)

- Engine deployed on Hostinger VPS, FastAPI behind nginx + TLS
- 7 of 11 ground-truth addresses succeed (3 Excellent, 2 Good, 2 Poor)
- 4 still fail (rural addresses where Nominatim geocodes to road
  centroid and no building is found at that point)
- Auto-test watcher runs the ground-truth harness on every code edit
- Multi-provider footprint chain: OSMnx → Overpass → MS Global ML →
  MS CA/US legacy
- Hybrid LIDAR + Solar with quality fallback (HIGH → MED → LOW)
- v3.4 footprint-area override + v3.5 adaptive overhang are the two
  algorithm tweaks that took mean error from ~25% to ~4%
