"""
VintedSpy — Scheduler
Scrape toutes les niches configurées toutes les X minutes.
"""
import asyncio, httpx, json, logging
from datetime import datetime
from pathlib import Path
import sys, os
sys.path.insert(0, str(Path(__file__).parent))

log_path = Path("/tmp/vintedspy.log") if not (Path.home() / "Downloads").exists() else Path.home() / "Downloads" / "vintedspy.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(log_path)]
)
log = logging.getLogger("scheduler")

BASE = "https://www.vinted.fr"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))

NICHES = [
    "a.f.vandevorst",
    "a.p.c.",
    "a.p.c. x carhartt",
    "a.p.c. x kanye west",
    "a.p.c. x nike",
    "a.p.c. x sacai",
    "adidas x missoni",
    "agent provocateur",
    "agnes b",
    "alaïa",
    "amazon",
    "amina muaddi",
    "ann demeulemeester",
    "apple",
    "arte",
    "asus",
    "ba&sh",
    "bally",
    "balmain",
    "balmain x h&m",
    "balzac paris",
    "barbour",
    "belles des pins",
    "bose",
    "bottega veneta",
    "braun",
    "burberry",
    "bylima",
    "calarena",
    "canon",
    "carolina herrera",
    "chanel",
    "chloé",
    "christian dior",
    "christian louboutin",
    "claris virot",
    "class roberto cavalli",
    "coach",
    "converse x missoni",
    "coperni",
    "coros",
    "courrèges",
    "crockett & jones",
    "currentbody",
    "demellier",
    "dior",
    "dji",
    "dolce & gabbana",
    "dr dennis gross",
    "dôen",
    "elisabetta franchi",
    "emilio pucci",
    "epson",
    "equi théme",
    "eres",
    "escada",
    "etro",
    "fendi",
    "freitag",
    "fujifilm",
    "garmin",
    "gerard darel",
    "giuseppe zanotti",
    "givenchy",
    "gopro",
    "gucci",
    "gucci x adidas",
    "gucci x balenciaga",
    "gucci x dapper dan",
    "gucci x disney",
    "gucci x doraemon",
    "gucci x mlb",
    "gucci x palace",
    "harris tweed",
    "hermès",
    "hit",
    "hit air",
    "insta360",
    "isabel marant",
    "isabel marant étoile",
    "jean paul gaultier",
    "jil sander",
    "jimmy choo x mugler",
    "john galliano",
    "jordan x the attico",
    "jérôme dreyfuss",
    "k-way",
    "kitchenaid",
    "kobo",
    "kujten",
    "l'agent by agent provocateur",
    "l'oréal",
    "lemaire",
    "livy",
    "livystone",
    "loewe",
    "louis vuitton",
    "louis vuitton x christian louboutin",
    "lpg",
    "lupo barcelona",
    "m missoni",
    "magda butrym",
    "maison margiela",
    "maje",
    "manolo blahnik",
    "maria de la orden",
    "marni",
    "max mara",
    "mcm",
    "miphai",
    "missoni",
    "missoni home",
    "missoni mare",
    "miu miu",
    "mm6 maison margiela",
    "momcozy",
    "montbell",
    "montblanc",
    "mugler",
    "mugler x h&m",
    "mulberry",
    "mulberry & grand",
    "mulberry secret",
    "mulberry street",
    "mulberry studios",
    "mulberry x acne studios",
    "naked wolfe",
    "new rock",
    "nikon",
    "nintendo",
    "nooance",
    "octobre editions",
    "olympus",
    "onyx",
    "orciani",
    "our legacy",
    "paco rabanne",
    "palm angels x missoni",
    "parajumpers",
    "philips",
    "pierre balmain",
    "pioneer",
    "plein sud",
    "prada",
    "proenza schouler",
    "proenza schouler white label",
    "puma x balmain",
    "rat & boa",
    "red wing shoes",
    "reebok x ba&sh",
    "reina olga",
    "remarkable",
    "renouard",
    "revitive",
    "richard orlinski",
    "roberta di camerino",
    "roberto cavalli",
    "roberto cavalli sport",
    "roberto cavalli x h&m",
    "s.t. dupont",
    "salvatore ferragamo",
    "samshield",
    "sandro",
    "scuffers",
    "see by chloé",
    "self-portrait",
    "shark",
    "shokz",
    "silk'n",
    "sima couture",
    "soeur",
    "sommer swim",
    "sonia rykiel",
    "spark",
    "stella mccartney",
    "supreme x emilio pucci",
    "suunto",
    "sézane",
    "the attico",
    "the frankie shop",
    "the north face x gucci",
    "therabody",
    "thermomix",
    "tory burch",
    "tumi",
    "vanessa bruno",
    "versace",
    "wandler",
    "zanellato",
    "zimmermann",
    "zoé lu",
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
    log.info(f"=== Scan de {len(NICHES)} niches ===")

    try:
        cookies_str, jar = await get_cookies()
    except Exception as e:
        log.error(f"Cookies: {e}")
        return

    async with httpx.AsyncClient(follow_redirects=True, timeout=20, cookies=jar) as s:
        toutes = []
        for niche in NICHES:
            annonces = await scraper_niche(s, niche, cookies_str)
            if annonces:
                log.info(f"  '{niche}': {len(annonces)} annonces")
            toutes.extend(annonces)
            await asyncio.sleep(2)

    nouvelles = sauvegarder_annonces(toutes)
    stats = stats_db()
    log.info(f"Scan terminé — {nouvelles} nouvelles | DB: {stats['annonces']} annonces")

async def main():
    from database import init_db
    init_db()
    log.info(f"Scheduler démarré — {len(NICHES)} niches — scan toutes les {INTERVAL//60} min")

    scan_count = 0
    while True:
        scan_count += 1
        log.info(f"--- Scan #{scan_count} ---")
        try:
            await run_scan()
        except Exception as e:
            log.error(f"Erreur scan #{scan_count}: {e}")
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Scheduler arrêté.")
