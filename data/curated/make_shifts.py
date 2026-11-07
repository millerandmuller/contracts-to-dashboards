"""Writes shifts.csv: a small CURATED shift data set. Not from the organizer's pack, not from any plant.

Two lines, three weeks, three shifts a day. Seeded, so the file is the same on every run.
The last two shifts on Line B are set by hand so the demo has a shift change with open points.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(20261107)
SHIFTS = ["early", "late", "night"]
REASONS = ["changeover", "filler jam", "label printer fault", "waiting for material", "cleaning overrun"]
MATERIAL = ["", "", "", "", "", "", "blister foil", "cartons"]
start, days = date(2026, 10, 18), 21
rows, n = [], 0
for d in range(days):
    day = start + timedelta(days=d)
    for s in SHIFTS:
        if day == date(2026, 11, 7) and s == "night":
            continue  # the night shift has not started yet: it is the incoming shift
        for line in ("Line A", "Line B"):
            n += 1
            planned = 4800 if line == "Line A" else 4200
            down = random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 50])
            reason = random.choice(REASONS) if down else ""
            mat = random.choice(MATERIAL)
            pend = random.choice([0, 0, 0, 1, 1, 2])
            if line == "Line B" and day == date(2026, 11, 7) and s == "early":
                down, reason, mat, pend = 55, "filler jam", "", 1
            if line == "Line B" and day == date(2026, 11, 7) and s == "late":
                down, reason, mat, pend = 75, "filler jam", "blister foil", 2
            produced = round(planned * (1 - down / 480) * random.uniform(0.97, 1.0))
            rows.append({"shift_id": f"SH-{n:04d}", "date": day.isoformat(), "shift": s, "line": line, "planned_units": planned,
                         "produced_units": produced, "downtime_min": down, "downtime_reason": reason, "material_short": mat,
                         "batches_pending_release": pend, "curated": "curated shift data, not from the pack, not from any plant"})
out = Path(__file__).with_name("shifts.csv")
with out.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(out, len(rows))
