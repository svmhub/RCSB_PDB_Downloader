"""
RCSB PDB Downloader between two dates
=====================================
Uses RCSB's official, public Search API:
    https://search.rcsb.org/rcsbsearch/v2/query

Filter applied:
  - Only release Date in range [start_date, end_date] (both inclusive)

Usage (Ubuntu / Linux):
    pip install requests tqdm
    python rcsb_pdb_downloader_by_between_dates.py --start 2024-01-01 --end 2024-06-30

Outputs (saved inside ALL_PDBS/):
    pdb_ids_<start>_<end>.txt       — all PDB IDs found
    PDB_FILES/*.pdb                 — downloaded structure files
    missing_pdbs_<start>_<end>.txt  — IDs that could not be downloaded
    summary_<start>_<end>.csv       — run statistics
    rcsb_downloader.log             — full log
"""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("rcsb_pdb_downloader.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────
SEARCH_API_URL       = "https://search.rcsb.org/rcsbsearch/v2/query"
FILE_DOWNLOAD_URL    = "https://files.rcsb.org/download/{id}.{ext}"
MAX_ROWS_PER_PAGE    = 10000   # maximum the RCSB API allows per page
REQUEST_TIMEOUT      = 30      # seconds


# ─── Search API ──────────────────────────────────────────────────────────────
def build_query(start_date: str, end_date: str, page_start: int = 0) -> dict:
    """
    Build the RCSB Search API JSON body.
    Single filter: Release Date in [start_date, end_date] (both inclusive).
    """
    return {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": "rcsb_accession_info.initial_release_date",
                "operator": "range",
                "value": {
                    "from": start_date,
                    "to": end_date,
                    "include_lower": True,
                    "include_upper": True,
                },
            },
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {
                "start": page_start,
                "rows": MAX_ROWS_PER_PAGE,
            },
            "results_content_type": ["experimental"],
        },
    }


