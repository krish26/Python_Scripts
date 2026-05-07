"""
Part 1 — Arbetsförmedlingen Historical Job Trend Analysis (2019–2024)
API: https://historical.api.jobtechdev.se/search
No API key required.

JSON structure (verified from API):
  hits.hits[]
    ._source.headline          → job title
    ._source.employer.name     → employer name
    ._source.workplace_address.municipality  → municipality
    ._source.publication_date  → ISO8601 datetime string

Run: python historical_analysis.py
"""

import requests
import csv
import time
import os
from datetime import datetime, date
from collections import defaultdict

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://historical.api.jobtechdev.se/search"

KEYWORDS = [
    "Frontend",
    "Backend",
    "AI",
    "DevOps",
    "Cloud",
    "IT-support",
    "Cybersecurity",
]

# 5 years back from today
START_YEAR = date.today().year - 5
END_YEAR   = date.today().year

# Where to save results — use absolute path to avoid cron surprises
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH   = os.path.join(SCRIPT_DIR, "job_trends.csv")

PAGE_SIZE   = 100   # max the API supports
RATE_DELAY  = 0.4   # seconds between requests — be polite


# ── API helper ────────────────────────────────────────────────────────────────

def fetch_jobs(keyword: str, from_date: str, to_date: str) -> list[dict]:
    """
    Paginate through all results for a keyword + date range.
    Returns list of dicts with keys: keyword, year, title, employer, municipality.
    """
    results      = []
    offset       = 0
    _debug_shown = False   # print one raw hit only once per keyword+year call
    # FIX 1: API hard-caps at offset=2000 (Elasticsearch default window limit).
    # Requesting offset=2100 returns 400. Stop at 2000 max.
    MAX_OFFSET   = 2000

    while True:
        # FIX 1 applied: never request past offset 2000
        if offset >= MAX_OFFSET:
            break

        params = {
            "q":               keyword,
            "published-after":  from_date,
            "published-before": to_date,
            "limit":           PAGE_SIZE,
            "offset":          offset,
        }

        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  ⚠ Request failed (offset={offset}): {e}")
            break

        data = resp.json()

        # Guard: dump structure once if top level is unexpected
        if not isinstance(data, dict):
            print(f"\n  ✗ Unexpected top-level type {type(data).__name__}. Raw: {str(data)[:300]}")
            break

        # FIX 2: API returns {"hits": [...], "total": N}
        # data["hits"] is the FLAT list of job objects — no nested "hits" key.
        hits        = data.get("hits", [])
        total_raw   = data.get("total", 0)
        total_count = total_raw.get("value", 0) if isinstance(total_raw, dict) else int(total_raw or 0)

        if not hits:
            break

        # FIX 3: Auto-detect field layout from the first hit.
        # The Jobtech API sometimes returns jobs with _source wrapper (Elasticsearch)
        # and sometimes as flat dicts. We detect which on the first hit.
        if not _debug_shown:
            import json as _json
            first = hits[0]
            has_source = "_source" in first
            # Show raw structure so you can verify field names yourself
            sample = first.get("_source", first)
            print(f"\n  [DEBUG first hit keys]: {list(sample.keys())[:12]}")
            _debug_shown = True

        for hit in hits:
            # Handle both _source-wrapped and flat layouts
            src = hit.get("_source", hit)

            # FIX 3: publication_date — try multiple known field names
            pub_date = (
                src.get("publication_date")
                or src.get("publicationDate")
                or src.get("published")
                or src.get("last_publication_date")
                or ""
            )
            year = pub_date[:4] if pub_date else "unknown"

            # Headline — consistent across versions
            title = src.get("headline") or src.get("title") or ""

            # Employer — can be nested {"name": ...} or flat string
            emp_raw = src.get("employer") or {}
            if isinstance(emp_raw, dict):
                employer = emp_raw.get("name") or emp_raw.get("employer_name") or ""
            else:
                employer = str(emp_raw)

            # Municipality — can be nested under workplace_address or flat
            addr = src.get("workplace_address") or {}
            if isinstance(addr, dict):
                municipality = addr.get("municipality") or addr.get("city") or ""
            else:
                municipality = src.get("municipality") or ""

            results.append({
                "keyword":      keyword,
                "year":         year,
                "title":        title,
                "employer":     employer,
                "municipality": municipality,
            })

        offset += PAGE_SIZE
        if offset >= min(total_count, MAX_OFFSET):
            break

        time.sleep(RATE_DELAY)

    return results


