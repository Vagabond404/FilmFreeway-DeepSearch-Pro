#!/usr/bin/env python3
"""
main.py — Point d'entrée du système d'intelligence FilmFreeway.

COMMANDES

  # 1) Découvre les festivals pertinents (listing filtré) + scrape leur détail
  python -m filmfreeway_intel.main discover --query "documentary" --max-pages 5

  # 2) Affiche les festivals scrapés dont l'extraction est incertaine
  python -m filmfreeway_intel.main review

  # 3) Classe les festivals en base selon ton profil de film
  python -m filmfreeway_intel.main match --profile profile_example.json

Tout est stocké dans filmfreeway.db (SQLite) — relance "discover" plus tard,
seuls les festivals jamais scrapés ou dont le cache a expiré seront refetch.
"""

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from . import db
    from . import crawler
    from . import matcher
    from . import llm_extractor
except (ImportError, ValueError):
    import db
    import crawler
    import matcher
    import llm_extractor


def cmd_discover(args):
    conn = db.get_conn(args.db)
    extra_params = json.loads(args.extra_params) if args.extra_params else None
    crawler.crawl_listing(conn, query=args.query, max_pages=args.max_pages,
                           extra_params=extra_params, delay=args.delay)
    crawler.crawl_details(conn, max_festivals=args.max_details, delay=args.delay,
                           refresh_days=args.refresh_days)
    conn.close()


def cmd_review(args):
    conn = db.get_conn(args.db)
    queue = db.get_review_queue(conn)
    conn.close()
    if not queue:
        print("[✓] Rien à vérifier manuellement.")
        return
    print(f"{len(queue)} festival(s) à vérifier manuellement :\n")
    for item in queue:
        print(f"- {item['name'] or item['slug']} ({item['url']})")
        print(f"    confiance={item['confidence']}  raison: {item['review_reason']}\n")


def cmd_llm_review(args):
    conn = db.get_conn(args.db)
    llm_extractor.run_llm_review_pass(conn, model=args.model, ollama_url=args.ollama_url)
    conn.close()


def cmd_match(args):
    with open(args.profile, encoding="utf-8") as f:
        profile = json.load(f)
    conn = db.get_conn(args.db)
    rows = db.get_all_with_detail(conn)
    conn.close()

    results = matcher.match_festivals(rows, profile)
    top = results[: args.top]

    print(f"\nTop {len(top)} festivals pour ton profil :\n")
    for r in top:
        flag = " ⚠️ à vérifier" if r["needs_review"] else ""
        print(f"[{r['score']:.2f}] {r['name']}{flag}")
        print(f"    {r['url']}")
        print(f"    catégories matchées: {r['matched_categories'] or '—'} | "
              f"frais min: {r['min_fee_usd']} | deadline à venir: {r['has_upcoming_deadline']} | "
              f"confiance festival: {r['trust_score']}")
        print()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"[✓] Résultats complets écrits dans {args.output}")


def main():
    ap = argparse.ArgumentParser(description="Système d'intelligence FilmFreeway")
    ap.add_argument("--db", default="filmfreeway.db", help="Chemin du fichier SQLite")
    sub = ap.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="Scrape listing + détail des festivals pertinents")
    p_discover.add_argument("--query", help="Mot-clé de recherche (paramètre 'q')")
    p_discover.add_argument("--max-pages", type=int, default=5)
    p_discover.add_argument("--max-details", type=int, default=None,
                             help="Limite le nb de fiches détail scrapées ce run (utile pour tester)")
    p_discover.add_argument("--delay", type=float, default=1.5)
    p_discover.add_argument("--refresh-days", type=int, default=14,
                             help="Ne re-scrape pas le détail d'un festival avant N jours")
    p_discover.add_argument("--extra-params", help='JSON de params supplémentaires, ex: \'{"event_type":"film"}\'')
    p_discover.set_defaults(func=cmd_discover)

    p_review = sub.add_parser("review", help="Liste les festivals à vérifier manuellement")
    p_review.set_defaults(func=cmd_review)

    p_llm = sub.add_parser("llm-review", help="Retente l'extraction via un mini LLM local (Ollama) sur les cas ambigus")
    p_llm.add_argument("--model", default="qwen2.5:0.5b", help="Nom du modèle Ollama à utiliser")
    p_llm.add_argument("--ollama-url", default="http://localhost:11434/api/generate",
                        help="URL de l'API Ollama (change si le modèle tourne sur ton serveur Armbian)")
    p_llm.set_defaults(func=cmd_llm_review)

    p_match = sub.add_parser("match", help="Classe les festivals selon un profil de film")
    p_match.add_argument("--profile", required=True, help="Fichier JSON du profil")
    p_match.add_argument("--top", type=int, default=20)
    p_match.add_argument("--output", help="Fichier JSON de sortie (résultats complets)")
    p_match.set_defaults(func=cmd_match)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
