"""
matcher.py — Score chaque festival scrapé par rapport au profil du film.

Profil attendu (voir profile_example.json) :
{
  "categories": ["Documentary", "Short"],   // mots-clés à retrouver dans les catégories du festival
  "runtime_minutes": 52,
  "country": "Cambodia",                    // pour matcher les focus régionaux si présents
  "max_fee_usd": 30,                        // budget max de frais d'inscription
  "min_years_running": 0,                   // 0 = accepte aussi les 1ers éditions
  "min_reviews": 0,
  "deadline_not_before": "2026-10-01"       // ignore les deadlines déjà dépassées avant cette date
}

Le score de matching est indépendant du score de confiance d'extraction :
un festival avec confidence basse peut quand même remonter (avec un badge
"⚠️ à vérifier") plutôt que d'être silencieusement exclu.
"""

import re
import json
import datetime
import extractor


COUNTRY_ALIASES = {
    "cambodge": ["cambodia", "cambodge", "khmer", "south east asia", "southeast asia", "asian", "asia", "phnom penh"],
    "cambodia": ["cambodia", "cambodge", "khmer", "south east asia", "southeast asia", "asian", "asia", "phnom penh"],
    "france": ["france", "french", "français", "francais"],
    "canada": ["canada", "canadian"],
    "etats-unis": ["united states", "usa", "us", "america", "american"],
    "united states": ["united states", "usa", "us", "america", "american"],
    "espagne": ["spain", "spanish", "espagne"],
    "spain": ["spain", "spanish", "espagne"],
    "italie": ["italy", "italian", "italie"],
    "italy": ["italy", "italian", "italie"],
    "allemagne": ["germany", "german", "allemagne"],
    "germany": ["germany", "german", "allemagne"],
    "royaume-uni": ["uk", "united kingdom", "britain", "british", "england"],
    "united kingdom": ["uk", "united kingdom", "britain", "british", "england"],
    "belgique": ["belgium", "belgian", "belgique"],
    "belgium": ["belgium", "belgian", "belgique"],
    "suisse": ["switzerland", "swiss", "suisse"],
    "switzerland": ["switzerland", "swiss", "suisse"],
}


def _parse_fee(fee_str):
    if not fee_str:
        return None
    if re.search(r"free", fee_str, re.IGNORECASE):
        return 0.0
    m = re.search(r"\d+(?:\.\d+)?", fee_str.replace(",", ""))
    if m:
        try:
            return float(m.group())
        except (ValueError, TypeError):
            return None
    return None


def _years_running_to_int(text):
    if not text:
        return 0
    if "First Year" in text:
        return 0
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 0


def _category_score(festival_categories, wanted_categories):
    if not wanted_categories:
        return 1.0, []
    names = " ".join(c.get("name", "") for c in festival_categories).lower()
    matched = [w for w in wanted_categories if w.lower() in names]
    return (len(matched) / len(wanted_categories)), matched


def _min_fee(festival_categories):
    fees = []
    for c in festival_categories:
        for _, v in c.get("fees", {}).items():
            f = _parse_fee(v)
            if f is not None:
                fees.append(f)
    return min(fees) if fees else None


