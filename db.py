"""
db.py — Stockage SQLite pour le système d'intelligence FilmFreeway.

Un seul fichier .db qui sert de cache persistant : on ne re-scrape jamais
un festival pour rien. Chaque festival a un statut de fraîcheur et un
statut de confiance sur les données extraites.
"""

import sqlite3
import json
import datetime

DB_PATH = "filmfreeway.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS festivals (
    slug TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    name TEXT,

    -- infos issues du LISTING (rapides, peu fiables mais toujours présentes)
    listing_status TEXT,
    listing_date TEXT,
    listing_location TEXT,
    listing_years_running TEXT,
    listing_reviews INTEGER,
    listing_sponsored INTEGER,

    -- infos issues du DETAIL (riches mais coûteuses à obtenir)
    detail_json TEXT,          -- JSON complet extrait de la page détail
    raw_text TEXT,             -- texte brut de la page (réutilisé par le LLM local, pas de re-scrape)
    confidence REAL,           -- 0.0 à 1.0
    needs_review INTEGER DEFAULT 0,
    review_reason TEXT,
    extracted_by TEXT DEFAULT 'regex',  -- 'regex' ou 'llm'

    -- gestion du cache / politesse
    last_scraped_listing TEXT,
    last_scraped_detail TEXT,
    next_detail_check TEXT,    -- ne pas re-scraper le détail avant cette date
    detail_fetch_failed_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_needs_review ON festivals(needs_review);
CREATE INDEX IF NOT EXISTS idx_next_check ON festivals(next_detail_check);

-- Watchlist / Saved Festivals
CREATE TABLE IF NOT EXISTS saved_festivals (
    slug TEXT PRIMARY KEY,
    saved_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    submission_status TEXT DEFAULT 'To Submit',
    FOREIGN KEY(slug) REFERENCES festivals(slug) ON DELETE CASCADE
);

