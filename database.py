"""
VintedSpy — Database avec connection pooling
"""
import os, statistics, logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("database")
DATABASE_URL = os.getenv("DATABASE_URL")
_conn_cache = None

def get_conn():
    global _conn_cache
    if DATABASE_URL:
        import pg8000.native
        # Réutiliser la connexion existante si possible
        try:
            if _conn_cache is not None:
                _conn_cache.run("SELECT 1")
                return _conn_cache, "pg"
        except:
            _conn_cache = None

        url = DATABASE_URL.replace("postgresql://","").replace("postgres://","")
        user_pass, rest = url.split("@", 1)
        user, password = user_pass.split(":", 1)
        host_db = rest.split("/", 1)
        host_port = host_db[0]
        db = host_db[1].split("?")[0]
        port = 5432
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host = host_port

        _conn_cache = pg8000.native.Connection(
            user=user, password=password, host=host,
            port=port, database=db, ssl_context=True
        )
        return _conn_cache, "pg"
    else:
        import sqlite3
        db_path = Path.home() / "Downloads" / "vintedspy.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def init_db():
    conn, mode = get_conn()
    if mode == "pg":
        conn.run("""CREATE TABLE IF NOT EXISTS annonces (
            id BIGINT PRIMARY KEY, titre TEXT, marque TEXT, taille TEXT,
            prix REAL, nb_favoris INTEGER, url TEXT, photo TEXT, vendeur TEXT, scraped_le TEXT)""")
        conn.run("""CREATE TABLE IF NOT EXISTS prix_history (
            id BIGSERIAL PRIMARY KEY, marque TEXT, taille TEXT, prix REAL, scraped_le TEXT)""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_marque_taille ON prix_history(marque, taille)")
        except: pass
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS annonces (
                id INTEGER PRIMARY KEY, titre TEXT, marque TEXT, taille TEXT,
                prix REAL, nb_favoris INTEGER, url TEXT, photo TEXT, vendeur TEXT, scraped_le TEXT);
            CREATE TABLE IF NOT EXISTS prix_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, marque TEXT, taille TEXT, prix REAL, scraped_le TEXT);
            CREATE INDEX IF NOT EXISTS idx_marque_taille ON prix_history(marque, taille);
        """)
        conn.commit()
    log.info(f"DB initialisée ({mode})")