def evaluate_category_eligibility(cat, profile):
    """Analyse fine de l'éligibilité du film pour une catégorie spécifique du festival."""
    cat_name = (cat.get("name") or "").strip()
    cat_desc = (cat.get("description") or "").strip()
    full_text = f"{cat_name} {cat_desc}".lower()

    film_runtime = profile.get("runtime_minutes", 15)
    project_type = (profile.get("project_type") or "fiction").lower()
    is_student = bool(profile.get("is_student", False))
    is_first_time = bool(profile.get("is_first_time", False))
    age_group = profile.get("director_age_group", "adult")
    ai_level = profile.get("ai_integration_level", "none")
    demographics = profile.get("demographics") or []

    blockers = []
    reasons = []
    is_recommended = False

    # 1. Scénario non filmé
    if re.search(r"\b(screenplay|script|teleplay|stage play|treatment)\b", full_text) and not re.search(r"\b(film|short|narrative|screening)\b", full_text):
        blockers.append("Catégorie réservée aux scénarios écrits (votre projet est un film produit)")

    # 2. Exclusivités thématiques & types de projet
    is_cat_doc = bool(re.search(r"\b(documentary|docu|documentaire)\b", full_text))
    is_cat_anim = bool(re.search(r"\b(animation|animated|anime)\b", full_text))
    is_cat_music_vid = bool(re.search(r"\b(music video|clip musical|music vid)\b", full_text))
    is_cat_vr = bool(re.search(r"\b(virtual reality|vr|360|xr|immersive)\b", full_text))
    is_cat_experimental = bool(re.search(r"\b(experimental|video art|art vidéo|avant-garde)\b", full_text))

    if is_cat_doc and project_type not in ("documentary", "docu"):
        blockers.append(f"Réservé aux documentaires (votre film est '{project_type.title()}')")
    elif is_cat_anim and project_type not in ("animation", "anime"):
        blockers.append(f"Réservé aux films d'animation (votre film est '{project_type.title()}')")
    elif is_cat_music_vid and project_type != "music_video":
        blockers.append(f"Réservé aux clips musicaux (votre film est '{project_type.title()}')")
    elif is_cat_vr and project_type != "vr_360":
        blockers.append("Réservé aux expériences VR / 360")

    if project_type in ("documentary", "docu") and is_cat_doc:
        reasons.append("Catégorie Documentaire dédiée")
        is_recommended = True
    elif project_type in ("animation", "anime") and is_cat_anim:
        reasons.append("Catégorie Animation dédiée")
        is_recommended = True
    elif project_type == "music_video" and is_cat_music_vid:
        reasons.append("Catégorie Clip Musical dédiée")
        is_recommended = True
    elif project_type == "experimental" and is_cat_experimental:
        reasons.append("Catégorie Expérimentale / Art Vidéo dédiée")
        is_recommended = True
    elif project_type == "fiction" and any(k in full_text for k in ["short", "fiction", "narrative", "drama", "comedy", "court"]):
        reasons.append("Catégorie Fiction / Court-métrage adaptée")

    # 3. Bornes de durée (Runtime)
    range_match = re.search(r"(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*(?:mins?|minutes?|m\b)", full_text)
    if not range_match:
        range_match = re.search(r"between\s+(\d{1,3})\s+and\s+(\d{1,3})\s*(?:mins?|minutes?|m\b)", full_text)

    if range_match:
        min_m = int(range_match.group(1))
        max_m = int(range_match.group(2))
        if film_runtime < min_m:
            blockers.append(f"Insufficient runtime ({film_runtime} min < min required {min_m} min)")
        elif film_runtime > max_m:
            blockers.append(f"Excessive runtime ({film_runtime} min > max allowed {max_m} min)")
        else:
            reasons.append(f"Runtime of {film_runtime} min within required range [{min_m}-{max_m} min]")
            is_recommended = True
    else:
        max_bound = re.search(r"(?:under|less than|max(?:imum)?|not exceeding|up to)\s*(\d{1,3})\s*(?:mins?|minutes?|m\b)", full_text)
        if max_bound:
            limit = int(max_bound.group(1))
            if film_runtime > limit:
                blockers.append(f"Runtime of {film_runtime} min exceeds maximum ceiling ({limit} min max)")
            else:
                reasons.append(f"Runtime of {film_runtime} min compliant (<= {limit} min)")
                is_recommended = True

        min_bound = re.search(r"(?:over|more than|longer than|at least)\s*(\d{1,3})\s*(?:mins?|minutes?|m\b)", full_text)
        if min_bound:
            limit = int(min_bound.group(1))
            if film_runtime < limit:
                blockers.append(f"Runtime of {film_runtime} min below minimum threshold ({limit} min)")

    # Feature film category
    is_feature_cat = bool(re.search(r"\b(feature|feature length|long[\s\-]?m[ée]trage)\b", full_text)) and not bool(re.search(r"\b(short|court)\b", full_text))
    if is_feature_cat and film_runtime < 40:
        blockers.append(f"Feature film category (requires >= 40 min, your project is {film_runtime} min)")

    # Short category general
    is_short_cat = bool(re.search(r"\b(short|shorts|court[\s\-]?m[ée]trage|court)\b", full_text))
    if is_short_cat and film_runtime <= 40 and not blockers:
        if not any("runtime" in r.lower() or "format" in r.lower() for r in reasons):
            reasons.append(f"Short film format compliant ({film_runtime} min)")
        if not is_recommended:
            is_recommended = True

    # 4. Student Status
    is_student_cat = bool(re.search(r"\b(student|film [ée]tudiant|university|college|lycée|école|school)\b", full_text))
    if is_student_cat:
        if is_student:
            reasons.append("🎓 Student status verified: eligible for student rate")
            is_recommended = True
        else:
            blockers.append("Category reserved for currently enrolled students")

    # 5. First-Time Filmmaker
    is_first_time_cat = bool(re.search(r"\b(first[\s\-]?time|debut|premi[èe]re [oœ]uvre|premier film)\b", full_text))
    if is_first_time_cat:
        if is_first_time:
            reasons.append("🌟 Eligible for First-Time Filmmaker / Debut competition")
            is_recommended = True
        else:
            blockers.append("Reserved for debut / first-time directors")

    # 6. Youth Filmmakers
    is_youth_cat = bool(re.search(r"\b(youth|teen|teenager|jeunesse|under 18|scolaire)\b", full_text))
    if is_youth_cat:
        if age_group == "youth":
            reasons.append("🎒 Eligible for Youth Filmmaker competition (< 18)")
            is_recommended = True
        else:
            blockers.append("Reserved for youth / minor directors (< 18)")

    # 7. AI Category
    is_ai_cat = bool(re.search(r"\b(ai|artificial intelligence|intelligence artificielle|ia|generative)\b", full_text))
    if is_ai_cat:
        if ai_level in ("hybrid", "full", "generative_full"):
            reasons.append("🤖 Dedicated AI / Generative category compatible with your creative workflow")
            is_recommended = True
        else:
            blockers.append("Reserved for works created using Artificial Intelligence")

    # 8. Demographics
    is_female_cat = bool(re.search(r"\b(women|female|femme|réalisatrice)\b", full_text))
    if is_female_cat:
        if "female_filmmaker" in demographics:
            reasons.append("👩 Female Filmmakers competition")
            is_recommended = True
        else:
            blockers.append("Category reserved for female directors")

    is_lgbtq_cat = bool(re.search(r"\b(lgbt|lgbtq|queer|gay|lesbian)\b", full_text))
    if is_lgbtq_cat:
        if "lgbtq" in demographics:
            reasons.append("🏳️‍🌈 LGBTQ+ / Queer competition")
            is_recommended = True
        else:
            blockers.append("Category reserved for LGBTQ+ themes or creators")

    # Status determination
    if blockers:
        status = "ineligible"
        is_eligible = False
        badge = "🔴 Ineligible for your film"
        badge_color = "rose"
    elif is_recommended:
        status = "recommended"
        is_eligible = True
        badge = "🟢 Highly Recommended for your film"
        badge_color = "emerald"
    else:
        status = "eligible"
        is_eligible = True
        badge = "🔵 Eligible"
        badge_color = "blue"

    return {
        "status": status,
        "is_eligible": is_eligible,
        "is_recommended": is_recommended,
        "badge": badge,
        "badge_color": badge_color,
        "reasons": reasons,
        "blockers": blockers,
        "name": cat_name,
        "description": cat_desc,
        "deadline_type": cat.get("deadline_type"),
        "fees": cat.get("fees", {})
    }