def run_search(start_date: str, end_date: str) -> list:
    """
    Query RCSB and return all matching PDB IDs.
    Paginates automatically when results exceed one page.
    """
    log.info(f"Searching RCSB for structures released between {start_date} and {end_date} ...")

    all_ids    = []
    page_start = 0
    total      = None

    while True:
        query = build_query(start_date, end_date, page_start)

        try:
            resp = requests.post(SEARCH_API_URL, json=query, timeout=REQUEST_TIMEOUT)
        except requests.ConnectionError:
            log.error("Cannot reach search.rcsb.org — check your internet connection.")
            sys.exit(1)

        if resp.status_code == 204:
            log.warning("No results returned by RCSB (HTTP 204 — empty result set).")
            break

        if resp.status_code != 200:
            log.error(f"RCSB API error {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()

        data = resp.json()

        if total is None:
            total = data.get("total_count", 0)
            log.info(f"Total matching entries: {total}")

        hits = data.get("result_set", [])
        if not hits:
            break

        page_ids = [hit["identifier"] for hit in hits]
        all_ids.extend(page_ids)
        log.info(f"  Retrieved {len(all_ids)} / {total} IDs ...")

        if len(all_ids) >= total:
            break

        page_start += MAX_ROWS_PER_PAGE
        time.sleep(0.3)   # be polite to the API

    # Remove any duplicates while preserving order
    seen, unique = set(), []
    for pid in all_ids:
        if pid not in seen:
            seen.add(pid)
            unique.append(pid)

    log.info(f"Done. {len(unique)} unique PDB IDs collected.")
    return unique


# ─── File downloader ─────────────────────────────────────────────────────────
def download_pdb_files(pdb_ids: list, output_dir: Path, file_format: str = "pdb"):
    """
    Download structure files from RCSB for each PDB ID.
    Returns (downloaded_list, missing_list).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded, missing = [], []

    session = requests.Session()
    session.headers.update({"User-Agent": "PDB Downloader (research)"})

    log.info(f"Downloading {len(pdb_ids)} files ({file_format.upper()}) -> {output_dir}/")

    for pdb_id in tqdm(pdb_ids, desc="Downloading", unit="file"):
        dest = output_dir / f"{pdb_id}.{file_format}"

        if dest.exists():
            downloaded.append(pdb_id)
            continue

        url = FILE_DOWNLOAD_URL.format(id=pdb_id, ext=file_format)
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            if resp.status_code == 200:
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        fh.write(chunk)
                downloaded.append(pdb_id)
            else:
                log.warning(f"{pdb_id}: HTTP {resp.status_code} — added to missing list.")
                missing.append(pdb_id)
        except requests.RequestException as exc:
            log.warning(f"{pdb_id}: download failed ({exc}) — added to missing list.")
            missing.append(pdb_id)

    return downloaded, missing


# ─── Output helpers ──────────────────────────────────────────────────────────
def save_id_list(pdb_ids: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write("\n".join(pdb_ids) + ("\n" if pdb_ids else ""))
    log.info(f"Saved {len(pdb_ids)} IDs -> {path}")


def save_summary(start, end, total, downloaded, missing, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Report generated",      datetime.now().isoformat()])
        w.writerow(["Date range",            f"{start} to {end}"])
        w.writerow(["Total PDB IDs found",   total])
        w.writerow(["Successfully downloaded", len(downloaded)])
        w.writerow(["Missing / failed",      len(missing)])
        w.writerow([])
        w.writerow(["--- MISSING PDB IDs ---"])
        for pid in missing:
            w.writerow([pid])
    log.info(f"Summary -> {path}")


# ─── CLI ─────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Download RCSB PDB structures by release date range (no resolution filter)."
    )
    p.add_argument("--start",      required=True,  metavar="YYYY-MM-DD", help="Start date (inclusive)")
    p.add_argument("--end",        required=True,  metavar="YYYY-MM-DD", help="End date (inclusive)")
    p.add_argument("--format",     choices=["pdb", "cif"], default="pdb", help="File format (default: pdb)")
    p.add_argument("--output-dir", default="ALL_PDBS", help="Output directory (default: ALL_PDBS/)")
    p.add_argument("--ids-only",   action="store_true", help="Only fetch IDs, skip downloading files")
    return p.parse_args()


def validate_date(s: str):
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        log.error(f"Invalid date '{s}' — use YYYY-MM-DD format.")
        sys.exit(1)


def main():
    args = parse_args()
    validate_date(args.start)
    validate_date(args.end)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("RCSB PDB Downloader (date range only)")
    log.info(f"  Start  : {args.start}")
    log.info(f"  End    : {args.end}")
    log.info(f"  Format : {args.format}")
    log.info(f"  Output : {output_dir.resolve()}")
    log.info("=" * 60)

    # 1. Search
    pdb_ids = run_search(args.start, args.end)

    if not pdb_ids:
        log.warning("No PDB IDs found for this date range. Exiting.")
        sys.exit(0)

    # 2. Save ID list
    id_path = output_dir / f"pdb_ids_{args.start}_{args.end}.txt"
    save_id_list(pdb_ids, id_path)

    if args.ids_only:
        log.info("--ids-only flag set. Skipping file downloads.")
        return

    # 3. Download files
    downloaded, missing = download_pdb_files(
        pdb_ids,
        output_dir / "PDB_FILES",
        args.format,
    )

    # 4. Save missing list
    if missing:
        missing_path = output_dir / f"missing_pdbs_{args.start}_{args.end}.txt"
        save_id_list(missing, missing_path)
        log.warning(f"{len(missing)} file(s) could not be downloaded — see {missing_path}")

    # 5. Save summary
    save_summary(
        args.start, args.end,
        len(pdb_ids), downloaded, missing,
        output_dir / f"summary_{args.start}_{args.end}.csv",
    )

    log.info("=" * 60)
    log.info(f"Finished. {len(downloaded)} downloaded, {len(missing)} missed during the downloading the list.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
