"""
RCSB PDB File Downloader — by PDB ID
======================================
Download specific PDB structure files by providing their 4-letter IDs.

Three ways to give PDB IDs:

  1. Directly on the command line:
       python download_by_pdbid.py --ids 1ABC 2DEF 3GHI

  2. From a text file (one ID per line, or comma-separated):
       python download_by_pdbid.py --file my_ids.txt

  3. Both at once:
       python download_by_pdbid.py --ids 1ABC --file my_ids.txt

Options:
  --format pdb|cif        File format to download (default: pdb)
  --output-dir PATH       Where to save files (default: pdb_downloads/)
  --workers N             Parallel downloads (default: 4, max: 8)

Output:
  pdb_downloads/
    ├── 1ABC.pdb
    ├── 2DEF.pdb
    ├── ...
    ├── missing_pdbs.txt   (only created when some files could not be found)
    └── download_summary.csv
"""

import argparse
import csv
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pdb_download.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
FILE_URL      = "https://files.rcsb.org/download/{id}.{ext}"
TIMEOUT       = 30     # seconds per request
MAX_WORKERS   = 8      # for parallel downloads
PDB_ID_REGEX  = re.compile(r'^[A-Za-z0-9]{4}$')


# ─── ID validation ────────────────────────────────────────────────────────────
def is_valid_pdb_id(s: str) -> bool:
    """PDB IDs are exactly 4 alphanumeric characters."""
    return bool(PDB_ID_REGEX.match(s))


def parse_id_file(path: Path) -> list:
    """
    Read PDB IDs from a text file.
    Accepts:
      - One ID per line          →  1ABC
      - Comma-separated          →  1ABC, 2DEF, 3GHI
      - Mixed                    →  1ABC, 2DEF
                                    3GHI
      - Lines starting with #    →  ignored (comments)
    """
    ids = []
    with open(path, "r") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # splitting the commas, spaces, tabs, semicolons
            parts = re.split(r'[\s,;]+', line)
            for part in parts:
                part = part.strip().upper()
                if part:
                    ids.append(part)
    return ids


def collect_ids(args) -> list:
    """Store, deduplicate, and validate all PDB IDs from the CLI and/or file."""
    raw = []

    if args.ids:
        raw.extend([i.upper() for i in args.ids])

    if args.file:
        p = Path(args.file)
        if not p.exists():
            log.error(f"ID file not found: {p}")
            sys.exit(1)
        raw.extend(parse_id_file(p))

    if not raw:
        log.error("No PDB IDs provided. Use --ids or --file (or both).")
        sys.exit(1)

    # Deduplicate while preserving order
    seen, unique = set(), []
    for pid in raw:
        if pid not in seen:
            seen.add(pid)
            unique.append(pid)

    # Validate each ID
    valid, invalid = [], []
    for pid in unique:
        if is_valid_pdb_id(pid):
            valid.append(pid)
        else:
            invalid.append(pid)

    if invalid:
        log.warning(f"Skipping {len(invalid)} invalid ID(s): {', '.join(invalid)}")
        log.warning("Valid PDB IDs are exactly 4 alphanumeric characters (e.g. 1ABC).")

    if not valid:
        log.error("No valid PDB IDs to download.")
        sys.exit(1)

    log.info(f"{len(valid)} unique valid PDB ID(s) to download.")
    return valid


# ─── Downloader ───────────────────────────────────────────────────────────────
def download_one(pdb_id: str, output_dir: Path, file_format: str, session: requests.Session) -> tuple:
    """
    Download a single PDB file.
    Returns (pdb_id, status) where status is 'ok', 'skipped', or 'missing'.
    """
    dest = output_dir / f"{pdb_id}.{file_format}"

    # Skipping already downloaded PDB's
    if dest.exists():
        return pdb_id, "skipped"

    url = FILE_URL.format(id=pdb_id, ext=file_format)
    try:
        resp = session.get(url, timeout=TIMEOUT, stream=True)
        if resp.status_code == 200:
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)
            return pdb_id, "ok"
        else:
            log.warning(f"{pdb_id}: HTTP {resp.status_code} — not found on RCSB.")
            return pdb_id, "missing"
    except requests.RequestException as exc:
        log.warning(f"{pdb_id}: network error ({exc}).")
        return pdb_id, "missing"


