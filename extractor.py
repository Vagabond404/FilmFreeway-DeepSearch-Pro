"""
extractor.py — Extraction 100% locale (pas de LLM, pas d'API payante).

Principe : on s'ancre sur les LIBELLÉS DE SECTION qui restent stables sur
FilmFreeway ("Dates & Deadlines", "Categories & Fees", "Venue"...) même si
les classes CSS changent. Quand un champ ne peut pas être extrait avec
confiance, on ne devine PAS : on flague le festival en `needs_review`.

C'est le principe qui remplace un LLM : plutôt que "comprendre" le texte,
on refuse honnêtement de répondre quand c'est ambigu, et un humain (toi)
tranche pour les quelques cas flagués au lieu de faire confiance à une
extraction fausse pour des milliers de festivals.
"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

BASE_URL = "https://filmfreeway.com"

STATUS_LABELS = [
    "Submissions Open", "Entries Closed", "Submissions Closed", "Opening Soon",
    "Waiver Codes Only", "Get Tickets", "Submit Now",
]

DEADLINE_LABELS = [
    "Opening Date", "Early Deadline", "Regular Deadline",
    "Late Deadline", "Final Deadline", "Extended Deadline",
    "Notification Date", "Event Date", "Withdrawal Deadline",
    "Early Bird Deadline", "Opening Deadline",
]

DATE_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}(?:\s*[-–—\ufffd]\s*\d{1,2})?,?\s*"
    r"(?:[-–—\ufffd]\s*[A-Z][a-z]+ \d{1,2},?\s*)?\d{4}|Today|TBA|TBD)"
)


def _is_deadline_label(label):
    if not label:
        return False
    clean = label.strip()
    if clean in DEADLINE_LABELS:
        return True
    if re.search(r"\b(?:Deadline|Date)\b", clean, re.IGNORECASE):
        return True
    return False


# ---------------------------------------------------------------------------
# LISTING (page de recherche) — logique robuste
# ---------------------------------------------------------------------------

def parse_search_page(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for h3 in soup.find_all(["h3", "h4"]):
        a = h3.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if not re.match(r"^/[A-Za-z0-9][\w\-]*$", href):
            continue
        if href.startswith(("/festivals", "/pages", "/login", "/sign_up", "/help")):
            continue

        name = a.get_text(strip=True)
        if not name:
            continue
        slug = href.lstrip("/")
        url = urljoin(BASE_URL, href)

        block = h3.find_parent("article") or h3.find_parent(["li", "div"]) or h3.parent
        block_text = block.get_text(separator="\n", strip=True) if block else ""

        entry = {
            "slug": slug, "name": name, "url": url,
            "status": None, "date": None, "location": None,
            "years_running": None, "reviews": None,
            "sponsored": "Sponsored" in block_text,
        }

        for label in STATUS_LABELS:
            if label in block_text:
                entry["status"] = label
                break

        m = re.search(r"([A-Z][a-z]+ \d{1,2}(?:\s*[-–—\ufffd]\s*\d{1,2})?,?\s*"
                      r"(?:[-–—\ufffd]\s*[A-Z][a-z]+ \d{1,2},?\s*)?\d{4})\s*•\s*([^\n]+)", block_text)
        if m:
            entry["date"], entry["location"] = m.group(1).strip(), m.group(2).strip()

        m = re.search(r"(First Year|\d+\s*Years?)", block_text)
        if m:
            entry["years_running"] = m.group(1).strip()

        m = re.search(r"(\d+)\s*Reviews?", block_text)
        if m:
            entry["reviews"] = int(m.group(1))

        results.append(entry)

    seen, deduped = set(), []
    for r in results:
        if r["slug"] not in seen:
            seen.add(r["slug"])
            deduped.append(r)
    return deduped


def get_total_pages(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"of\s+([\d,]+)\s+Festivals", text)
    if not m:
        return None
    total = int(m.group(1).replace(",", ""))
    return max(1, -(-total // 50))


# ---------------------------------------------------------------------------
# DETAIL (fiche festival) — extraction + score de confiance
# ---------------------------------------------------------------------------

def _between(full_text, start_label, end_labels):
    start = full_text.find(start_label)
    if start == -1:
        return ""
    start += len(start_label)
    end = len(full_text)
    for lbl in end_labels:
        idx = full_text.find(lbl, start)
        if idx != -1:
            end = min(end, idx)
    return full_text[start:end].strip()


def extract_dates(full_text, soup=None):
    """Retourne (liste_de_dates, ambigu:bool)."""
    # 1. Essai DOM via classe ProfileFestival-datesDeadlines si soup disponible
    if soup:
        ul = soup.find("ul", class_=re.compile(r"ProfileFestival-datesDeadlines", re.I))
        if ul:
            dates = []
            for li in ul.find_all("li"):
                text = li.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) >= 2:
                    if _is_deadline_label(lines[1]):
                        dates.append({"label": lines[1], "date": lines[0]})
                    elif _is_deadline_label(lines[0]):
                        dates.append({"label": lines[0], "date": lines[1]})
            if dates:
                return dates, False

    # 2. Fallback textuel ancré sur libellés
    block = _between(full_text, "Dates & Deadlines", ["Categories & Fees", "Other Festivals You May Like"])
    if not block:
        return [], True  # section absente = suspect

    lines = [l.strip() for l in block.split("\n") if l.strip()]
    dates, i, unmatched = [], 0, 0
    while i < len(lines):
        if i + 1 < len(lines) and _is_deadline_label(lines[i + 1]) and DATE_RE.fullmatch(lines[i]):
            dates.append({"label": lines[i + 1], "date": lines[i]})
            i += 2
        elif i + 1 < len(lines) and _is_deadline_label(lines[i]) and DATE_RE.fullmatch(lines[i + 1]):
            dates.append({"label": lines[i], "date": lines[i + 1]})
            i += 2
        else:
            unmatched += 1
            i += 1

    ambiguous = (not dates) or (unmatched > len(lines) / 2)
    return dates, ambiguous


def extract_categories(full_text, soup=None):
    """Retourne (liste_de_categories, ambigu:bool)."""
    # 1. Essai DOM via classe ProfileCategories si soup disponible
    if soup:
        ul = soup.find("ul", class_=re.compile(r"ProfileCategories", re.I))
        if ul:
            categories = []
            for li in ul.find_all("li", recursive=False):
                text = li.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in text.split("\n") if l.strip() and l != "Submit Now"]
                if not lines:
                    continue
                cat = {"name": lines[0], "description": None, "deadline_type": None, "fees": {}}
                i = 1
                desc_parts = []
                while i < len(lines) and not _is_deadline_label(lines[i]) and ":" not in lines[i]:
                    desc_parts.append(lines[i])
                    i += 1
                if desc_parts:
                    cat["description"] = " ".join(desc_parts)
                while i < len(lines):
                    line = lines[i]
                    if _is_deadline_label(line) and ":" not in line:
                        if not cat["deadline_type"]:
                            cat["deadline_type"] = line
                        i += 1
                    elif ":" in line:
                        parts = line.split(":", 1)
                        cat["fees"][parts[0].strip()] = parts[1].strip()
                        i += 1
                    elif line.endswith(":") and i + 1 < len(lines):
                        cat["fees"][line.rstrip(":")] = lines[i + 1]
                        i += 2
                    else:
                        i += 1
                if cat["name"]:
                    categories.append(cat)
            if categories:
                unparsed = sum(1 for c in categories if not c["fees"])
                ambiguous = unparsed > len(categories) / 2
                return categories, ambiguous

    # 2. Fallback textuel ancré sur libellés
    block = _between(full_text, "Categories & Fees", ["Other Festivals You May Like", "Share this Event"])
    if not block:
        return [], True

    # Découpage par blocs "Submit Now" ou motifs de catégories
    raw_chunks = block.split("Submit Now")
    categories, unparsed = [], 0
    for chunk in raw_chunks:
        lines = [l.strip() for l in chunk.split("\n") if l.strip()]
        if not lines:
            continue
        cat = {"name": lines[0], "description": None, "deadline_type": None, "fees": {}}
        i, desc_lines = 1, []
        while i < len(lines) and not _is_deadline_label(lines[i]) and ":" not in lines[i]:
            desc_lines.append(lines[i])
            i += 1
        if desc_lines:
            cat["description"] = " ".join(desc_lines)
        found_fee = False
        while i < len(lines):
            if _is_deadline_label(lines[i]):
                if not cat["deadline_type"]:
                    cat["deadline_type"] = lines[i]
                i += 1
            elif ":" in lines[i]:
                parts = lines[i].split(":", 1)
                cat["fees"][parts[0].strip()] = parts[1].strip()
                found_fee = True
                i += 1
            elif lines[i].endswith(":") and i + 1 < len(lines):
                cat["fees"][lines[i].rstrip(":")] = lines[i + 1]
                found_fee = True
                i += 2
            else:
                i += 1
        if not found_fee:
            unparsed += 1
        if cat["name"] and len(cat["name"]) < 120:
            categories.append(cat)

    ambiguous = (not categories) or (unparsed > len(categories) / 2 if categories else True)
    return categories, ambiguous


def extract_festival_constraints(full_text, categories, rules="", description="", name="", slug=""):
    """Analyse en profondeur le texte, les règlements et les catégories pour extraire
    les 5 familles de contraintes : IA, durées max, dates d'achèvement, premières,
    politiques de diffusion en ligne, accréditations (Oscars, BAFTA, etc.), et catégories spéciales."""
    combined = f"{name or ''}\n{slug or ''}\n{rules or ''}\n{description or ''}\n{full_text or ''}".lower()
    cat_text = " ".join((c.get("name", "") + " " + (c.get("description") or "")) for c in categories).lower()

    # 1. Limite de durée (Runtime limit) pour les courts-métrages
    max_short_runtime = None

    # Limite explicite dans le nom/titre du festival (ex: 3 Minute Film Festival)
    m_title = re.search(r"\b(\d{1,2})[\s\-]+minute\s+(?:film\s+festival|challenge|fest)\b", combined)
    if m_title:
        max_short_runtime = int(m_title.group(1))

    if not max_short_runtime:
        # Analyser les catégories de courts standards (en ignorant les sous-catégories micro/super-short isolées)
        standard_limits = []
        has_general_short = False
        for c in categories:
            c_name = c.get("name", "").lower()
            c_desc = (c.get("description") or "").lower()
            c_full = f"{c_name} {c_desc}"
            
            is_micro = bool(re.search(r"\b(micro|super[\s\-]?short|short[\s\-]?short|shorter short|1[\s\-]?min|2[\s\-]?min|3[\s\-]?min|under [1235]\s*min)\b", c_full))
            
            m_bound = re.search(r"(?:between\s+\d+\s+and|not exceeding|cannot exceed|max(?:imum)?|up to|under|less than)\s*(\d{1,3})\s*(?:mins?|minutes?|m\b)", c_full)
            if not m_bound:
                m_bound = re.search(r"(\d{1,3})\s*(?:mins?|minutes?|m\b)\s*(?:or\s+under|or\s+less)", c_full)
            if m_bound:
                val = int(m_bound.group(1))
                if 1 <= val <= 90:
                    if not is_micro:
                        standard_limits.append(val)
            else:
                if any(k in c_name for k in ["short", "court", "documentary", "fiction", "animation", "asian"]):
                    if not is_micro:
                        has_general_short = True

        # Analyser les déclarations globales dans le règlement
        rule_limits = []
        for m in re.finditer(r"(?:all\s+)?(?:shorts?|court|films?)[^\n\.\;]{0,40}?(?:between\s+\d+\s+and|not exceeding|cannot exceed|max(?:imum)?|up to|under)\s*(\d{1,3})\s*(?:mins?|minutes?|m\b)", combined):
            val = int(m.group(1))
            if 5 <= val <= 90:
                rule_limits.append(val)

        candidates = standard_limits + rule_limits
        if candidates:
            max_short_runtime = max(candidates)
        elif has_general_short:
            max_short_runtime = None  # Catégories de courts générales acceptant les durées standards
        else:
            micro_limits = [int(m.group(1)) for m in re.finditer(r"(?:under|less than|max)\s*(\d{1,2})\s*(?:mins?|minutes)", cat_text)]
            if micro_limits:
                max_short_runtime = max(micro_limits)

    # 2. Date d'achèvement minimale (Completion cutoff year)
    min_completion_year = None
    m_comp = re.search(r"(?:completed|produced|released|made)\s*(?:on or )?after\s*(?:[a-z]+)?\s*\d*,?\s*(202\d)", combined)
    if m_comp:
        min_completion_year = int(m_comp.group(1))

    # 3. Politique Intelligence Artificielle (IA)
    ai_banned = bool(re.search(
        r"(?:strictly prohibits?|does not accept|will not accept|not eligible|disqualified|prohibited)[^\n\.\;]{0,50}?\b(?:ai|artificial intelligence|generative ai|sora|midjourney)\b",
        combined
    ) or re.search(r"\b(?:no ai\b|ai generated[^\n\.\;]{0,40}?\b(?:not permitted|banned|forbidden|not allowed))\b", combined))

    has_ai_category = bool(re.search(r"\b(ai|artificial intelligence|generative|prompt|ia)\b", cat_text))
    ai_disclosure_required = bool(re.search(r"(?:ai|artificial intelligence)[^\n\.\;]{0,50}?(?:disclosure|disclose|declare|declaration|statement)", combined))

    # 4. Statut de Première requis
    premiere_required = "none"
    if re.search(r"\bworld premiere (?:is )?required\b", combined):
        premiere_required = "world"
    elif re.search(r"\b(?:international|national|country) premiere (?:is )?required\b", combined):
        premiere_required = "national"
    elif re.search(r"\b(?:regional|state) premiere (?:is )?required\b", combined):
        premiere_required = "regional"

    # 5. Disponibilité publique en ligne (YouTube/Vimeo public) interdite ?
    public_online_banned = bool(re.search(
        r"(?:publicly available|available publicly|available on youtube|available online|on the internet)[^\n\.\;]{0,50}?(?:not eligible|cannot be submitted|disqualified|prohibited|not accepted)",
        combined
    ) or re.search(r"(?:must not be|cannot be)[^\n\.\;]{0,40}?(?:available online|publicly accessible)", combined))

    # 6. Catégories & opportunités spéciales
    has_student_category = bool(re.search(r"\b(student|school|university|college|lycée|école|youth|jeunesse|teen)\b", cat_text))
    has_youth_category = bool(re.search(r"\b(youth|teen|teenager|high school|jeunesse|scolaire|under 18|young filmmakers?)\b", cat_text))
    adult_only_restriction = bool(re.search(r"\b(?:must be|at least|minimum age of)\s*18\b|\b18\s*(?:years of age|\+)\s*(?:only|required)\b", combined))
    has_first_time_category = bool(re.search(r"\b(first-time|debut|first film|emerging director|première œuvre|premier film)\b", cat_text))
    has_female_category = bool(re.search(r"\b(women|female|femme|réalisatrice)\b", cat_text))
    has_lgbtq_category = bool(re.search(r"\b(lgbt|lgbtq|queer|gay|lesbian)\b", cat_text))
    has_indigenous_category = bool(re.search(r"\b(indigenous|native|autochtone|first nations)\b", cat_text))

    # 7. Accréditations prestigieuses
    is_oscar_qualifying = bool(re.search(r"\b(academy award|oscar qualifying|oscar®)\b", combined))
    is_bafta_qualifying = bool(re.search(r"\b(bafta qualifying|bafta)\b", combined))
    is_csa_qualifying = bool(re.search(r"\b(canadian screen award)\b", combined))
    is_melies_qualifying = bool(re.search(r"\b(méliès d'or|melies d'or|melies)\b", combined))
    is_fiapf_accredited = bool(re.search(r"\b(fiapf)\b", combined))
    is_ffa_member = bool(re.search(r"\b(film festival alliance|ffa member)\b", combined))

    # 8. Type d'événement (Live screening, Virtual, Contest only)
    is_live_screening = bool(re.search(r"\b(live screening|theater|cinema|theatre|screened in front of an audience|projections en salle)\b", combined))
    is_online_festival = bool(re.search(r"\b(virtual festival|online festival|streamed online)\b", combined))
    is_contest_only = bool(re.search(r"\b(contest only|awards only|no public screening|competition only)\b", combined))

    # 9. Exclusivité thématique / type de projet & sous-titres
    has_general_fiction = any(k in cat_text for k in ["fiction", "narrative", "drama", "comedy", "animation", "live action"])
    has_live_action = any(k in cat_text for k in ["live action", "fiction", "documentary", "docu"])

    is_doc_only = bool(
        re.search(r"\b(?:documentary film festival|documentaries only|dedicated exclusively to documentaries|only documentaries)\b", combined)
        or (("documentary" in combined or "docs" in (name.lower() + " " + slug.lower())) and not has_general_fiction and "documentary" in (description or "").lower())
    )
    is_animation_only = bool(
        re.search(r"\b(?:animation film festival|animated films only|dedicated exclusively to animation|only animated)\b", combined)
        or (("animation" in combined or "anim" in (name.lower() + " " + slug.lower())) and not has_live_action and "animated" in (description or "").lower())
    )
    is_fiction_only = bool(re.search(r"\b(?:fiction film festival|narrative fiction only)\b", combined))
    english_subtitles_mandatory = bool(re.search(r"\b(?:must have english subtitles|english subtitles (?:are )?required|all non-english films must have english subtitles)\b", combined))

    return {
        "max_short_runtime_mins": max_short_runtime,
        "min_completion_year": min_completion_year,
        "ai_banned": ai_banned,
        "has_ai_category": has_ai_category,
        "ai_disclosure_required": ai_disclosure_required,
        "premiere_required": premiere_required,
        "public_online_banned": public_online_banned,
        "has_student_category": has_student_category,
        "has_youth_category": has_youth_category,
        "adult_only_restriction": adult_only_restriction,
        "has_first_time_category": has_first_time_category,
        "has_female_category": has_female_category,
        "has_lgbtq_category": has_lgbtq_category,
        "has_indigenous_category": has_indigenous_category,
        "is_oscar_qualifying": is_oscar_qualifying,
        "is_bafta_qualifying": is_bafta_qualifying,
        "is_csa_qualifying": is_csa_qualifying,
        "is_melies_qualifying": is_melies_qualifying,
        "is_fiapf_accredited": is_fiapf_accredited,
        "is_ffa_member": is_ffa_member,
        "is_live_screening": is_live_screening,
        "is_online_festival": is_online_festival,
        "is_contest_only": is_contest_only,
        "is_doc_only": is_doc_only,
        "is_animation_only": is_animation_only,
        "is_fiction_only": is_fiction_only,
        "english_subtitles_mandatory": english_subtitles_mandatory,
    }


def extract_venue(full_text):
    block = _between(full_text, "Venue", ["Other Festivals You May Like", "Share this Event"])
    if not block:
        return None, None
    lines = [l for l in block.split("\n") if l and l != "View Map"]
    if not lines:
        return None, None
    name = lines[0]
    address = " ".join(lines[1:]) if len(lines) > 1 else None
    return name, address


def parse_festival_detail(html, url, slug):
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text("\n", strip=True)

    data = {
        "url": url, "slug": slug, "name": None, "event_type": None,
        "years_running": None, "status": None, "description": None,
        "location": None, "venue_name": None, "venue_address": None,
        "dates": [], "categories": [], "awards": None, "rules": None,
        "constraints": {},
    }
    reasons = []
    confidence = 1.0

    h1 = soup.find("h1")
    data["name"] = h1.get_text(strip=True) if h1 else None
    if not data["name"]:
        confidence -= 0.3
        reasons.append("titre introuvable")

    for label in STATUS_LABELS:
        if label in full_text:
            data["status"] = label
            break

    m = re.search(r"(First Year|\d+\s*Years?)\s*Running", full_text)
    if m:
        data["years_running"] = m.group(1)

    desc_zone = full_text.split("Dates & Deadlines")[0]
    paragraphs = [p.strip() for p in desc_zone.split("\n") if len(p.strip()) > 80]
    if paragraphs:
        data["description"] = max(paragraphs, key=len)
    else:
        confidence -= 0.15
        reasons.append("description non trouvée (page peut-être atypique)")

    data["awards"] = _between(full_text, "Awards & Prizes", ["Rules & Terms", "Dates & Deadlines"]) or None
    data["rules"] = _between(full_text, "Rules & Terms", ["Dates & Deadlines", "Categories & Fees"]) or None

    venue_name, venue_address = extract_venue(full_text)
    data["venue_name"], data["venue_address"] = venue_name, venue_address

    loc_match = re.search(r"maps\.google\.com/\?q=([^\)\"&]+)", html)
    if loc_match:
        data["location"] = loc_match.group(1).replace("+", " ").split("&")[0]
    elif not venue_address:
        confidence -= 0.1
        reasons.append("localisation introuvable")

    dates, dates_ambiguous = extract_dates(full_text, soup=soup)
    data["dates"] = dates
    if dates_ambiguous:
        confidence -= 0.35
        reasons.append("dates/deadlines ambiguës ou absentes")

    categories, cats_ambiguous = extract_categories(full_text, soup=soup)
    data["categories"] = categories
    if cats_ambiguous:
        confidence -= 0.35
        reasons.append("catégories/tarifs ambigus ou absents")

    # Extraction approfondie des 5 familles de contraintes
    data["constraints"] = extract_festival_constraints(
        full_text, categories, data.get("rules") or "", data.get("description") or "",
        name=data.get("name") or "", slug=slug
    )

    confidence = max(0.0, round(confidence, 2))
    needs_review = confidence < 0.6 or dates_ambiguous or cats_ambiguous
    review_reason = "; ".join(reasons) if reasons else None

    return data, confidence, needs_review, review_reason, full_text