-- Historique & Sauvegarde des analyses passées (Sessions)
CREATE TABLE IF NOT EXISTS analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    film_title TEXT,
    profile_json TEXT NOT NULL,
    results_json TEXT NOT NULL,
    total_count INTEGER DEFAULT 0,
    top_match_pct INTEGER DEFAULT 0,
    note TEXT DEFAULT ''
);
"""


def get_conn(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_listing_entry(conn, entry):
    """Insère ou met à jour un festival à partir d'une ligne de listing.
    N'écrase JAMAIS les données de détail déjà présentes."""
    conn.execute(
        """
        INSERT INTO festivals (slug, url, name, listing_status, listing_date,
            listing_location, listing_years_running, listing_reviews,
            listing_sponsored, last_scraped_listing)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            url=excluded.url,
            name=excluded.name,
            listing_status=excluded.listing_status,
            listing_date=excluded.listing_date,
            listing_location=excluded.listing_location,
            listing_years_running=excluded.listing_years_running,
            listing_reviews=excluded.listing_reviews,
            listing_sponsored=excluded.listing_sponsored,
            last_scraped_listing=excluded.last_scraped_listing
        """,
        (
            entry["slug"], entry["url"], entry["name"], entry.get("status"),
            entry.get("date"), entry.get("location"), entry.get("years_running"),
            entry.get("reviews"), int(bool(entry.get("sponsored"))),
            datetime.datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


def save_detail(conn, slug, detail, confidence, needs_review, review_reason, refresh_days, raw_text=None):
    next_check = (
        datetime.datetime.utcnow() + datetime.timedelta(days=refresh_days)
    ).isoformat()
    conn.execute(
        """
        UPDATE festivals SET
            detail_json=?, raw_text=?, confidence=?, needs_review=?, review_reason=?,
            last_scraped_detail=?, next_detail_check=?, detail_fetch_failed_count=0,
            extracted_by='regex'
        WHERE slug=?
        """,
        (
            json.dumps(detail, ensure_ascii=False), raw_text, confidence, int(needs_review),
            review_reason, datetime.datetime.utcnow().isoformat(), next_check, slug,
        ),
    )
    conn.commit()


def save_llm_correction(conn, slug, detail, confidence=0.8):
    """Écrase l'extraction avec le résultat du LLM local, marque comme résolu."""
    conn.execute(
        """
        UPDATE festivals SET
            detail_json=?, confidence=?, needs_review=0,
            review_reason='corrigé par LLM local', extracted_by='llm'
        WHERE slug=?
        """,
        (json.dumps(detail, ensure_ascii=False), confidence, slug),
    )
    conn.commit()


def get_review_queue_with_raw(conn):
    rows = conn.execute(
        "SELECT slug, url, name, raw_text, review_reason FROM festivals "
        "WHERE needs_review=1 AND raw_text IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_detail_fetch_failed(conn, slug, retry_hours=6):
    next_check = (
        datetime.datetime.utcnow() + datetime.timedelta(hours=retry_hours)
    ).isoformat()
    conn.execute(
        """
        UPDATE festivals SET
            detail_fetch_failed_count = detail_fetch_failed_count + 1,
            next_detail_check=?
        WHERE slug=?
        """,
        (next_check, slug),
    )
    conn.commit()


def get_slugs_needing_detail(conn, priority_slugs=None, limit=None):
    """Festivals dont le détail n'a jamais été scrapé, ou dont le cache a expiré.
    Priorise en premier les slugs passés dans priority_slugs (ex: ceux trouvés dans la recherche en cours)."""
    now = datetime.datetime.utcnow().isoformat()
    if priority_slugs:
        # Prioritize matching slugs found in this search
        placeholders = ",".join("?" for _ in priority_slugs)
        q = f"""
            SELECT slug, url FROM festivals
            WHERE slug IN ({placeholders})
              AND (last_scraped_detail IS NULL OR next_detail_check IS NULL OR next_detail_check < ?)
            ORDER BY listing_sponsored ASC
        """
        rows = conn.execute(q, (*priority_slugs, now)).fetchall()
        out = list(rows)
        if limit and len(out) >= limit:
            return out[:limit]
        
        rem = (limit - len(out)) if limit else None
        q_rem = f"""
            SELECT slug, url FROM festivals
            WHERE slug NOT IN ({placeholders})
              AND (last_scraped_detail IS NULL OR next_detail_check IS NULL OR next_detail_check < ?)
            ORDER BY listing_sponsored ASC, slug ASC
        """
        if rem:
            q_rem += f" LIMIT {int(rem)}"
        rows_rem = conn.execute(q_rem, (*priority_slugs, now)).fetchall()
        out.extend(rows_rem)
        return out
    else:
        q = """
            SELECT slug, url FROM festivals
            WHERE last_scraped_detail IS NULL
               OR next_detail_check IS NULL
               OR next_detail_check < ?
            ORDER BY listing_sponsored ASC, slug ASC
        """
        if limit:
            q += f" LIMIT {int(limit)}"
        return conn.execute(q, (now,)).fetchall()


def get_all_with_detail(conn):
    rows = conn.execute(
        "SELECT * FROM festivals WHERE detail_json IS NOT NULL"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail_json"] = json.loads(d["detail_json"]) if d["detail_json"] else None
        out.append(d)
    return out


def get_review_queue(conn):
    rows = conn.execute(
        "SELECT slug, url, name, review_reason, confidence FROM festivals WHERE needs_review=1"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# FAVORIS / FESTIVALS SAUVEGARDÉS (WATCHLIST)
# ---------------------------------------------------------------------------

def save_festival_bookmark(conn, slug, notes="", submission_status="À soumettre"):
    """Ajoute ou met à jour un festival dans les favoris."""
    now = datetime.datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO saved_festivals (slug, saved_at, notes, submission_status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            notes = excluded.notes,
            submission_status = excluded.submission_status
        """,
        (slug, now, notes or "", submission_status or "À soumettre")
    )
    conn.commit()


def remove_festival_bookmark(conn, slug):
    """Retire un festival des favoris."""
    conn.execute("DELETE FROM saved_festivals WHERE slug = ?", (slug,))
    conn.commit()


def update_saved_festival(conn, slug, notes=None, submission_status=None):
    """Met à jour les notes ou le statut de soumission d'un festival sauvegardé."""
    fields = []
    vals = []
    if notes is not None:
        fields.append("notes = ?")
        vals.append(notes)
    if submission_status is not None:
        fields.append("submission_status = ?")
        vals.append(submission_status)
    if not fields:
        return
    vals.append(slug)
    conn.execute(f"UPDATE saved_festivals SET {', '.join(fields)} WHERE slug = ?", vals)
    conn.commit()


def get_saved_festivals(conn):
    """Retourne la liste de tous les festivals sauvegardés avec leurs métadonnées et fiches."""
    rows = conn.execute(
        """
        SELECT sf.slug, sf.saved_at, sf.notes, sf.submission_status,
               f.url, f.name, f.listing_status, f.listing_date, f.listing_location,
               f.listing_years_running, f.listing_reviews, f.detail_json, f.confidence
        FROM saved_festivals sf
        LEFT JOIN festivals f ON sf.slug = f.slug
        ORDER BY sf.saved_at DESC
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail_json"):
            try:
                d["detail_json"] = json.loads(d["detail_json"])
            except Exception:
                pass
        out.append(d)
    return out


def is_festival_saved(conn, slug):
    """Vérifie si un festival est dans les favoris."""
    row = conn.execute("SELECT slug, notes, submission_status FROM saved_festivals WHERE slug = ?", (slug,)).fetchone()
    if row:
        return {"saved": True, "notes": row["notes"], "submission_status": row["submission_status"]}
    return {"saved": False, "notes": "", "submission_status": "À soumettre"}


# ---------------------------------------------------------------------------
# HISTORIQUE DES ANALYSES & SESSIONS
# ---------------------------------------------------------------------------

def save_analysis_snapshot(conn, name, film_title, profile, results, note=""):
    """Enregistre un snapshot complet d'une analyse terminée pour rechargement direct."""
    now = datetime.datetime.utcnow().isoformat()
    total_count = len(results)
    top_pct = 0
    if results:
        top_pct = max(r.get("match_pct", 0) for r in results)

    cur = conn.execute(
        """
        INSERT INTO analysis_history (name, created_at, film_title, profile_json, results_json, total_count, top_match_pct, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            now,
            film_title or "Court-Métrage",
            json.dumps(profile, ensure_ascii=False),
            json.dumps(results, ensure_ascii=False),
            total_count,
            top_pct,
            note or ""
        )
    )
    conn.commit()
    return cur.lastrowid


def list_analysis_snapshots(conn):
    """Liste sommaire des analyses sauvegardées (sans le gros JSON de résultats pour la rapidité)."""
    rows = conn.execute(
        """
        SELECT id, name, created_at, film_title, total_count, top_match_pct, note
        FROM analysis_history
        ORDER BY id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_analysis_snapshot(conn, snapshot_id):
    """Récupère l'intégralité d'un snapshot (profil + résultats) pour restauration."""
    row = conn.execute("SELECT * FROM analysis_history WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else {}
    d["results"] = json.loads(d["results_json"]) if d["results_json"] else []
    return d


def delete_analysis_snapshot(conn, snapshot_id):
    """Supprime une ancienne analyse."""
    conn.execute("DELETE FROM analysis_history WHERE id = ?", (snapshot_id,))
    conn.commit()