def evaluate_all_categories(categories, profile):
    """Évalue toutes les catégories d'un festival et les ordonne : recommandées d'abord, puis éligibles, puis inéligibles."""
    evaluated = []
    for cat in categories:
        evaluated.append(evaluate_category_eligibility(cat, profile))
    
    # Tri : Recommandé d'abord, puis éligible, puis inéligible
    status_order = {"recommended": 0, "eligible": 1, "ineligible": 2}
    evaluated.sort(key=lambda x: status_order.get(x["status"], 99))
    return evaluated



def _has_upcoming_deadline(dates, not_before):
    if not dates:
        return None  # inconnu, pas "non"
    for d in dates:
        label = d.get("label", "")
        if "Deadline" not in label:
            continue
        parsed = _try_parse_date(d.get("date", ""))
        if parsed and parsed >= not_before:
            return True
    return False


def _try_parse_date(text):
    text = text.split("–")[0].split("-")[0].strip()
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.date.fromisoformat(
                datetime.datetime.strptime(text, fmt).date().isoformat()
            )
        except ValueError:
            continue
    return None


def trust_score(festival_row):
    """Heuristique simple de fiabilité (PAS une preuve, juste un signal) :
    plus d'années d'existence + plus d'avis + labellisé FFA/Academy = plus fiable."""
    years = _years_running_to_int(festival_row.get("listing_years_running"))
    reviews = festival_row.get("listing_reviews") or 0
    detail = festival_row.get("detail_json") or {}
    text_blob = json.dumps(detail).lower()
    labeled = any(k in text_blob for k in ["film festival alliance", "academy award", "qualifying"])

    score = 0.0
    score += min(years / 10, 0.4)          # jusqu'à 0.4 pour l'ancienneté
    score += min(reviews / 20, 0.3)        # jusqu'à 0.3 pour les avis
    score += 0.3 if labeled else 0.0       # 0.3 si labellisé
    return round(min(score, 1.0), 2)


