"""
Downloads GDELT GKG 1.0 daily files ONE DAY AT A TIME, matches organizations
against the alias table, aggregates tone scores per (date, ticker), and
appends the result to a single growing CSV. The raw ~137MB daily file is
NEVER written to disk — downloaded into memory, processed, discarded.

Resumable: re-running skips dates already present in the output file, so
it's safe to stop and restart (e.g. overnight, or after a failure).

Run locally (from ai-engine/ root):
    python -m preprocessing.build_news_sentiment 20160930 20260720

Requires: pip install requests
"""

import csv
import io
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import requests

from database.config import config
from preprocessing.gdelt_matching import load_aliases, match_organizations

GKG_URL_TEMPLATE = "http://data.gdeltproject.org/gkg/{date_str}.gkg.csv.zip"

GKG_COLUMNS = [
    "DATE", "NUMARTS", "COUNTS", "THEMES", "LOCATIONS",
    "PERSONS", "ORGANIZATIONS", "TONE", "CAMEOEVENTIDS",
    "SOURCES", "SOURCEURLS",
]

OUTPUT_COLUMNS = ["date", "ticker", "article_count", "avg_tone_mean", "avg_tone_std"]

REQUEST_TIMEOUT = 60
RETRY_ATTEMPTS = 3
SLEEP_BETWEEN_DAYS = 1.0  # seconds, be polite to GDELT's server


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def get_already_processed_dates(output_path: Path) -> set:
    if not output_path.exists():
        return set()
    processed = set()
    with open(output_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            processed.add(row["date"])
    return processed


def download_day(date_str: str):
    """Downloads and unzips one day's GKG file in memory. Returns raw text or None if unavailable."""
    url = GKG_URL_TEMPLATE.format(date_str=date_str)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                print(f"  [{date_str}] No file available (404) — skipping.")
                return None
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                inner_name = zf.namelist()[0]
                with zf.open(inner_name) as f:
                    return f.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  [{date_str}] attempt {attempt}/{RETRY_ATTEMPTS} failed: {e}")
            time.sleep(2 * attempt)
    print(f"  [{date_str}] giving up after {RETRY_ATTEMPTS} attempts.")
    return None


def process_day_text(raw_text: str, date_str: str, aliases: list) -> list:
    """Parses raw GKG text, matches orgs, aggregates tone per ticker. Returns output rows."""
    tone_by_ticker = {}  # ticker -> list of avg_tone values

    reader = csv.DictReader(
        io.StringIO(raw_text), fieldnames=GKG_COLUMNS, delimiter="\t"
    )
    for row in reader:
        matches = match_organizations(row.get("ORGANIZATIONS", ""), row.get("THEMES", ""), aliases)
        if not matches:
            continue
        tone_field = row.get("TONE", "")
        if not tone_field:
            continue
        try:
            avg_tone = float(tone_field.split(",")[0])
        except (ValueError, IndexError):
            continue

        seen_tickers_this_row = {ticker for ticker, _ in matches}
        for ticker in seen_tickers_this_row:
            tone_by_ticker.setdefault(ticker, []).append(avg_tone)

    output_rows = []
    for ticker, tones in tone_by_ticker.items():
        n = len(tones)
        mean_tone = sum(tones) / n
        if n > 1:
            variance = sum((t - mean_tone) ** 2 for t in tones) / (n - 1)
            std_tone = variance ** 0.5
        else:
            std_tone = 0.0
        output_rows.append({
            "date": date_str,
            "ticker": ticker,
            "article_count": n,
            "avg_tone_mean": round(mean_tone, 4),
            "avg_tone_std": round(std_tone, 4),
        })
    return output_rows


def append_rows(output_path: Path, rows: list):
    file_exists = output_path.exists()
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def main():
    if len(sys.argv) != 3:
        print("Usage: python -m preprocessing.build_news_sentiment <START_YYYYMMDD> <END_YYYYMMDD>")
        sys.exit(1)

    start = date(int(sys.argv[1][:4]), int(sys.argv[1][4:6]), int(sys.argv[1][6:8]))
    end = date(int(sys.argv[2][:4]), int(sys.argv[2][4:6]), int(sys.argv[2][6:8]))

    alias_path = config.DATA_DIR / "alias_table.csv"
    aliases = load_aliases(alias_path)
    print(f"Loaded {len(aliases)} alias entries.")

    output_path = config.DATASET_DIR / "news_sentiment.csv"
    config.DATASET_DIR.mkdir(parents=True, exist_ok=True)

    already_done = get_already_processed_dates(output_path)
    print(f"{len(already_done)} dates already processed — will skip those.")

    total_days = (end - start).days + 1
    for i, d in enumerate(daterange(start, end), 1):
        date_str = d.strftime("%Y%m%d")

        if date_str in already_done:
            continue

        print(f"[{i}/{total_days}] {date_str}...")
        raw_text = download_day(date_str)
        if raw_text is None:
            continue

        rows = process_day_text(raw_text, date_str, aliases)
        append_rows(output_path, rows)
        print(f"  -> {len(rows)} (ticker) rows written.")

        del raw_text
        time.sleep(SLEEP_BETWEEN_DAYS)

    print(f"\nDone. Output at {output_path}")


if __name__ == "__main__":
    main()