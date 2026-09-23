"""
crawler.py — Couche réseau. Deux étages :

  1. crawl_listing()  : scrape les pages de résultats filtrées (léger,
     rapide) et met à jour la base pour CHAQUE festival trouvé.
  2. crawl_details()  : ne va sur la fiche détail QUE pour les festivals
     qui en ont besoin (jamais scrapés, ou cache expiré) — donc jamais
     de re-scraping inutile des mêmes milliers de pages à chaque run.

Politesse : délai + jitter, retry/backoff sur 429/503, faible concurrence.
"""

import time
import random
import sys
from urllib.parse import urlencode

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

try:
    from . import db
    from . import extractor
    from . import llm_extractor
except (ImportError, ValueError):
    import db
    import extractor
    import llm_extractor

BASE_URL = "https://filmfreeway.com"
SEARCH_URL = f"{BASE_URL}/festivals"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}


def create_session():
    if USE_CURL_CFFI:
        return requests.Session(impersonate="chrome124")
    return requests.Session()


def polite_get(session, url, params=None, max_retries=4, base_delay=1.5):
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=20)
        except Exception as exc:
            wait = base_delay * (2 ** attempt)
            print(f"  [!] Erreur réseau ({exc}), retry dans {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            time.sleep(base_delay + random.uniform(0, 1.0))
            return resp
        if resp.status_code in (429, 503):
            wait = base_delay * (2 ** attempt)
            print(f"  [!] {resp.status_code} reçu, pause {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        print(f"  [!] HTTP {resp.status_code} pour {resp.url}", file=sys.stderr)
        return None
    return None


def crawl_listing(conn, query=None, max_pages=10, extra_params=None, delay=1.5):
    """Scrape les pages de listing (filtrées si extra_params fourni) et
    upsert chaque festival trouvé dans la base (infos légères)."""
    session = create_session()
    total_pages = None
    found = 0

    for page in range(1, max_pages + 1):
        params = {"page": page}
        if query:
            params["q"] = query
        if extra_params:
            params.update(extra_params)

        print(f"[Listing] page {page}{f'/{total_pages}' if total_pages else ''} "
              f"— {SEARCH_URL}?{urlencode(params)}")
        resp = polite_get(session, SEARCH_URL, params=params, base_delay=delay)
        if resp is None:
            print("  [!] Échec requête listing, arrêt.", file=sys.stderr)
            break

        entries = extractor.parse_search_page(resp.text)
        if not entries:
            print("  [i] Page vide, arrêt.")
            break

        for e in entries:
            db.upsert_listing_entry(conn, e)
        found += len(entries)

        if total_pages is None:
            total_pages = extractor.get_total_pages(resp.text)
        if total_pages and page >= total_pages:
            print(f"  [i] Dernière page atteinte ({total_pages}).")
            break

    print(f"[Listing] {found} entrées trouvées/mises à jour dans la base.")
    return found


def crawl_details(conn, max_festivals=None, delay=1.5, refresh_days=14):
    """Va chercher le détail uniquement des festivals qui en ont besoin
    (jamais scrapés ou cache expiré). Ne re-scrape jamais pour rien."""
    session = create_session()
    rows = db.get_slugs_needing_detail(conn, limit=max_festivals)
    print(f"[Détail] {len(rows)} festivals à (re)scraper.")

    for i, row in enumerate(rows, 1):
        slug, url = row["slug"], row["url"]
        print(f"[Détail {i}/{len(rows)}] {slug}")
        resp = polite_get(session, url, base_delay=delay)
        if resp is None:
            db.mark_detail_fetch_failed(conn, slug)
            continue

        detail, confidence, needs_review, reason, raw_text = extractor.parse_festival_detail(
            resp.text, url, slug
        )
        db.save_detail(conn, slug, detail, confidence, needs_review, reason, refresh_days, raw_text)

        if needs_review:
            print(f"  [?] À vérifier manuellement : {reason}")
