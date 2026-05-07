"""
Part 2 — Weekly Job Trend Updater
Fetches last 7 days, appends to the same CSV, sends summary email.

Cron schedule (Monday 08:00):
  0 8 * * 1 /usr/bin/python3 /absolute/path/to/job_trends/weekly_update.py >> /absolute/path/to/job_trends/cron.log 2>&1

Quick test (run 3 minutes from now):
  crontab -e
  Then add (replace with your actual minute):  37 14 * * * /usr/bin/python3 /path/weekly_update.py

Email config: set env vars or edit SMTP_* constants below.
  export EMAIL_FROM="you@example.com"
  export EMAIL_TO="you@example.com"
  export SMTP_HOST="smtp.gmail.com"
  export SMTP_PORT="587"
  export SMTP_USER="you@example.com"
  export SMTP_PASS="your-app-password"
"""

import requests
import csv
import os
import time
import smtplib
import logging
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from collections import defaultdict

# ── Logging (important for silent cron debugging) ─────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH   = os.path.join(SCRIPT_DIR, "weekly_update.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL  = "https://historical.api.jobtechdev.se/search"
CSV_PATH  = os.path.join(SCRIPT_DIR, "job_trends.csv")   # absolute — required for cron

KEYWORDS  = [
    "Frontend", "Backend", "AI", "DevOps",
    "Cloud", "IT-support", "Cybersecurity",
]

PAGE_SIZE   = 100
RATE_DELAY  = 0.4

# Email — read from env so credentials aren't in source code
EMAIL_FROM = os.getenv("EMAIL_FROM", "sender@example.com")
EMAIL_TO   = os.getenv("EMAIL_TO",   "recipient@example.com")
SMTP_HOST  = os.getenv("SMTP_HOST",  "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER",  EMAIL_FROM)
SMTP_PASS  = os.getenv("SMTP_PASS",  "")         # use an App Password for Gmail


# ── Fetch last 7 days ─────────────────────────────────────────────────────────

def fetch_last_7_days() -> tuple[list[dict], dict[str, int]]:
    today    = date.today()
    week_ago = today - timedelta(days=7)

    from_dt = f"{week_ago.isoformat()}T00:00:00"
    to_dt   = f"{today.isoformat()}T23:59:59"

    all_rows: list[dict] = []
    counts:   dict[str, int] = {}

    for keyword in KEYWORDS:
        log.info(f"Fetching '{keyword}' ({from_dt} → {to_dt})")
        rows = _paginate(keyword, from_dt, to_dt)
        all_rows.extend(rows)
        counts[keyword] = len(rows)
        log.info(f"  '{keyword}' → {len(rows)} jobs")
        time.sleep(RATE_DELAY)

    return all_rows, counts


def _paginate(keyword: str, from_dt: str, to_dt: str) -> list[dict]:
    results    = []
    offset     = 0
    MAX_OFFSET = 2000   # API hard-caps at offset=2000; beyond that → 400

    while True:
        if offset >= MAX_OFFSET:
            break

        params = {
            "q":               keyword,
            "published-after":  from_dt,
            "published-before": to_dt,
            "limit":           PAGE_SIZE,
            "offset":          offset,
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"Request failed: {e}")
            break

        data        = resp.json()
        hits        = data.get("hits", [])
        total_raw   = data.get("total", 0)
        total_count = total_raw.get("value", 0) if isinstance(total_raw, dict) else int(total_raw or 0)

        if not hits:
            break

        for hit in hits:
            # Handle both _source-wrapped and flat layouts
            src = hit.get("_source", hit)

            pub_date = (
                src.get("publication_date")
                or src.get("publicationDate")
                or src.get("published")
                or src.get("last_publication_date")
                or ""
            )
            year  = pub_date[:4] if pub_date else "unknown"
            title = src.get("headline") or src.get("title") or ""

            emp_raw = src.get("employer") or {}
            employer = emp_raw.get("name", "") if isinstance(emp_raw, dict) else str(emp_raw)

            addr = src.get("workplace_address") or {}
            municipality = addr.get("municipality", "") if isinstance(addr, dict) else src.get("municipality", "")

            results.append({
                "year":         year,
                "keyword":      keyword,
                "title":        title,
                "employer":     employer,
                "municipality": municipality,
            })

        offset += PAGE_SIZE
        if offset >= min(total_count, MAX_OFFSET):
            break

        time.sleep(RATE_DELAY)

    return results


# ── Append to CSV ─────────────────────────────────────────────────────────────

def append_to_csv(rows: list[dict]) -> int:
    fieldnames = ["year", "keyword", "title", "employer", "municipality"]
    file_exists = os.path.isfile(CSV_PATH)

    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})

    log.info(f"Appended {len(rows)} rows to {CSV_PATH}")
    return len(rows)


# ── Send summary email ────────────────────────────────────────────────────────

def send_email(counts: dict[str, int], total: int):
    week_str = (date.today() - timedelta(days=7)).strftime("%d %b") + " – " + date.today().strftime("%d %b %Y")

    lines = [
        f"Weekly job posting summary — {week_str}",
        f"Source: Arbetsförmedlingen (historical.api.jobtechdev.se)",
        "",
        f"{'Category':<18}  {'Jobs this week':>14}",
        "-" * 35,
    ]
    for kw in KEYWORDS:
        lines.append(f"  {kw:<16}  {counts.get(kw, 0):>14,}")
    lines += [
        "-" * 35,
        f"  {'TOTAL':<16}  {total:>14,}",
        "",
        f"CSV updated: {CSV_PATH}",
        f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    body = "\n".join(lines)
    subject = f"[Job Trends] Week of {week_str} — {total:,} new postings"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO

    if not SMTP_PASS:
        log.warning("SMTP_PASS not set — printing email to stdout instead")
        print("\n" + "=" * 60)
        print(f"Subject: {subject}")
        print("=" * 60)
        print(body)
        return

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        log.info(f"Email sent to {EMAIL_TO}")
    except Exception as e:
        log.error(f"Email failed: {e}")
        # Don't crash — the CSV update is more important than the email
        print(f"Email failed: {e}. Body was:\n{body}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== Weekly update started ===")
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Fetching last 7 days...")

    rows, counts = fetch_last_7_days()
    total = sum(counts.values())

    appended = append_to_csv(rows)
    print(f"Appended {appended:,} rows to CSV.")

    send_email(counts, total)
    print("Done. Check weekly_update.log for details.")

    log.info(f"=== Weekly update done. Total new rows: {total} ===")