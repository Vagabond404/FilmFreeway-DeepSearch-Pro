"""
llm_extractor.py — Extraction de secours via un mini LLM LOCAL (pas d'API payante).

Utilisé UNIQUEMENT pour les festivals flagués `needs_review` par extractor.py
(cas rares/ambigus) — le gros du volume reste géré par le regex, gratuit et
rapide. On ne demande pas au LLM d'écrire, juste de relire un texte anglais
et remplir un JSON fermé : une tâche largement à la portée d'un modèle 1-2B
quantifié tournant sur CPU.

PRÉ-REQUIS
  1. Installer Ollama : https://ollama.com (Linux/Windows/Mac, marche sur ARM
     donc utilisable directement sur un serveur Armbian).
  2. Télécharger un petit modèle :
       ollama pull qwen2.5:0.5b
     (ou qwen2.5:1.5b si la machine a >= 4 Go de RAM libre)
  3. Ollama tourne en arrière-plan sur http://localhost:11434 (par défaut).
     Si le modèle tourne sur ton serveur Armbian et que tu lances ce script
     depuis ton PC, remplace OLLAMA_URL par l'IP du serveur (ex: via DuckDNS).
"""

import json
import re
import requests

try:
    from . import db
except (ImportError, ValueError):
    import db

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:0.5b"

PROMPT_TEMPLATE = """You will read a messy, unstructured English page from a film festival submission website (FilmFreeway) and extract structured data.

Return ONLY valid JSON, no explanation, no markdown fences, matching exactly this shape:
{{
  "description": "<one paragraph describing the festival, or null>",
  "dates": [{{"label": "<e.g. Regular Deadline>", "date": "<e.g. June 30, 2026>"}}],
  "categories": [{{"name": "<category name>", "fees": {{"Standard": "<amount or Free>", "Student": "<amount or Free>"}}}}],
  "venue_name": "<venue name or null>",
  "location": "<city, country or null>"
}}

If a field is truly not present in the text, use null or an empty list. Do not invent data.

PAGE TEXT:
\"\"\"
{page_text}
\"\"\"

JSON:"""