def _get_next_deadline(dates, not_before):
    upcoming = []
    for d in dates:
        label = d.get("label", "")
        parsed = _try_parse_date(d.get("date", ""))
        if parsed and parsed >= not_before:
            upcoming.append((parsed, label, d.get("date", "")))
    if not upcoming:
        return None, None
    upcoming.sort(key=lambda x: x[0])
    return upcoming[0][1], upcoming[0][2]


def match_festivals(all_rows, profile):
    dnb_val = profile.get("deadline_not_before")
    not_before = datetime.date.fromisoformat(dnb_val) if dnb_val else datetime.date.today()
    results = []

    film_runtime = profile.get("runtime_minutes", 15)
    ai_level = profile.get("ai_integration_level", "none")
    online_status = profile.get("online_availability", "private")
    film_premiere = profile.get("premiere_status_available", "world")
    film_completion = profile.get("completion_date")
    is_student = profile.get("is_student", False)
    is_first_time = profile.get("is_first_time", False)
    age_group = profile.get("director_age_group", "adult")
    spoken_lang = (profile.get("spoken_language") or "").strip().lower()
    subtitles = [str(s).lower() for s in (profile.get("subtitles") or [])]
    country_origin = (profile.get("country_of_origin") or "").strip().lower()
    country_filming = (profile.get("country_of_filming") or "").strip().lower()
    target_events = profile.get("target_event_types") or []
    demographics = profile.get("demographics") or []
    wanted_genres = profile.get("genres") or profile.get("categories") or ["Short"]
    project_type = (profile.get("project_type") or "fiction").lower()
    max_budget = profile.get("max_fee_usd", 25.0)

    for row in all_rows:
        detail = row.get("detail_json") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        
        constraints = detail.get("constraints") or {}
        categories = detail.get("categories", [])
        dates = detail.get("dates", [])
        rules_str = detail.get("rules") or ""
        desc_str = detail.get("description") or ""
        raw_text = row.get("raw_text") or ""
        rules_text = (rules_str or "") + " " + (desc_str or "")

        fest_name = row.get("name") or detail.get("name") or row.get("slug") or ""
        slug = row.get("slug") or ""

        if not constraints and (rules_text.strip() or raw_text.strip() or categories):
            constraints = extractor.extract_festival_constraints(
                full_text=raw_text,
                categories=categories,
                rules=rules_str,
                description=desc_str,
                name=fest_name,
                slug=slug
            )

        rules_lower = rules_text.lower()
        cat_names_lower = " ".join(c.get("name", "").lower() for c in categories)

        blockers = []
        warnings = []
        positive_reasons = []

        # -------------------------------------------------------------
        # 1. CRITICAL ELIGIBILITY (Blockers)
        # -------------------------------------------------------------
        # A. Runtime Check
        max_short = constraints.get("max_short_runtime_mins")
        if max_short and film_runtime > max_short:
            blockers.append(f"Runtime exceeded: {film_runtime} min (Max allowed: {max_short} min)")
        else:
            positive_reasons.append(f"Runtime compatible ({film_runtime} min)")

        # B. AI Policy Check
        ai_banned = constraints.get("ai_banned", False)
        has_ai_category = constraints.get("has_ai_category", False)

        is_heavy_ai = ai_level in ("hybrid", "full", "generative_full")
        is_full_ai = ai_level in ("full", "generative_full")

        if is_heavy_ai and ai_banned:
            blockers.append("AI-generated content is strictly prohibited by this festival")
        elif is_full_ai and not has_ai_category:
            warnings.append("100% AI Project: no explicit AI category found, verify festival policy")
        elif is_heavy_ai and has_ai_category:
            positive_reasons.append("Dedicated AI / Generative category available")
        elif ai_level in ("none", "assistive"):
            positive_reasons.append("Traditional production (100% eligible without AI constraints)")

        # C. Online Screening / Public availability
        public_banned = constraints.get("public_online_banned", False)
        if online_status == "public" and public_banned:
            blockers.append("Publicly available on the Internet: strictly prohibited by festival rules")
        elif online_status == "private":
            positive_reasons.append("Private screener with password protection (complies with exclusivity)")

        # D. Premiere Status Requirement
        fest_premiere_req = constraints.get("premiere_required", "none")
        if fest_premiere_req == "world" and film_premiere not in ("world",):
            blockers.append("World Premiere requirement not met")
        elif fest_premiere_req == "national" and film_premiere in ("none",):
            blockers.append("National Premiere requirement not met")
        elif fest_premiere_req == "none":
            positive_reasons.append("No strict Premiere requirement")
        else:
            positive_reasons.append(f"Compatible Premiere status ({film_premiere.title()})")

        # E. Completion Date Cutoff
        min_comp_year = constraints.get("min_completion_year")
        if min_comp_year and film_completion:
            try:
                film_year = int(film_completion.split("-")[0])
                if film_year < min_comp_year:
                    blockers.append(f"Completed in {film_year} (Festival requires production after {min_comp_year})")
                else:
                    positive_reasons.append(f"Completion date compliant (>= {min_comp_year})")
            except Exception:
                pass

        # F. Project Type Exclusivity (Blockers)
        has_general_fiction = any(k in cat_names_lower for k in ["fiction", "narrative", "drama", "comedy", "live action"])
        is_doc_only = constraints.get("is_doc_only") or bool("documentary film festival" in fest_name.lower() or ("docs" in (fest_name.lower() + " " + slug.lower()) and not has_general_fiction and "documentary" in rules_lower))
        is_animation_only = constraints.get("is_animation_only") or bool("animation film festival" in fest_name.lower())
        is_fiction_only = constraints.get("is_fiction_only")

        if is_doc_only and project_type not in ("documentary", "docu"):
            blockers.append(f"Festival exclusively dedicated to Documentaries (your project is '{project_type.title()}')")
        elif is_animation_only and project_type not in ("animation", "anime"):
            blockers.append(f"Festival exclusively dedicated to Animation (your project is '{project_type.title()}')")
        elif is_fiction_only and project_type in ("documentary", "docu"):
            blockers.append("Festival exclusively dedicated to Fiction (your project is a Documentary)")

        # G. Age Group / Adult-Only Restriction
        has_youth_cat = constraints.get("has_youth_category", False) or "youth" in cat_names_lower or "teen" in cat_names_lower
        if age_group == "youth":
            if constraints.get("adult_only_restriction"):
                blockers.append("Festival restricted to adult filmmakers (18+ only)")
            elif has_youth_cat:
                positive_reasons.append("🎒 Youth / Student category (< 18) available")

        # -------------------------------------------------------------
        # 2. CATEGORY MATCH & BONUSES
        # -------------------------------------------------------------
        # Language & English Subtitles
        if spoken_lang and spoken_lang not in ("english", "anglais"):
            has_eng_sub = any("english" in s or "anglais" in s for s in subtitles)
            if has_eng_sub:
                positive_reasons.append("English subtitles available (essential for international circuit)")
            else:
                warnings.append("Non-English film without English subtitles: risk of disqualification")

        # Geographic Match / Focus
        fest_location = (row.get("listing_location") or detail.get("location") or "").lower()
        searchable_geo = f"{fest_location} {fest_name.lower()} {slug.lower()} {cat_names_lower} {rules_lower}"
        geo_match = False
        for c_target in [country_origin, country_filming]:
            if c_target and len(c_target) >= 3:
                aliases = COUNTRY_ALIASES.get(c_target, [c_target])
                if any(alias in searchable_geo for alias in aliases):
                    geo_match = True
                    positive_reasons.append(f"📍 Located in target region or regional focus ({c_target.title()})")
                    break

        # Student Status
        has_student_cat = constraints.get("has_student_category", False) or "student" in cat_names_lower
        if is_student and has_student_cat:
            positive_reasons.append("🎓 Student category & discounts available")

        # First-Time Filmmaker
        has_debut_cat = constraints.get("has_first_time_category", False) or any(k in cat_names_lower for k in ["first-time", "debut", "first film"])
        if is_first_time and has_debut_cat:
            positive_reasons.append("🌟 Debut / First-Time Filmmaker competition open")

        # Demographics (Female, LGBTQ+, etc.)
        if "female_filmmaker" in demographics and (constraints.get("has_female_category") or "female" in cat_names_lower or "women" in cat_names_lower):
            positive_reasons.append("👩 Female Filmmakers competition")
        if "lgbtq" in demographics and (constraints.get("has_lgbtq_category") or "lgbt" in cat_names_lower or "queer" in cat_names_lower):
            positive_reasons.append("🏳️‍🌈 LGBTQ+ / Queer competition")

        # Project Type & Genres match
        cat_score, matched_cats = _category_score(categories, wanted_genres)
        if matched_cats:
            positive_reasons.append(f"Matching genres: {', '.join(matched_cats)}")

        if project_type in cat_names_lower or project_type in rules_lower:
            positive_reasons.append(f"Accepted project type ({project_type.title()})")

        # Fees & Budget
        fee = _min_fee(categories)
        fee_ok = (fee is None) or (max_budget is None) or (fee <= max_budget)
        if fee_ok:
            if fee is not None and fee > 0:
                positive_reasons.append(f"Within budget (${fee:.2f} <= ${max_budget:.0f})")
            elif fee == 0:
                positive_reasons.append("100% Free Submission ($0)")
            else:
                positive_reasons.append("Free or unspecified fee")
        else:
            warnings.append(f"Entry fee exceeds budget (${fee:.2f} > ${max_budget:.0f})")

        # Deadlines
        deadline_ok = _has_upcoming_deadline(dates, not_before)
        next_dl_label, next_dl_date = _get_next_deadline(dates, not_before)
        if deadline_ok:
            positive_reasons.append(f"Submissions open (next: {next_dl_date or 'soon'})")
        elif deadline_ok is False:
            warnings.append("Submissions currently closed or deadlines passed")

        # Accreditations list
        accreditations = []
        if constraints.get("is_oscar_qualifying"):
            accreditations.append("Oscar Qualifying")
            positive_reasons.append("🏆 Academy Award® Qualifying (Oscars)")
        if constraints.get("is_bafta_qualifying"):
            accreditations.append("BAFTA Qualifying")
            positive_reasons.append("🇬🇧 BAFTA Qualifying")
        if constraints.get("is_melies_qualifying"):
            accreditations.append("Méliès d'Or")
            positive_reasons.append("🌟 Méliès d'Or")
        if constraints.get("is_ffa_member"):
            accreditations.append("Film Festival Alliance")
            positive_reasons.append("🛡️ Film Festival Alliance Member")

        # Event Type
        if constraints.get("is_live_screening"):
            positive_reasons.append("🍿 In-person theatrical screening (Live)")
        if "live_screening" in target_events and constraints.get("is_contest_only"):
            warnings.append("Online contest / script competition without in-person theatrical screening")

        # -------------------------------------------------------------
        # 3. SCORE CALCULATION
        # -------------------------------------------------------------
        t_score = trust_score(row)
        is_disqualified = len(blockers) > 0

        if is_disqualified:
            overall_score = 0.08
            match_quality = "Ineligible (Disqualifying Blocker)"
            pct = 8
        else:
            score_pts = 0.0
            # 1. Artistic & Category (30 pts)
            score_pts += cat_score * 30.0

            # 2. Rules & Technical (25 pts)
            score_pts += 25.0
            if not fee_ok:
                score_pts -= 15.0

            # 3. Deadlines & Openness (20 pts)
            if deadline_ok:
                score_pts += 20.0
            elif deadline_ok is None:
                score_pts += 10.0
            else:
                score_pts += 2.0

            # 4. Prestige & Trust (15 pts)
            score_pts += t_score * 15.0

            # 5. Bonuses (up to 15 pts)
            if is_student and has_student_cat:
                score_pts += 3.0
            if is_first_time and has_debut_cat:
                score_pts += 3.0
            if age_group == "youth" and has_youth_cat:
                score_pts += 4.0
            if geo_match:
                score_pts += 6.0
            if accreditations:
                score_pts += 4.0

            score_pts = min(100.0, max(12.0, score_pts))
            pct = int(round(score_pts))
            overall_score = round(score_pts / 100.0, 3)

            if pct >= 85:
                match_quality = "Ideal Match (High Fit)"
            elif pct >= 70:
                match_quality = "Strong Match"
            elif pct >= 50:
                match_quality = "Partial / Secondary Match"
            else:
                match_quality = "Low Compatibility"

            evaluated_cats = evaluate_all_categories(categories, profile)
            rec_count = sum(1 for c in evaluated_cats if c.get("is_recommended"))
            elig_count = sum(1 for c in evaluated_cats if c.get("is_eligible"))

            results.append({
                "slug": row["slug"],
                "name": row.get("name") or detail.get("name") or row["slug"],
                "url": row.get("url"),
                "score": overall_score,
                "match_pct": pct,
                "match_quality": match_quality,
                "is_eligible": not is_disqualified,
                "blockers": blockers,
                "warnings": warnings,
                "why_matched": positive_reasons,
                "matched_categories": matched_cats,
                "evaluated_categories": evaluated_cats,
                "recommended_categories_count": rec_count,
                "eligible_categories_count": elig_count,
                "min_fee_usd": fee,
                "fee_within_budget": fee_ok,
                "has_upcoming_deadline": deadline_ok,
                "next_deadline_label": next_dl_label,
                "next_deadline_date": next_dl_date,
                "trust_score": t_score,
                "accreditations": accreditations,
                "has_student_discount": has_student_cat,
                "has_first_time_category": has_debut_cat,
                "is_live_screening": constraints.get("is_live_screening", False),
                "location": row.get("listing_location") or detail.get("location") or "",
                "years_running": row.get("listing_years_running") or detail.get("years_running") or "",
                "reviews_count": row.get("listing_reviews") or 0,
                "needs_review": bool(row.get("needs_review")),
                "review_reason": row.get("review_reason"),
            })

    # Sort: Eligible first by score, then disqualified
    results.sort(key=lambda r: (r["is_eligible"], r["score"]), reverse=True)
    return results

