// Snippet to add to lib/report-pdf.ts to render the v0.6 accessoryTakeoff[].
//
// 1. Find the line where the pages array is created:
//      const pages: string[][] = Array.from({ length: 8 }, () => []);
//    Change `length: 8` to `length: 9` so we have one more page.
//
// 2. Find every place that says "of 8" and change to "of 9":
//      header(pages[0], tenant, "Roof Measurement Report", "Page 1 of 8", ...)
//                                                                    ^^^^^^
//    (search-and-replace "Page X of 8" -> "Page X of 9" for all 8 instances)
//
// 3. INSERT THIS PAGE between the existing "Report Summary" (pages[6]) and
//    "Pricing & Notes" (pages[7]) so the new page becomes pages[7] and the
//    pricing page shifts to pages[8]. Easiest: copy pricing block, change its
//    index to 8, then insert the accessory block as pages[7].
//
// 4. Add this code BEFORE the existing pricing page block:

  // ---------------- Page 8 of 9 — Materials & Accessory Take-Off ----------------
  header(pages[7], tenant, "Materials & Accessories", "Page 8 of 9", address, reportId);
  pageTitle(pages[7], "MATERIALS & ACCESSORY TAKE-OFF");

  // Subtitle
  text(pages[7], 42, 650,
    "Quantities below are derived from the measured roof geometry. Items marked",
    9, "F1");
  text(pages[7], 42, 636,
    "[measured] come from LiDAR directly. [derived] are computed (e.g. squares = sq ft / 100).",
    9, "F1");
  text(pages[7], 42, 622,
    "[estimated] are formula-based ballparks — verify on-site before final ordering.",
    9, "F1");

  const takeoff = (extra?.accessoryTakeoff || []) as Array<{
    name: string; value: number; unit: string; source: string; note?: string;
  }>;

  // Sectioned rendering. Each section has a title + a small table.
  const SURFACE_NAMES = new Set([
    "Roof surface area", "Waste-adjusted shingle area",
    "Shingle squares (3-tab/architectural)", "Shingle bundles (3 per square)",
    "Synthetic underlayment", "Ice & water shield",
  ]);
  const LINEAR_NAMES = new Set([
    "Ridge cap material", "Ridge cap bundles",
    "Starter strip", "Starter strip bundles",
    "Drip edge", "Drip edge pieces (10 ft each)",
    "Step flashing", "Wall flashing", "Gutters", "Downspouts",
  ]);
  const ACCESSORY_NAMES = new Set([
    "Roof penetrations", "Pipe boots / vent collars", "Roof nails",
  ]);

  function sourceChipColor(source: string): string {
    if (source === "measured") return "0.08 0.45 0.22";   // green
    if (source === "derived")  return "0.04 0.27 0.65";   // blue
    if (source === "estimated")return "0.70 0.40 0.06";   // amber
    return "0.35 0.35 0.35";                              // grey
  }
  function sourceChipBg(source: string): string {
    if (source === "measured") return "0.86 0.97 0.88";
    if (source === "derived")  return "0.86 0.92 1";
    if (source === "estimated")return "0.99 0.93 0.80";
    return "0.94 0.94 0.94";
  }

  function renderSection(yStart: number, title: string, names: Set<string>): number {
    let y = yStart;
    text(pages[7], 42, y, title, 12, "F2", "0.03 0.22 0.42");
    y -= 18;
    tableRow(pages[7], y, [
      [62, "Item"], [310, "Qty"], [380, "Unit"], [440, "Source"],
    ], true);
    y -= 20;
    const items = takeoff.filter(t => names.has(t.name));
    items.forEach((t, idx) => {
      if (idx % 2 === 1) rect(pages[7], 42, y - 6, 528, 18, "0.97 0.99 1", "0.92 0.95 0.98");
      text(pages[7], 70, y, t.name, 9);
      text(pages[7], 310, y, number(t.value), 9, "F2");
      text(pages[7], 380, y, t.unit, 9);
      // Source chip
      const chipBg = sourceChipBg(t.source);
      const chipFg = sourceChipColor(t.source);
      rect(pages[7], 440, y - 4, 64, 13, chipBg, chipBg);
      text(pages[7], 446, y, t.source, 8, "F2", chipFg);
      y -= 18;
    });
    return y - 6;
  }

  let y = 600;
  y = renderSection(y, "Surface materials", SURFACE_NAMES);
  y = renderSection(y, "Linear features (ft + pieces)", LINEAR_NAMES);
  y = renderSection(y, "Penetrations & accessories", ACCESSORY_NAMES);

  // Footer caveat
  wrapText(pages[7], 42, y - 6,
    "Material quantities include the suggested waste percentage. Confirm with your supplier; coverage per bundle/roll varies by manufacturer.",
    8, 104);

  footer(pages[7], tenant);

// 5. The existing pricing page is currently pages[7]. Change its references to pages[8]
//    and update its header to "Page 9 of 9". That's the only other edit needed.