def sauvegarder_annonces(annonces: list[dict]) -> int:
    conn, mode = get_conn()
    nouvelles = 0
    now = datetime.now().isoformat()
    for a in annonces:
        try:
            if mode == "pg":
                conn.run("""INSERT INTO annonces (id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le)
                    VALUES (:id,:titre,:marque,:taille,:prix,:nb_favoris,:url,:photo,:vendeur,:now)
                    ON CONFLICT (id) DO NOTHING""",
                    id=a["id"],titre=a["titre"],marque=a["marque"],taille=a["taille"],
                    prix=a["prix"],nb_favoris=a["nb_favoris"],url=a["url"],
                    photo=a.get("photo",""),vendeur=a.get("vendeur",""),now=now)
                if conn.row_count > 0:
                    nouvelles += 1
                    conn.run("INSERT INTO prix_history (marque,taille,prix,scraped_le) VALUES (:m,:t,:p,:n)",
                        m=a["marque"],t=a["taille"],p=a["prix"],n=now)
            else:
                conn.execute("""INSERT OR IGNORE INTO annonces (id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (a["id"],a["titre"],a["marque"],a["taille"],a["prix"],
                     a["nb_favoris"],a["url"],a.get("photo",""),a.get("vendeur",""),now))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    nouvelles += 1
                    conn.execute("INSERT INTO prix_history (marque,taille,prix,scraped_le) VALUES (?,?,?,?)",
                        (a["marque"],a["taille"],a["prix"],now))
                conn.commit()
        except Exception as e:
            log.error(f"Erreur insertion {a.get('id')}: {e}")
    return nouvelles

def get_median_prix(marque: str, taille: str, jours: int = 90):
    conn, mode = get_conn()
    since = (datetime.now() - timedelta(days=jours)).isoformat()
    if mode == "pg":
        rows = conn.run("SELECT prix FROM prix_history WHERE marque=:m AND taille=:t AND scraped_le>:s ORDER BY scraped_le DESC LIMIT 200",
            m=marque, t=taille, s=since)
        prices = [r[0] for r in rows]
    else:
        rows = conn.execute("SELECT prix FROM prix_history WHERE marque=? AND taille=? AND scraped_le>? ORDER BY scraped_le DESC LIMIT 200",
            (marque, taille, since)).fetchall()
        prices = [r[0] for r in rows]
    if len(prices) < 3:
        return None
    return round(statistics.median(prices), 2)

def scorer_annonce(annonce: dict, prix_median) -> int:
    if not prix_median or annonce["prix"] <= 0:
        return 0
    ecart = (prix_median - annonce["prix"]) / prix_median
    score_ecart = min(ecart * 1.5, 1.0) * 40
    score_favs = min(annonce["nb_favoris"] / 10, 1.0) * 30
    marge = prix_median - annonce["prix"]
    score_marge = min(marge / 30, 1.0) * 30
    return min(int(score_ecart + score_favs + score_marge), 100)

def get_opportunites(limit: int = 20) -> list[dict]:
    conn, mode = get_conn()
    if mode == "pg":
        rows = conn.run("SELECT id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le FROM annonces ORDER BY scraped_le DESC LIMIT 100")
        cols = ["id","titre","marque","taille","prix","nb_favoris","url","photo","vendeur","scraped_le"]
        annonces = [dict(zip(cols, r)) for r in rows]
    else:
        rows = conn.execute("SELECT * FROM annonces ORDER BY scraped_le DESC LIMIT 100").fetchall()
        annonces = [dict(r) for r in rows]

    resultats = []
    for a in annonces:
        median = get_median_prix(a["marque"], a["taille"])
        score = scorer_annonce(a, median)
        if score > 0:
            a["prix_median"] = median
            a["ecart_pct"] = round((median - a["prix"]) / median * 100) if median else 0
            a["marge_nette"] = round(median - a["prix"] - (a["prix"] * 0.05) - 0.70, 2) if median else 0
            a["score"] = score
            resultats.append(a)
    return sorted(resultats, key=lambda x: x["score"], reverse=True)[:limit]

def get_feed_annonces(offset: int = 0, limit: int = 40, marque: str = None,
                       prix_min: float = None, prix_max: float = None,
                       search: str = None, order: str = "recent") -> list[dict]:
    conn, mode = get_conn()
    order_clause = {
        "recent":    "scraped_le DESC",
        "prix_asc":  "prix ASC",
        "prix_desc": "prix DESC",
        "favs":      "nb_favoris DESC",
    }.get(order, "scraped_le DESC")

    if mode == "pg":
        conditions, kwargs = [], {}
        if marque:
            conditions.append("LOWER(marque) LIKE :marque")
            kwargs["marque"] = f"%{marque.lower()}%"
        if prix_min is not None:
            conditions.append("prix >= :prix_min")
            kwargs["prix_min"] = prix_min
        if prix_max is not None:
            conditions.append("prix <= :prix_max")
            kwargs["prix_max"] = prix_max
        if search:
            conditions.append("(LOWER(titre) LIKE :search OR LOWER(marque) LIKE :search2)")
            kwargs["search"] = f"%{search.lower()}%"
            kwargs["search2"] = f"%{search.lower()}%"
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        q = f"SELECT id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le FROM annonces {where} ORDER BY {order_clause} LIMIT :limit OFFSET :offset"
        kwargs.update({"limit": limit, "offset": offset})
        rows = conn.run(q, **kwargs)
        cols = ["id","titre","marque","taille","prix","nb_favoris","url","photo","vendeur","scraped_le"]
        return [dict(zip(cols, r)) for r in rows]
    else:
        conditions, params = [], []
        if marque:
            conditions.append("LOWER(marque) LIKE ?")
            params.append(f"%{marque.lower()}%")
        if prix_min is not None:
            conditions.append("prix >= ?")
            params.append(prix_min)
        if prix_max is not None:
            conditions.append("prix <= ?")
            params.append(prix_max)
        if search:
            conditions.append("(LOWER(titre) LIKE ? OR LOWER(marque) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        q = f"SELECT * FROM annonces {where} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def stats_db() -> dict:
    conn, mode = get_conn()
    if mode == "pg":
        nb_a = conn.run("SELECT COUNT(*) FROM annonces")[0][0]
        nb_p = conn.run("SELECT COUNT(*) FROM prix_history")[0][0]
        niches_rows = conn.run("SELECT marque, COUNT(*) as nb, AVG(prix) as prix_moy, MIN(prix) as prix_min, MAX(prix) as prix_max FROM prix_history GROUP BY marque ORDER BY nb DESC")
        niches = [{"marque": r[0], "nb": r[1], "prix_moy": r[2], "prix_min": r[3], "prix_max": r[4]} for r in niches_rows]
    else:
        nb_a = conn.execute("SELECT COUNT(*) FROM annonces").fetchone()[0]
        nb_p = conn.execute("SELECT COUNT(*) FROM prix_history").fetchone()[0]
        niches = [dict(r) for r in conn.execute("SELECT marque, COUNT(*) as nb, AVG(prix) as prix_moy, MIN(prix) as prix_min, MAX(prix) as prix_max FROM prix_history GROUP BY marque ORDER BY nb DESC").fetchall()]
    return {"annonces": nb_a, "prix_history": nb_p, "niches": niches}
