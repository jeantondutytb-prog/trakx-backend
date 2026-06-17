"""
Trakr — Scheduler global
Scrape tout Vinted (newest_first, sans filtre) et s'arrête
dès qu'on retombe sur des annonces déjà connues.
Intervalle : 20 minutes par défaut.
"""
import asyncio, httpx, logging, os
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

log_path = Path("/tmp/trakr.log") if not (Path.home() / "Downloads").exists() else Path.home() / "Downloads" / "trakr.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(log_path)]
)
log = logging.getLogger("scheduler")

BASE     = "https://www.vinted.fr"
UA       = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
INTERVAL = int(os.getenv("SCAN_INTERVAL", "1200"))  # 20 minutes
MAX_PAGES = 15   # max pages par scan (15 × 96 = ~1 440 annonces max)
PER_PAGE  = 96   # max autorisé par Vinted


def parse_item(item):
    try:
        prix_obj = item.get("price", {})
        prix = float(prix_obj.get("amount", prix_obj)) if isinstance(prix_obj, dict) else float(prix_obj)
        if prix <= 0:
            return None
        return {
            "id":         int(item["id"]),
            "titre":      item.get("title", ""),
            "marque":     item.get("brand_title", "") or "Sans marque",
            "taille":     item.get("size_title", ""),
            "prix":       prix,
            "nb_favoris": int(item.get("favourite_count", 0)),
            "url":        BASE + item.get("path", ""),
            "photo":      (item.get("photo") or {}).get("url", ""),
            "vendeur":    (item.get("user") or {}).get("login", ""),
            "categorie":  item.get("catalog_title", ""),
        }
    except Exception:
        return None


async def get_cookies():
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as s:
        await s.get(BASE, headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
        await asyncio.sleep(2)
        return "; ".join(f"{k}={v}" for k, v in dict(s.cookies).items()), s.cookies


def ids_already_in_db(ids: list[int]) -> set[int]:
    """Retourne les IDs déjà présents en base."""
    from database import get_conn
    conn, mode = get_conn()
    if not ids:
        return set()
    try:
        if mode == "pg":
            placeholders = ",".join(f":id{i}" for i in range(len(ids)))
            kwargs = {f"id{i}": v for i, v in enumerate(ids)}
            rows = conn.run(f"SELECT id FROM annonces WHERE id IN ({placeholders})", **kwargs)
            return {r[0] for r in rows}
        else:
            placeholders = ",".join(["?" for _ in ids])
            rows = conn.execute(f"SELECT id FROM annonces WHERE id IN ({placeholders})", ids).fetchall()
            return {r[0] for r in rows}
    except Exception as e:
        log.error(f"Erreur ids_already_in_db: {e}")
        return set()


async def run_scan_with_session(s, cookies_str):
    from database import sauvegarder_annonces, stats_db
    log.info("=== Démarrage scan global Vinted ===")

    toutes = []
    stop = False

    for page in range(1, MAX_PAGES + 1):
        if stop:
            break
        try:
            r = await s.get(
                BASE + "/api/v2/catalog/items",
                params={"order": "newest_first", "per_page": PER_PAGE, "page": page},
                headers={
                    "User-Agent": UA, "Accept": "application/json",
                    "Cookie": cookies_str, "Referer": BASE, "Accept-Language": "fr-FR,fr;q=0.9",
                },
                timeout=15,
            )
        except Exception as e:
            log.error(f"Page {page}: {e}")
            break

        if r.status_code == 429:
            log.warning("Rate limit Vinted — pause 60s puis abandon du scan")
            await asyncio.sleep(60)
            break
        if r.status_code != 200:
            log.warning(f"Page {page}: HTTP {r.status_code}")
            break

        items = r.json().get("items", [])
        if not items:
            log.info(f"Page {page}: vide, on s'arrête")
            break

        page_annonces = [a for a in (parse_item(i) for i in items) if a]
        page_ids = [a["id"] for a in page_annonces]
        known_ids = ids_already_in_db(page_ids)
        new_on_page = [a for a in page_annonces if a["id"] not in known_ids]
        toutes.extend(new_on_page)

        ratio_connu = len(known_ids) / max(len(page_ids), 1)
        log.info(f"Page {page}: {len(items)} items | {len(new_on_page)} nouveaux | {len(known_ids)} déjà connus")

        if ratio_connu > 0.5:
            log.info("Majorité d'annonces déjà connues — scan terminé")
            stop = True

        await asyncio.sleep(2)

    if toutes:
        nouvelles = sauvegarder_annonces(toutes)
        stats = stats_db()
        log.info(f"Scan terminé — {nouvelles} nouvelles annonces | DB: {stats['annonces']} total")
    else:
        log.info("Scan terminé — aucune nouvelle annonce")


async def run_niche_scans(s, cookies_str):
    """Scan each active niche for new listings."""
    from urllib.parse import urlparse, parse_qs
    from database import get_active_niches, upsert_niche_items, mark_niche_items_sold

    niches = get_active_niches()
    if not niches:
        return
    log.info(f"Niches: {len(niches)} à scanner")

    for niche in niches:
        lien = niche.get("lien", "")
        niche_id = niche["id"]
        if not lien:
            continue
        try:
            parsed = urlparse(lien if lien.startswith("http") else f"https://{lien}")
            qs = parse_qs(parsed.query, keep_blank_values=False)

            api_params = {"order": "newest_first", "per_page": PER_PAGE, "page": 1}
            # Pass through all catalog filter params as-is
            for key in qs:
                val = qs[key]
                api_params[key] = val[0] if len(val) == 1 else val

            r = await s.get(
                BASE + "/api/v2/catalog/items",
                params=api_params,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Cookie": cookies_str,
                    "Referer": BASE,
                    "Accept-Language": "fr-FR,fr;q=0.9",
                },
                timeout=15,
            )
            if r.status_code == 429:
                log.warning(f"Niche {niche_id}: rate limit, skip")
                await asyncio.sleep(30)
                continue
            if r.status_code != 200:
                log.warning(f"Niche {niche_id}: HTTP {r.status_code}")
                continue

            items = r.json().get("items", [])
            annonces = [a for a in (parse_item(i) for i in items) if a]
            if annonces:
                upsert_niche_items(niche_id, annonces)
                seen_ids = [a["id"] for a in annonces]
                mark_niche_items_sold(niche_id, seen_ids, INTERVAL)
                log.info(f"Niche {niche_id} '{niche['nom']}': {len(annonces)} items | {len(seen_ids)} vus")
        except Exception as e:
            log.error(f"Niche {niche_id}: {e}")
        await asyncio.sleep(3)


async def main():
    from database import init_db
    init_db()
    log.info(f"Scheduler démarré — scan toutes les {INTERVAL//60} min | max {MAX_PAGES} pages × {PER_PAGE} items")

    scan_count = 0
    while True:
        scan_count += 1
        log.info(f"--- Scan #{scan_count} ---")
        try:
            cookies_str, jar = await get_cookies()
            async with httpx.AsyncClient(follow_redirects=True, timeout=20, cookies=jar) as s:
                await run_scan_with_session(s, cookies_str)
                await run_niche_scans(s, cookies_str)
        except Exception as e:
            log.error(f"Erreur scan #{scan_count}: {e}")
        log.info(f"Prochain scan dans {INTERVAL//60} min")
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Scheduler arrêté.")