def _extract_json_block(text):
    """Le modèle peut entourer le JSON de texte parasite ; on isole le bloc {...}."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def extract_with_llm(raw_text, model=DEFAULT_MODEL, ollama_url=OLLAMA_URL, max_chars=6000, timeout=120):
    """Retourne un dict (mêmes clés que l'extraction regex) ou None si échec."""
    truncated = raw_text[:max_chars]  # les petits modèles ont un contexte limité
    prompt = PROMPT_TEMPLATE.format(page_text=truncated)

    try:
        resp = requests.post(
            ollama_url,
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [!] Ollama inaccessible ({exc}). Vérifie qu'il tourne bien.")
        return None

    raw_output = resp.json().get("response", "")
    json_block = _extract_json_block(raw_output)
    if not json_block:
        return None

    try:
        parsed = json.loads(json_block)
    except json.JSONDecodeError:
        return None

    # Validation minimale de forme — si ça ne ressemble pas au schéma, on rejette
    if not isinstance(parsed.get("dates", []), list) or not isinstance(parsed.get("categories", []), list):
        return None

    return parsed


def run_llm_review_pass(conn, model=DEFAULT_MODEL, ollama_url=OLLAMA_URL):
    """Relit tous les festivals en attente de revue via le LLM local et
    corrige ceux qu'il arrive à comprendre correctement."""
    queue = db.get_review_queue_with_raw(conn)
    print(f"[LLM] {len(queue)} festival(s) à retenter via {model}.")

    fixed, still_stuck = 0, 0
    for i, item in enumerate(queue, 1):
        print(f"[LLM {i}/{len(queue)}] {item['name'] or item['slug']}")
        result = extract_with_llm(item["raw_text"], model=model, ollama_url=ollama_url)
        if result:
            db.save_llm_correction(conn, item["slug"], result)
            fixed += 1
        else:
            still_stuck += 1
            print("  [?] Le LLM n'a pas réussi non plus — reste à vérifier à la main.")

    print(f"[LLM] {fixed} corrigés, {still_stuck} toujours à vérifier manuellement.")


def generate_ai_festival_summary(fest_data, profile, model=DEFAULT_MODEL, ollama_url=OLLAMA_URL):
    """Generates an analytical, structured festival summary in English for the filmmaker.
    Attempts local LLM (Ollama) first if available, falling back to a structured analytical summary."""
    fest_name = fest_data.get("name") or fest_data.get("slug") or "This Festival"
    detail = fest_data.get("detail") or fest_data.get("detail_json") or {}
    match = fest_data.get("match") or {}
    desc = detail.get("description") or ""
    rules = detail.get("rules") or ""
    awards = detail.get("awards") or ""
    venue_name = detail.get("venue_name") or ""
    location = fest_data.get("location") or detail.get("location") or ""
    years = fest_data.get("years_running") or detail.get("years_running") or ""
    film_title = profile.get("film_title", "Your Film")
    runtime = profile.get("runtime_minutes", 15)
    project_type = profile.get("project_type", "short film")
    match_pct = match.get("match_pct", 0)
    why_matched = match.get("why_matched") or []
    blockers = match.get("blockers") or []

    # 1. Try local LLM via Ollama if online
    summary_prompt = f"""You are an expert film festival programmer and international festival consultant.
Write a punchy, highly structured analytical summary in ENGLISH for the following film festival, tailored specifically for the director of "{film_title}" ({runtime}-minute {project_type}).

FESTIVAL INFORMATION:
Name: {fest_name}
Location: {location}
Years Running: {years}
Screening Venue: {venue_name}
Official Description:
{desc[:1200]}

Rules & Regulations:
{rules[:800]}

Awards & Prizes:
{awards[:600]}

Match Score with our film: {match_pct}%
Key Strengths identified: {', '.join(why_matched[:4])}
Potential Blockers / Caveats: {', '.join(blockers[:2])}

INSTRUCTIONS:
Write a HIGHLY STRUCTURED briefing in 3 concise, impactful sections:
1. **Identity & Prestige**: Who this festival is, its spirit, reputation, and international/industry reach (local, international, Oscar/BAFTA qualifying).
2. **Editorial Line & Focus**: What programmers and curators look for (artistic voice, themes, narrative style).
3. **Opportunities for "{film_title}"**: Strategic angle on why our project ({runtime} min, {project_type}) fits, and submission recommendations.

Respond DIRECTLY with the 3 sections, with no preamble or conversational filler."""

    llm_text = None
    try:
        resp = requests.post(
            ollama_url,
            json={
                "model": model,
                "prompt": summary_prompt,
                "stream": False,
                "options": {"temperature": 0.3}
            },
            timeout=25
        )
        if resp.status_code == 200:
            raw_resp = resp.json().get("response", "").strip()
            if len(raw_resp) > 80:
                llm_text = raw_resp
    except Exception:
        pass

    if llm_text:
        return {
            "source": f"ia_ollama ({model})",
            "content": llm_text,
            "headline": f"AI Strategy Brief for \"{film_title}\""
        }

    # 2. Structured heuristic fallback (instant, rich)
    accred = fest_data.get("accreditations") or []
    accred_str = ", ".join(accred) if accred else "Independent Festival"
    prestige_str = "High industry prestige" if accred else (f"Established Festival ({years})" if years else "Emerging Discovery Event")

    section1 = f"**{fest_name}** is an event based in **{location or 'International'}** ({prestige_str}). "
    if venue_name:
        section1 += f"Official selections receive theatrical physical screenings on the big screen at **{venue_name}**. "
    if accred:
        section1 += f"This is an accredited festival holding recognition from **{accred_str}**."
    elif years:
        section1 += f"Running for **{years}**, the festival features an established reputation and an engaged audience."
    else:
        section1 += "The event highlights bold indie filmmakers and emerging cinematic voices."

    if desc:
        clean_desc = desc.split("\n")[0].strip()
        if len(clean_desc) > 300:
            clean_desc = clean_desc[:297] + "..."
        section2 = f"{clean_desc} The festival favors projects with an original cinematic voice and compelling storytelling."
    else:
        section2 = "Programming highlights innovative short form and feature cinema with distinct authorial perspectives."

    if blockers:
        section3 = f"⚠️ **Attention Required for \"{film_title}\"**: Blockers or eligibility restrictions were detected ({' • '.join(blockers)}). Verify whether a waiver or alternative category is suitable."
    else:
        section3 = f"✓ **Match Fit for \"{film_title}\"** ({match_pct}% match): Your runtime ({runtime} min, {project_type.title()}) aligns well with the festival guidelines. "
        if why_matched:
            section3 += f"Confirmed strengths: {' • '.join(why_matched[:3])}."

    synthesized = f"### 1. Identity & Prestige\n{section1}\n\n### 2. Editorial Line & Focus\n{section2}\n\n### 3. Opportunities for Your Film\n{section3}"

    return {
        "source": "heuristic_analysis",
        "content": synthesized,
        "headline": f"Eligibility Brief for \"{film_title}\""
    }