def download_all(pdb_ids: list, output_dir: Path, file_format: str, workers: int) -> tuple:
    """
    Download all PDB files, up to the total no. of cores at a time in run parallel.
    Returns (downloaded, skipped, missing) lists.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "PDB-Downloader (research)"})

    downloaded, skipped, missing = [], [], []

    log.info(f"Starting downloads ({workers} parallel in CPU cores) ...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_one, pid, output_dir, file_format, session): pid
            for pid in pdb_ids
        }
        with tqdm(total=len(pdb_ids), desc="Downloading", unit="file") as bar:
            for future in as_completed(futures):
                pdb_id, status = future.result()
                if status == "ok":
                    downloaded.append(pdb_id)
                elif status == "skipped":
                    skipped.append(pdb_id)
                else:
                    missing.append(pdb_id)
                bar.update(1)

    return downloaded, skipped, missing


# ─── Output helpers ───────────────────────────────────────────────────────────
def save_list(ids: list, path: Path):
    if not ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write("\n".join(ids) + "\n")
    log.info(f"Saved {len(ids)} IDs → {path}")


def save_summary(pdb_ids, downloaded, skipped, missing, output_dir: Path):
    path = output_dir / "download_summary.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Report generated",   datetime.now().isoformat()])
        w.writerow(["Total requested",    len(pdb_ids)])
        w.writerow(["Downloaded (new)",   len(downloaded)])
        w.writerow(["Skipped (existed)",  len(skipped)])
        w.writerow(["Missing / failed",   len(missing)])
        if missing:
            w.writerow([])
            w.writerow(["--- MISSING PDB IDs ---"])
            for pid in missing:
                w.writerow([pid])
    log.info(f"Summary → {path}")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Download specific PDB structure files from RCSB by ID.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download three specific files
  python rcsb_pdb_downloader_by_pdbid.py --ids 1ABC 2DEF 3GHI

  # Download from a text file of IDs
  python rcsb_pdb_downloader_by_pdbid.py --file my_list.txt

  # Download as mmCIF format
  python rcsb_pdb_downloader_by_pdbid.py --ids 1ABC 2DEF --format cif

  # Save to a custom folder with 6 parallel workers
  python rcsb_pdb_downloader_by_pdbid.py --file my_list.txt --output-dir /data/pdbs --workers 6
        """
    )
    p.add_argument(
        "--ids", nargs="+", metavar="PDBID",
        help="One or more 4-letter PDB IDs (e.g. --ids 1ABC 2DEF 3GHI)"
    )
    p.add_argument(
        "--file", metavar="PATH",
        help="Path to a text file with PDB IDs (one per line or comma-separated)"
    )
    p.add_argument(
        "--format", choices=["pdb", "cif"], default="pdb",
        help="File format to download: pdb or cif (default: pdb)"
    )
    p.add_argument(
        "--output-dir", default="pdb_downloads", metavar="PATH",
        help="Directory to save downloaded files (default: pdb_downloads/)"
    )
    p.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Number of parallel downloads (default: 4, max: 8)"
    )
    return p.parse_args()


# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    args = parse_args()
    args.workers = min(max(1, args.workers), MAX_WORKERS)

    output_dir = Path(args.output_dir)

    log.info("=" * 55)
    log.info("RCSB PDB Downloader — by ID")
    log.info(f"  Format     : {args.format.upper()}")
    log.info(f"  Output dir : {output_dir.resolve()}")
    log.info(f"  Workers    : {args.workers}")
    log.info("=" * 55)

    # Collect and validate IDs
    pdb_ids = collect_ids(args)

    # Download
    downloaded, skipped, missing = download_all(
        pdb_ids, output_dir, args.format, args.workers
    )

    # Save missing list
    if missing:
        save_list(missing, output_dir / "missing_pdbs.txt")

    # Save summary
    save_summary(pdb_ids, downloaded, skipped, missing, output_dir)

    # Final report
    log.info("=" * 55)
    log.info(f"Done.")
    log.info(f"  Downloaded (new) : {len(downloaded)}")
    log.info(f"  Skipped (existed): {len(skipped)}")
    log.info(f"  Missing / failed : {len(missing)}")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
