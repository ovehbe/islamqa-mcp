#!/usr/bin/env python3
"""Scrape IslamQA.info answers (English + Arabic) into a local JSON corpus.

Strategy
--------
1.  Discover all valid answer IDs from the official sitemaps.
2.  For each ID, fetch the English page (and optionally Arabic).
3.  Extract structured data from JSON-LD + HTML meta.
4.  Save incrementally to ``data/answers.json`` (merge, never lose).

The scraper is **idempotent**: re-running only fetches answers whose ID is
not yet in the local dataset, making it safe for cron / systemd timers.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

SITEMAP_INDEX = "https://islamqa.info/sitemaps/sitemap-index.xml"
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ANSWERS_FILE = DATA_DIR / "answers.json"

_thread_local = threading.local()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _thread_local.session = session
    return session


def fetch(url: str, delay: float = 0.0, retries: int = 3) -> requests.Response:
    for attempt in range(1, retries + 1):
        try:
            if delay:
                time.sleep(delay)
            resp = get_session().get(url, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise
            if attempt == retries:
                raise
        except requests.RequestException:
            if attempt == retries:
                raise
        time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to fetch {url}")


# ---------------------------------------------------------------------------
# Sitemap discovery
# ---------------------------------------------------------------------------

def discover_ids_from_sitemap(lang: str) -> Set[int]:
    """Return all answer IDs listed in sitemaps for *lang* (e.g. 'en', 'ar')."""
    idx_resp = fetch(SITEMAP_INDEX)
    idx_root = ET.fromstring(idx_resp.text)
    sitemap_urls = []
    for loc in idx_root.findall(".//s:sitemap/s:loc", SITEMAP_NS):
        text = loc.text or ""
        if f"/{lang}/answers/" in text:
            sitemap_urls.append(text)

    ids: Set[int] = set()
    for sm_url in sitemap_urls:
        resp = fetch(sm_url)
        root = ET.fromstring(resp.text)
        for loc in root.findall(".//s:url/s:loc", SITEMAP_NS):
            parts = (loc.text or "").rstrip("/").split("/")
            for i, p in enumerate(parts):
                if p == "answers" and i + 1 < len(parts):
                    try:
                        ids.add(int(parts[i + 1]))
                    except ValueError:
                        pass
                    break
    return ids


# ---------------------------------------------------------------------------
# Single-page extraction
# ---------------------------------------------------------------------------

def extract_categories(soup: BeautifulSoup) -> List[Dict[str, str]]:
    seen: Set[str] = set()
    cats: List[Dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/categories/topics/" in href:
            cat_id = href.rstrip("/").split("/")[-1]
            cat_name = a.get_text(strip=True)
            key = f"{cat_id}:{cat_name}"
            if key not in seen:
                seen.add(key)
                cats.append({"id": cat_id, "name": cat_name})
    return cats


def extract_from_page(answer_id: int, lang: str) -> Optional[Dict[str, Any]]:
    """Fetch one answer page and return structured data, or None on 404."""
    url = f"https://islamqa.info/{lang}/answers/{answer_id}"
    try:
        resp = fetch(url, delay=0.0)
    except requests.HTTPError:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # JSON-LD is the richest structured source
    ld_script = soup.find("script", type="application/ld+json")
    if not ld_script or not ld_script.string:
        return None

    try:
        ld = json.loads(ld_script.string)
    except json.JSONDecodeError:
        return None

    entity = ld.get("mainEntity", {})
    accepted = entity.get("acceptedAnswer", {})

    title = entity.get("name", "")
    question = entity.get("text", "")
    answer = accepted.get("text", "")

    if not title and not answer:
        return None

    categories = extract_categories(soup)

    return {
        "title": title,
        "question": question,
        "answer": answer,
        "date_created": entity.get("dateCreated", ""),
        "date_modified": entity.get("dateModified", ""),
        "categories": categories,
        "url": url,
    }


def scrape_answer(answer_id: int, delay: float, fetch_ar: bool) -> Optional[Dict[str, Any]]:
    """Scrape a single answer (EN required, AR optional) into a merged record."""
    if delay:
        time.sleep(delay)

    en_data = extract_from_page(answer_id, "en")

    ar_data = None
    if fetch_ar:
        if delay:
            time.sleep(delay)
        ar_data = extract_from_page(answer_id, "ar")

    if en_data is None and ar_data is None:
        return None

    record: Dict[str, Any] = {"id": answer_id}

    if en_data:
        record["title_en"] = en_data["title"]
        record["question_en"] = en_data["question"]
        record["answer_en"] = en_data["answer"]
        record["categories_en"] = en_data["categories"]
        record["url_en"] = en_data["url"]

    if ar_data:
        record["title_ar"] = ar_data["title"]
        record["question_ar"] = ar_data["question"]
        record["answer_ar"] = ar_data["answer"]
        record["categories_ar"] = ar_data["categories"]
        record["url_ar"] = ar_data["url"]

    src = en_data or ar_data
    record["date_created"] = src["date_created"]
    record["date_modified"] = src["date_modified"]

    return record


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_existing() -> Dict[int, Dict]:
    if not ANSWERS_FILE.exists():
        return {}
    with open(ANSWERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {entry["id"]: entry for entry in data}


def save_answers(answers: Dict[int, Dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sorted_list = sorted(answers.values(), key=lambda x: x["id"])
    with open(ANSWERS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_list, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_scrape(
    *,
    max_answers: Optional[int] = None,
    fetch_ar: bool = True,
    delay: float = 0.1,
    workers: int = 4,
    commit_every: int = 50,
    ids_file: Optional[str] = None,
) -> None:
    existing = load_existing()
    print(f"Existing answers on disk: {len(existing)}")

    # Discover IDs
    if ids_file:
        with open(ids_file) as f:
            all_ids = {int(line.strip()) for line in f if line.strip().isdigit()}
        print(f"Loaded {len(all_ids)} IDs from {ids_file}")
    else:
        print("Discovering answer IDs from sitemaps...")
        en_ids = discover_ids_from_sitemap("en")
        ar_ids = discover_ids_from_sitemap("ar") if fetch_ar else set()
        all_ids = en_ids | ar_ids
        print(f"Sitemap IDs — EN: {len(en_ids)}, AR: {len(ar_ids)}, union: {len(all_ids)}")

    new_ids = sorted(all_ids - set(existing.keys()))
    if max_answers:
        new_ids = new_ids[:max_answers]

    print(f"New answers to scrape: {len(new_ids)}")
    if not new_ids:
        print("Nothing to do.")
        return

    answers = dict(existing)
    scraped = 0
    errors = 0

    with tqdm(total=len(new_ids), desc="Scraping", unit="answer") as pbar:
        if workers <= 1:
            for aid in new_ids:
                try:
                    record = scrape_answer(aid, delay, fetch_ar)
                    if record:
                        answers[aid] = record
                        scraped += 1
                    else:
                        errors += 1
                except Exception as exc:
                    tqdm.write(f"  Error {aid}: {exc}")
                    errors += 1
                pbar.update(1)
                if scraped > 0 and scraped % commit_every == 0:
                    save_answers(answers)
                    tqdm.write(f"  Checkpoint: {scraped} scraped, {len(answers)} total")
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(scrape_answer, aid, delay, fetch_ar): aid
                    for aid in new_ids
                }
                for future in as_completed(futures):
                    aid = futures[future]
                    try:
                        record = future.result()
                        if record:
                            answers[aid] = record
                            scraped += 1
                        else:
                            errors += 1
                    except Exception as exc:
                        tqdm.write(f"  Error {aid}: {exc}")
                        errors += 1
                    pbar.update(1)
                    if scraped > 0 and scraped % commit_every == 0:
                        save_answers(answers)
                        tqdm.write(f"  Checkpoint: {scraped} scraped, {len(answers)} total")

    save_answers(answers)
    print(f"\nDone. Scraped: {scraped}, Errors: {errors}, Total on disk: {len(answers)}")


def run_single(answer_id: int, fetch_ar: bool = True) -> None:
    """Scrape and pretty-print a single answer (for testing)."""
    record = scrape_answer(answer_id, delay=0.0, fetch_ar=fetch_ar)
    if record is None:
        print(f"Answer {answer_id}: not found or no data")
        sys.exit(1)
    print(json.dumps(record, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape IslamQA.info answers")
    sub = parser.add_subparsers(dest="command")

    # Single answer test
    single = sub.add_parser("single", help="Scrape one answer for testing")
    single.add_argument("answer_id", type=int)
    single.add_argument("--no-ar", action="store_true", help="Skip Arabic")

    # Full scrape
    full = sub.add_parser("scrape", help="Full incremental scrape")
    full.add_argument("--max", type=int, default=None, help="Max new answers to scrape")
    full.add_argument("--no-ar", action="store_true", help="Skip Arabic")
    full.add_argument("--delay", type=float, default=0.1, help="Delay between requests (s)")
    full.add_argument("--workers", type=int, default=4, help="Parallel workers (1=sequential)")
    full.add_argument("--commit-every", type=int, default=50, help="Save every N answers")
    full.add_argument("--ids-file", type=str, default=None, help="File with answer IDs (one per line)")

    # Discover only
    disco = sub.add_parser("discover", help="Just discover and save all valid IDs")
    disco.add_argument("--lang", default="en", help="Language to discover (en, ar, or both)")

    args = parser.parse_args()

    if args.command == "single":
        run_single(args.answer_id, fetch_ar=not args.no_ar)

    elif args.command == "scrape":
        run_scrape(
            max_answers=args.max,
            fetch_ar=not args.no_ar,
            delay=args.delay,
            workers=args.workers,
            commit_every=args.commit_every,
            ids_file=args.ids_file,
        )

    elif args.command == "discover":
        langs = ["en", "ar"] if args.lang == "both" else [args.lang]
        all_ids: Set[int] = set()
        for lang in langs:
            ids = discover_ids_from_sitemap(lang)
            print(f"{lang.upper()}: {len(ids)} answer IDs")
            all_ids |= ids
        print(f"Total unique: {len(all_ids)}")
        out = DATA_DIR / "answer_ids.txt"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            for i in sorted(all_ids):
                f.write(f"{i}\n")
        print(f"Saved to {out}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
