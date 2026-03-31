"""
NYC Board of Correction meeting scraper.

What it does per meeting:
  1. Scrapes the meeting page for PDF links and YouTube embed IDs
  2. Downloads all PDFs (skips existing)
  3. Extracts text from PDFs
  4. Downloads YouTube auto-captions (if available) as .vtt + .txt
  5. Downloads audio as .m4a (skips if already downloaded)
  6. Stores everything in SQLite

Usage:
  python scrape.py                  # scrape all years listed in YEARS
  python scrape.py --year 2026      # scrape one year only
  python scrape.py --no-audio       # skip audio download (captions only)

Database: data/boc.db
PDFs:     pdfs/
Audio:    audio/
Captions: captions/
"""

import argparse
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

BASE_URL = "https://www.nyc.gov"
ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "boc.db"
PDF_DIR = ROOT / "pdfs"
AUDIO_DIR = ROOT / "audio"
CAPTION_DIR = ROOT / "captions"

YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020]

HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; contact ted.alcorn@gmail.com)"}


# ── Database setup ────────────────────────────────────────────────────────────

def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meetings (
            id          INTEGER PRIMARY KEY,
            date        TEXT UNIQUE,          -- YYYY-MM-DD
            display     TEXT,                 -- "January 13, 2026"
            page_url    TEXT,
            youtube_id  TEXT,
            youtube_url TEXT,
            scraped_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id          INTEGER PRIMARY KEY,
            meeting_id  INTEGER REFERENCES meetings(id),
            title       TEXT,
            url         TEXT UNIQUE,
            filename    TEXT,
            doc_type    TEXT,                 -- minutes | agenda | testimony | variance | report | other
            text        TEXT,
            downloaded  INTEGER DEFAULT 0,
            extracted   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS captions (
            id          INTEGER PRIMARY KEY,
            meeting_id  INTEGER REFERENCES meetings(id) UNIQUE,
            source      TEXT,                 -- auto | manual | none
            vtt_file    TEXT,
            text        TEXT,
            fetched_at  TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
            title, text, content='documents', content_rowid='id'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS captions_fts USING fts5(
            text, content='captions', content_rowid='id'
        );
    """)
    conn.commit()


# ── Scraping helpers ─────────────────────────────────────────────────────────

def get_soup(url, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)


def get_year_meeting_links(year):
    """Return list of (display_text, full_url) for all meetings in a year."""
    if year == 2020:
        url = f"{BASE_URL}/site/boc/meetings/2020-meetings.page"
    elif year < 2020:
        url = f"{BASE_URL}/site/boc/meetings/pre-2020-meetings.page"
    else:
        url = f"{BASE_URL}/site/boc/meetings/{year}-meetings.page"

    soup = get_soup(url)
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # Individual meeting pages: either /site/boc/meetings/YYYYMMDD.page
        # or slug format like /site/boc/meetings/jan-14-2020.page
        if re.search(r'/boc/meetings/\d{8}\.page', href) or \
           re.search(r'/boc/meetings/[a-z]{3}-\d+-\d{4}\.page', href) or \
           re.search(r'/boc/meetings/[a-z]+-\d+-\d{4}\.page', href):
            full = urljoin(BASE_URL, href) if not href.startswith("http") else href
            links.append((text, full))
    return links


def classify_doc(title, url):
    t = (title + " " + url).lower()
    if "minute" in t:
        return "minutes"
    if "agenda" in t:
        return "agenda"
    if "testimon" in t or "comment" in t or "letter" in t:
        return "testimony"
    if "variance" in t:
        return "variance"
    if "report" in t or "dashboard" in t or "presentation" in t:
        return "report"
    return "other"


def scrape_meeting_page(page_url):
    """
    Returns:
      pdfs: list of (title, full_url)
      youtube_id: str or None
    """
    soup = get_soup(page_url)
    raw_html = str(soup)

    # YouTube embed IDs
    yt_match = re.search(r'youtube\.com/embed/([A-Za-z0-9_-]+)', raw_html)
    youtube_id = yt_match.group(1) if yt_match else None

    # PDF links
    pdfs = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf"):
            continue
        full = urljoin(BASE_URL, href) if not href.startswith("http") else href
        if full in seen:
            continue
        seen.add(full)
        title = a.get_text(strip=True) or Path(href).stem
        pdfs.append((title, full))

    return pdfs, youtube_id


def date_from_url(page_url, display_text):
    """Try to extract YYYY-MM-DD from the meeting page URL or display text."""
    # Numeric format: /site/boc/meetings/20260113.page
    m = re.search(r'/(\d{4})(\d{2})(\d{2})\.page', page_url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Slug format: jan-14-2020, march-10-2020, etc.
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        "january": "01", "february": "02", "march": "03", "april": "04",
        "june": "06", "july": "07", "august": "08", "september": "09",
        "october": "10", "november": "11", "december": "12",
    }
    m = re.search(r'/([a-z]+)-(\d+)-(\d{4})\.page', page_url)
    if m:
        mon = months.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"

    # Try display text like "January 14, 2020" or "January 13"
    m = re.search(r'(\w+)\s+(\d+)(?:,\s*(\d{4}))?', display_text)
    if m:
        mon = months.get(m.group(1).lower())
        year_str = m.group(3) or ""
        if mon and year_str:
            return f"{year_str}-{mon}-{m.group(2).zfill(2)}"

    return None


# ── PDF download + extraction ─────────────────────────────────────────────────

def download_pdf(url, dest_path):
    r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)


def extract_pdf_text(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            return "\n\n".join(pages)
    except Exception as e:
        return f"[extraction error: {e}]"


# ── YouTube captions + audio ─────────────────────────────────────────────────

def fetch_captions(youtube_id, caption_dir, audio_dir, download_audio=True):
    """
    Uses yt-dlp to:
      - Download auto-generated captions as .vtt (fast)
      - Download audio as .m4a (slower, skips if exists)

    Returns (source, vtt_path, plain_text) where source is 'auto', 'manual', or 'none'.
    """
    import subprocess, json

    yt_url = f"https://www.youtube.com/watch?v={youtube_id}"
    vtt_base = caption_dir / youtube_id
    audio_base = audio_dir / youtube_id

    # --- Captions ---
    vtt_path = None
    plain_text = None
    source = "none"

    # Try auto-generated English captions first, then manual
    for sub_type in ["--write-auto-subs", "--write-subs"]:
        result = subprocess.run([
            "yt-dlp",
            sub_type,
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "--skip-download",
            "--output", str(vtt_base),
            "--no-playlist",
            "--quiet",
            yt_url,
        ], capture_output=True, text=True)

        # yt-dlp writes file as <base>.en.vtt
        candidates = list(caption_dir.glob(f"{youtube_id}*.vtt"))
        if candidates:
            vtt_path = candidates[0]
            source = "auto" if "auto" in sub_type else "manual"
            # Convert vtt to plain text
            vtt_content = vtt_path.read_text(encoding="utf-8", errors="ignore")
            # Strip VTT markup: timestamps, tags, duplicate lines
            lines = []
            seen_lines = set()
            for line in vtt_content.splitlines():
                line = line.strip()
                if not line or re.match(r'^\d+$', line) or '-->' in line:
                    continue
                if line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("Kind:") or line.startswith("Language:"):
                    continue
                # Remove <c> tags and timestamps within text
                line = re.sub(r'<[^>]+>', '', line)
                line = re.sub(r'&amp;', '&', line)
                if line and line not in seen_lines:
                    seen_lines.add(line)
                    lines.append(line)
            plain_text = "\n".join(lines)
            txt_path = vtt_path.with_suffix(".txt")
            txt_path.write_text(plain_text, encoding="utf-8")
            break

    # --- Audio ---
    audio_path = None
    if download_audio:
        existing_audio = list(audio_dir.glob(f"{youtube_id}.*"))
        if existing_audio:
            print(f"    [audio already exists: {existing_audio[0].name}]")
        else:
            print(f"    Downloading audio...")
            subprocess.run([
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "m4a",
                "--audio-quality", "0",
                "--output", str(audio_base) + ".%(ext)s",
                "--no-playlist",
                "--quiet",
                "--progress",
                yt_url,
            ])
            audio_files = list(audio_dir.glob(f"{youtube_id}.*"))
            if audio_files:
                audio_path = audio_files[0]

    return source, vtt_path, plain_text


# ── Main pipeline ─────────────────────────────────────────────────────────────

def scrape_year(year, conn, download_audio=True):
    print(f"\n{'='*60}")
    print(f"  {year}")
    print(f"{'='*60}")

    meetings = get_year_meeting_links(year)
    print(f"  Found {len(meetings)} meetings\n")

    for display_text, page_url in tqdm(meetings, desc=f"{year} meetings", unit="mtg"):
        date_str = date_from_url(page_url, display_text)
        if not date_str:
            print(f"  [warn] couldn't parse date from {page_url}")
            continue

        # Check if already scraped
        existing = conn.execute(
            "SELECT id FROM meetings WHERE date=?", (date_str,)
        ).fetchone()

        if existing:
            meeting_id = existing[0]
            print(f"\n  {date_str} — already in DB (meeting_id={meeting_id}), checking for new docs...")
        else:
            print(f"\n  {date_str} — {display_text}")

        # Scrape meeting page
        try:
            pdfs, youtube_id = scrape_meeting_page(page_url)
        except Exception as e:
            print(f"    [error scraping page: {e}]")
            continue

        youtube_url = f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else None

        # Upsert meeting record
        conn.execute("""
            INSERT INTO meetings (date, display, page_url, youtube_id, youtube_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                youtube_id=excluded.youtube_id,
                youtube_url=excluded.youtube_url,
                scraped_at=excluded.scraped_at
        """, (date_str, display_text, page_url, youtube_id, youtube_url))
        conn.commit()

        meeting_id = conn.execute(
            "SELECT id FROM meetings WHERE date=?", (date_str,)
        ).fetchone()[0]

        print(f"    {len(pdfs)} PDFs, YouTube: {youtube_id or 'none'}")

        # Download + index PDFs
        for title, pdf_url in pdfs:
            filename = re.sub(r'[^\w\-.]', '_', Path(pdf_url).name)
            dest = PDF_DIR / filename

            # Check if already in DB
            existing_doc = conn.execute(
                "SELECT id, downloaded, extracted FROM documents WHERE url=?", (pdf_url,)
            ).fetchone()

            if not existing_doc:
                doc_type = classify_doc(title, pdf_url)
                conn.execute("""
                    INSERT OR IGNORE INTO documents (meeting_id, title, url, filename, doc_type)
                    VALUES (?, ?, ?, ?, ?)
                """, (meeting_id, title, pdf_url, filename, doc_type))
                conn.commit()
                existing_doc = conn.execute(
                    "SELECT id, downloaded, extracted FROM documents WHERE url=?", (pdf_url,)
                ).fetchone()

            doc_id, downloaded, extracted = existing_doc

            # Download if needed
            if not downloaded or not dest.exists():
                try:
                    download_pdf(pdf_url, dest)
                    conn.execute(
                        "UPDATE documents SET downloaded=1 WHERE id=?", (doc_id,)
                    )
                    conn.commit()
                except Exception as e:
                    print(f"    [pdf error] {Path(pdf_url).name}: {e}")
                    continue

            # Extract text if needed
            if not extracted or not existing_doc[2]:
                text = extract_pdf_text(dest)
                conn.execute(
                    "UPDATE documents SET text=?, extracted=1 WHERE id=?",
                    (text, doc_id)
                )
                conn.commit()

        # Captions + audio
        if youtube_id:
            already = conn.execute(
                "SELECT id FROM captions WHERE meeting_id=?", (meeting_id,)
            ).fetchone()
            if not already:
                print(f"    Fetching captions for {youtube_id}...")
                try:
                    source, vtt_path, plain_text = fetch_captions(
                        youtube_id, CAPTION_DIR, AUDIO_DIR, download_audio=download_audio
                    )
                    conn.execute("""
                        INSERT OR REPLACE INTO captions
                            (meeting_id, source, vtt_file, text, fetched_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                    """, (
                        meeting_id, source,
                        str(vtt_path) if vtt_path else None,
                        plain_text
                    ))
                    conn.commit()
                    print(f"    Captions: {source}")
                except Exception as e:
                    print(f"    [caption error] {e}")
            else:
                print(f"    Captions already fetched")

    # Rebuild FTS index
    conn.executescript("""
        INSERT INTO docs_fts(docs_fts) VALUES('rebuild');
        INSERT INTO captions_fts(captions_fts) VALUES('rebuild');
    """)
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Scrape a single year")
    parser.add_argument("--no-audio", action="store_true", help="Skip audio download")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_db(conn)

    years = [args.year] if args.year else YEARS

    for year in years:
        try:
            scrape_year(year, conn, download_audio=not args.no_audio)
        except Exception as e:
            print(f"\n[Error on {year}: {e}]")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
