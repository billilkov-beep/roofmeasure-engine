#!/bin/bash
# Compare two harness runs — per-address before/after deltas.
#
# After each deploy, you want to know: did this patch improve any address?
# Did it break any address? This shows you immediately.
#
# Usage:
#   bash compare_runs.sh <old_csv> <new_csv>
#   bash compare_runs.sh                 (auto-picks newest 2 in /tmp/)

# Pick CSVs
if [ $# -ge 2 ]; then
    OLD="$1"; NEW="$2"
else
    # Auto-pick the two newest groundtruth CSVs
    NEW=$(ls -t /tmp/v2_groundtruth_*.csv 2>/dev/null | head -1)
    OLD=$(ls -t /tmp/v2_groundtruth_*.csv 2>/dev/null | sed -n '2p')
fi

if [ ! -f "$OLD" ] || [ ! -f "$NEW" ]; then
    echo "Usage: $0 <old_csv> <new_csv>"
    echo "Or with auto-pick: needs at least 2 /tmp/v2_groundtruth_*.csv files"
    exit 1
fi

echo "OLD: $OLD"
echo "NEW: $NEW"
echo

sudo /home/roofmeasure/engine/venv/bin/python << PYEOF
import csv, sys
from pathlib import Path

OLD = "$OLD"
NEW = "$NEW"

def load(path):
    with open(path) as f:
        return {row["address"]: row for row in csv.DictReader(f)}

old = load(OLD)
new = load(NEW)

def parse_float(s, default=0.0):
    try: return float(s)
    except (ValueError, TypeError): return default

all_addrs = sorted(set(old.keys()) | set(new.keys()))

print(f"{'address':<48} {'old Δ%':>9} {'new Δ%':>9} {'change':>9} {'verdict':<14}")
print("-" * 100)

improvements = []
regressions = []
unchanged = []
went_from_fail = []
went_to_fail = []

for addr in all_addrs:
    o = old.get(addr, {})
    n = new.get(addr, {})
    o_pct = o.get("delta_pct", "")
    n_pct = n.get("delta_pct", "")
    o_succ = o_pct != "" and o_pct != "-"
    n_succ = n_pct != "" and n_pct != "-"

    if o_succ and n_succ:
        op = parse_float(o_pct); np_ = parse_float(n_pct)
        change = abs(np_) - abs(o_pct and op or 0)
        change_str = f"{change:+.1f}%"
        if abs(change) < 0.5:
            verdict = "unchanged"
            unchanged.append(addr)
        elif change < 0:
            verdict = "IMPROVED"
            improvements.append((addr, change))
        else:
            verdict = "regressed"
            regressions.append((addr, change))
        print(f"{addr[:48]:<48} {op:>9.1f} {np_:>9.1f} {change_str:>9} {verdict:<14}")
    elif n_succ and not o_succ:
        np_ = parse_float(n_pct)
        verdict = "NOW PASSING"
        went_from_fail.append((addr, np_))
        print(f"{addr[:48]:<48} {'fail':>9} {np_:>9.1f} {'+++':>9} {verdict:<14}")
    elif o_succ and not n_succ:
        op = parse_float(o_pct)
        verdict = "now FAILING"
        went_to_fail.append((addr, op))
        print(f"{addr[:48]:<48} {op:>9.1f} {'fail':>9} {'---':>9} {verdict:<14}")
    else:
        print(f"{addr[:48]:<48} {'fail':>9} {'fail':>9} {'':>9} still failing")

print()
print(f"=== Summary ===")
print(f"  Improved:     {len(improvements)}")
for addr, ch in improvements:
    print(f"    {addr[:60]}  {ch:+.1f}%")
print(f"  Regressed:    {len(regressions)}")
for addr, ch in regressions:
    print(f"    {addr[:60]}  {ch:+.1f}%")
print(f"  Now passing:  {len(went_from_fail)}")
for addr, pct in went_from_fail:
    print(f"    {addr[:60]}  {pct:+.1f}%")
print(f"  Now failing:  {len(went_to_fail)}")
for addr, pct in went_to_fail:
    print(f"    {addr[:60]}  was {pct:+.1f}%")
print(f"  Unchanged:    {len(unchanged)}")

# Aggregate quality
old_pcts = [abs(parse_float(r.get("delta_pct", ""))) for r in old.values() if r.get("delta_pct", "") not in ("", "-")]
new_pcts = [abs(parse_float(r.get("delta_pct", ""))) for r in new.values() if r.get("delta_pct", "") not in ("", "-")]
if old_pcts and new_pcts:
    import statistics
    print()
    print(f"  OLD mean abs %err: {statistics.mean(old_pcts):.2f}%  median: {statistics.median(old_pcts):.2f}%  n={len(old_pcts)}")
    print(f"  NEW mean abs %err: {statistics.mean(new_pcts):.2f}%  median: {statistics.median(new_pcts):.2f}%  n={len(new_pcts)}")
PYEOF