# ── Main: collect + save ──────────────────────────────────────────────────────

def collect_historical():
    all_rows = []

    for year in range(START_YEAR, END_YEAR + 1):
        from_dt = f"{year}-01-01T00:00:00"
        to_dt   = f"{year}-12-31T23:59:59"

        for keyword in KEYWORDS:
            print(f"  Fetching {keyword:15s} {year}...", end=" ", flush=True)
            rows = fetch_jobs(keyword, from_dt, to_dt)
            print(f"{len(rows):>5} jobs")
            all_rows.extend(rows)
            time.sleep(RATE_DELAY)

    # Write CSV
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["year", "keyword", "title", "employer", "municipality"]
        )
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row[k] for k in writer.fieldnames})

    print(f"\n✓ Saved {len(all_rows):,} rows → {CSV_PATH}\n")
    return all_rows


# ── Trend table ───────────────────────────────────────────────────────────────

def print_trend_table(rows: list[dict]):
    # Count by (year, keyword)
    counts: dict[tuple, int] = defaultdict(int)
    for row in rows:
        counts[(row["year"], row["keyword"])] += 1

    years    = sorted({r["year"] for r in rows if r["year"] != "unknown"})
    keywords = KEYWORDS

    # Build table: rows = keywords, cols = years
    headers = ["Keyword"] + years
    table   = []
    for kw in keywords:
        row_data = [kw]
        for yr in years:
            row_data.append(counts.get((yr, kw), 0))
        table.append(row_data)

    print("=" * 70)
    print("  TECH JOB TREND — ARBETSFÖRMEDLINGEN (Sweden)")
    print(f"  {START_YEAR}–{END_YEAR}  |  source: historical.api.jobtechdev.se")
    print("=" * 70)

    if HAS_TABULATE:
        print(tabulate(table, headers=headers, tablefmt="rounded_outline", intfmt=","))
    else:
        # Fallback plain formatter
        col_w = max(len(str(h)) for h in headers)
        kw_w  = max(len(kw) for kw in keywords)
        header_line = f"{'Keyword':<{kw_w}}" + "".join(f"  {yr:>{col_w}}" for yr in years)
        print(header_line)
        print("-" * len(header_line))
        for row_data in table:
            kw   = row_data[0]
            vals = row_data[1:]
            print(f"{kw:<{kw_w}}" + "".join(f"  {v:>{col_w},}" for v in vals))

    print()
    _print_observations(counts, years)


def _print_observations(counts, years):
    """Auto-detect notable patterns from the data."""
    print("── Key observations ─────────────────────────────────────────────────")

    for kw in KEYWORDS:
        yearly = [(yr, counts.get((yr, kw), 0)) for yr in years]
        if not yearly:
            continue

        peak_yr, peak_val = max(yearly, key=lambda x: x[1])
        latest_val        = yearly[-1][1] if yearly else 0
        first_val         = yearly[0][1]  if yearly else 0

        if peak_val == 0:
            print(f"  {kw}: no data")
            continue

        # Growth ratio: last year vs first year (avoid div/0)
        growth = ((latest_val - first_val) / first_val * 100) if first_val else float("inf")

        trend_label = (
            f"▲ +{growth:.0f}% over period" if growth > 20
            else f"▼ {growth:.0f}% over period" if growth < -20
            else "→ roughly flat"
        )
        peaked_note = (
            f"  [peaked {peak_yr}]" if peak_yr != years[-1] and peak_val > latest_val * 1.3
            else ""
        )
        print(f"  {kw:<15}: {trend_label}{peaked_note}")

    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nArbetsförmedlingen Historical Analysis")
    print(f"Collecting {len(KEYWORDS)} keywords × {END_YEAR - START_YEAR + 1} years\n")

    rows = collect_historical()
    print_trend_table(rows)