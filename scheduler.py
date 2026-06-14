"""
VintedSpy — Scheduler
Tourne en boucle, scrape toutes les 5 minutes, stocke et score.
Lance avec : python3 scheduler.py
"""
import asyncio, httpx, json, logging
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

log_path = Path("/tmp/vintedspy.log") if not (Path.home() / "Downloads").exists() else Path.home() / "Downloads" / "vintedspy.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_path)
    ]
)
log = logging.getLogger("scheduler")

BASE = "https://www.vinted.fr"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
INTERVAL = 300  # secondes entre chaque scan (5 min)

NICHES = [
    "levi 501",
    "nike air force 1",
    "north face",
    "carhartt",
]

async def scraper_niche(session, search, cookies_str):
    try:
        r = await session.get(
            BASE + "/api/v2/catalog/items",
            params={"search_text": search, "order": "newest_first", "per_page": 20, "page": 1},
            headers={"User-Agent": UA, "Accept": "application/json",
                     "Cookie": cookies_str, "Referer": BASE},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        items = r.json().get("items", [])
        annonces = []
        for item in items:
            try:
                prix_obj = item.get("price", {})
                prix = float(prix_obj.get("amount", prix_obj)) if isinstance(prix_obj, dict) else float(prix_obj)
                annonces.append({
                    "id": int(item["id"]),
                    "titre": item.get("title", ""),
                    "marque": item.get("brand_title", ""),
                    "taille": item.get("size_title", ""),
                    "prix": prix,
                    "nb_favoris": int(item.get("favourite_count", 0)),
                    "url": BASE + item.get("path", ""),
                    "photo": (item.get("photo") or {}).get("url", ""),
                    "vendeur": (item.get("user") or {}).get("login", ""),
                })
            except:
                pass
        return annonces
    except Exception as e:
        log.error(f"Erreur scrape '{search}': {e}")
        return []

async def get_cookies():
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as s:
        await s.get(BASE, headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
        await asyncio.sleep(2)
        return "; ".join(f"{k}={v}" for k, v in dict(s.cookies).items()), s.cookies

async def run_scan():
    from database import init_db, sauvegarder_annonces, get_opportunites, stats_db
    log.info("=== Démarrage scan ===")

    try:
        cookies_str, jar = await get_cookies()
    except Exception as e:
        log.error(f"Impossible d'obtenir les cookies: {e}")
        return

    async with httpx.AsyncClient(follow_redirects=True, timeout=20, cookies=jar) as s:
        toutes = []
        for niche in NICHES:
            annonces = await scraper_niche(s, niche, cookies_str)
            log.info(f"  '{niche}': {len(annonces)} annonces")
            toutes.extend(annonces)
            await asyncio.sleep(3)

    nouvelles = sauvegarder_annonces(toutes)
    stats = stats_db()
    opps = get_opportunites(10)

    log.info(f"Scan terminé — {nouvelles} nouvelles | DB: {stats['annonces']} annonces")
    log.info(f"Top opportunité: {opps[0]['titre'][:40]} | {opps[0]['prix']}€ vs {opps[0]['prix_median']}€ médiane | score {opps[0]['score']}" if opps else "Pas encore de scores")

    # Sauvegarder le top opportunités en JSON (l'API le lit)
    out = Path.home() / "Downloads" / "opportunites.json"
    with open(out, "w") as f:
        json.dump(opps, f, ensure_ascii=False, indent=2)

async def main():
    from database import init_db
    init_db()
    log.info(f"Scheduler démarré — scan toutes les {INTERVAL//60} minutes")
    log.info("Ctrl+C pour arrêter\n")

    scan_count = 0
    while True:
        scan_count += 1
        log.info(f"--- Scan #{scan_count} ---")
        try:
            await run_scan()
        except Exception as e:
            log.error(f"Erreur scan #{scan_count}: {e}")

        next_scan = datetime.now().strftime('%H:%M:%S')
        log.info(f"Prochain scan dans {INTERVAL//60} min...")
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Scheduler arrêté.")
