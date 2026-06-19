"""
Trakr — Database avec connection pooling
"""
import os, statistics, logging, threading
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("database")
DATABASE_URL = os.getenv("DATABASE_URL")
_thread_local = threading.local()

def get_conn():
    if DATABASE_URL:
        import pg8000.native
        conn = getattr(_thread_local, 'conn', None)
        try:
            if conn is not None:
                conn.run("SELECT 1")
                return conn, "pg"
        except Exception:
            _thread_local.conn = None

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

        conn = pg8000.native.Connection(
            user=user, password=password, host=host,
            port=port, database=db, ssl_context=True
        )
        _thread_local.conn = conn
        return conn, "pg"
    else:
        import sqlite3
        db_path = Path.home() / "Downloads" / "trakr.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def init_db():
    conn, mode = get_conn()
    if mode == "pg":
        conn.run("""CREATE TABLE IF NOT EXISTS annonces (
            id BIGINT PRIMARY KEY, titre TEXT, marque TEXT, taille TEXT,
            prix REAL, nb_favoris INTEGER, url TEXT, photo TEXT, vendeur TEXT, scraped_le TEXT, publie_le TEXT)""")
        try:
            conn.run("ALTER TABLE annonces ADD COLUMN IF NOT EXISTS publie_le TEXT")
        except: pass
        conn.run("""CREATE TABLE IF NOT EXISTS prix_history (
            id BIGSERIAL PRIMARY KEY, marque TEXT, taille TEXT, prix REAL, scraped_le TEXT)""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_marque_taille ON prix_history(marque, taille)")
        except: pass
        conn.run("""CREATE TABLE IF NOT EXISTS niches (
            id BIGSERIAL PRIMARY KEY, user_id TEXT NOT NULL, nom TEXT,
            marque TEXT, taille TEXT, score_min INTEGER, prix_min REAL,
            recherche TEXT, lien TEXT, created_le TEXT)""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_niches_user ON niches(user_id)")
        except: pass
        try:
            conn.run("ALTER TABLE niches ADD COLUMN IF NOT EXISTS recherche TEXT")
        except: pass
        try:
            conn.run("ALTER TABLE niches ADD COLUMN IF NOT EXISTS lien TEXT")
        except: pass
        conn.run("""CREATE TABLE IF NOT EXISTS niche_items (
            id BIGSERIAL PRIMARY KEY,
            niche_id BIGINT NOT NULL,
            vinted_id BIGINT NOT NULL,
            titre TEXT, prix REAL, photo TEXT, url TEXT, marque TEXT, taille TEXT,
            first_seen TEXT, last_seen TEXT, sold_at TEXT,
            UNIQUE(niche_id, vinted_id))""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_niche_items_niche ON niche_items(niche_id)")
        except: pass
        conn.run("""CREATE TABLE IF NOT EXISTS surveillance (
            id BIGSERIAL PRIMARY KEY, user_id TEXT NOT NULL, annonce_id BIGINT NOT NULL,
            titre TEXT, marque TEXT, taille TEXT, prix REAL, photo TEXT, url TEXT,
            added_le TEXT, last_seen_le TEXT, vendu BOOLEAN DEFAULT FALSE,
            UNIQUE(user_id, annonce_id))""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_surv_user ON surveillance(user_id)")
        except: pass
        conn.run("""CREATE TABLE IF NOT EXISTS subscriptions (
            id BIGSERIAL PRIMARY KEY,
            user_email TEXT NOT NULL UNIQUE,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            status TEXT NOT NULL DEFAULT 'inactive',
            current_period_end TEXT,
            plan TEXT DEFAULT 'starter',
            updated_le TEXT)""")
        try:
            conn.run("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'starter'")
        except: pass
        try:
            conn.run("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_expires_at TEXT")
        except: pass
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_sub_email ON subscriptions(user_email)")
        except: pass
        conn.run("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        # Performance indexes for /feed endpoint
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_annonces_scraped ON annonces(scraped_le DESC)")
        except: pass
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_annonces_prix ON annonces(prix)")
        except: pass
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_annonces_favs ON annonces(nb_favoris DESC)")
        except: pass
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_annonces_marque ON annonces(LOWER(marque))")
        except: pass
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS annonces (
                id INTEGER PRIMARY KEY, titre TEXT, marque TEXT, taille TEXT,
                prix REAL, nb_favoris INTEGER, url TEXT, photo TEXT, vendeur TEXT, scraped_le TEXT, publie_le TEXT);
            CREATE TABLE IF NOT EXISTS prix_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, marque TEXT, taille TEXT, prix REAL, scraped_le TEXT);
            CREATE INDEX IF NOT EXISTS idx_marque_taille ON prix_history(marque, taille);
            CREATE TABLE IF NOT EXISTS niches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, nom TEXT,
                marque TEXT, taille TEXT, score_min INTEGER, prix_min REAL, recherche TEXT, lien TEXT, created_le TEXT);
            CREATE INDEX IF NOT EXISTS idx_niches_user ON niches(user_id);
            CREATE TABLE IF NOT EXISTS niche_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche_id INTEGER NOT NULL, vinted_id INTEGER NOT NULL,
                titre TEXT, prix REAL, photo TEXT, url TEXT, marque TEXT, taille TEXT,
                first_seen TEXT, last_seen TEXT, sold_at TEXT,
                UNIQUE(niche_id, vinted_id));
            CREATE INDEX IF NOT EXISTS idx_niche_items_niche ON niche_items(niche_id);
            CREATE TABLE IF NOT EXISTS surveillance (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, annonce_id INTEGER NOT NULL,
                titre TEXT, marque TEXT, taille TEXT, prix REAL, photo TEXT, url TEXT,
                added_le TEXT, last_seen_le TEXT, vendu INTEGER DEFAULT 0,
                UNIQUE(user_id, annonce_id));
            CREATE INDEX IF NOT EXISTS idx_surv_user ON surveillance(user_id);
        """)
        conn.commit()
        try:
            conn.execute("ALTER TABLE niches ADD COLUMN recherche TEXT")
            conn.commit()
        except: pass
        try:
            conn.execute("ALTER TABLE niches ADD COLUMN lien TEXT")
            conn.commit()
        except: pass
        try:
            conn.execute("ALTER TABLE annonces ADD COLUMN publie_le TEXT")
            conn.commit()
        except: pass
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS niche_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche_id INTEGER NOT NULL, vinted_id INTEGER NOT NULL,
                titre TEXT, prix REAL, photo TEXT, url TEXT, marque TEXT, taille TEXT,
                first_seen TEXT, last_seen TEXT, sold_at TEXT,
                UNIQUE(niche_id, vinted_id))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_niche_items_niche ON niche_items(niche_id)")
            conn.commit()
        except: pass
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL UNIQUE,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                status TEXT NOT NULL DEFAULT 'inactive',
                current_period_end TEXT,
                updated_le TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_email ON subscriptions(user_email)")
            conn.commit()
        except: pass
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
        except: pass
        try:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN plan TEXT DEFAULT 'starter'")
            conn.commit()
        except: pass
    log.info(f"DB initialisée ({mode})")

def sauvegarder_annonces(annonces: list[dict]) -> int:
    conn, mode = get_conn()
    nouvelles = 0
    now = datetime.now().isoformat()
    for a in annonces:
        try:
            if mode == "pg":
                conn.run("""INSERT INTO annonces (id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le,publie_le)
                    VALUES (:id,:titre,:marque,:taille,:prix,:nb_favoris,:url,:photo,:vendeur,:now,:publie_le)
                    ON CONFLICT (id) DO NOTHING""",
                    id=a["id"],titre=a["titre"],marque=a["marque"],taille=a["taille"],
                    prix=a["prix"],nb_favoris=a["nb_favoris"],url=a["url"],
                    photo=a.get("photo",""),vendeur=a.get("vendeur",""),now=now,
                    publie_le=a.get("publie_le",""))
                if conn.row_count > 0:
                    nouvelles += 1
                    conn.run("INSERT INTO prix_history (marque,taille,prix,scraped_le) VALUES (:m,:t,:p,:n)",
                        m=a["marque"],t=a["taille"],p=a["prix"],n=now)
            else:
                conn.execute("""INSERT OR IGNORE INTO annonces (id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le,publie_le)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (a["id"],a["titre"],a["marque"],a["taille"],a["prix"],
                     a["nb_favoris"],a["url"],a.get("photo",""),a.get("vendeur",""),now,a.get("publie_le","")))
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
        rows = conn.run("SELECT id,titre,marque,taille,prix,nb_favoris,url,photo,vendeur,scraped_le,publie_le FROM annonces ORDER BY scraped_le DESC LIMIT 100")
        cols = ["id","titre","marque","taille","prix","nb_favoris","url","photo","vendeur","scraped_le","publie_le"]
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

def _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search, order_clause, limit, offset, since_ts=None, favs_min=None):
    cols = ["id","titre","marque","taille","prix","nb_favoris","url","photo","vendeur","scraped_le","publie_le"]
    if mode == "pg":
        conditions, kwargs = [], {}
        if marques:
            placeholders = ",".join(f":marque{i}" for i in range(len(marques)))
            conditions.append(f"LOWER(marque) IN ({placeholders})")
            for i, m in enumerate(marques):
                kwargs[f"marque{i}"] = m.lower()
        if taille:
            # Match "S" exactly AND Vinted composite format "S / 36 / 8"
            t = taille.lower()
            conditions.append("(LOWER(taille) = :taille OR LOWER(taille) LIKE :taille_prefix)")
            kwargs["taille"] = t
            kwargs["taille_prefix"] = t + " / %"
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
        if since_ts:
            conditions.append("scraped_le >= :since_ts")
            kwargs["since_ts"] = since_ts
        if favs_min is not None:
            conditions.append("nb_favoris >= :favs_min")
            kwargs["favs_min"] = favs_min
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        q = f"SELECT {','.join(cols)} FROM annonces {where} ORDER BY {order_clause} LIMIT :limit OFFSET :offset"
        kwargs.update({"limit": limit, "offset": offset})
        rows = conn.run(q, **kwargs)
        return [dict(zip(cols, r)) for r in rows]
    else:
        conditions, params = [], []
        if marques:
            placeholders = ",".join("?" for _ in marques)
            conditions.append(f"LOWER(marque) IN ({placeholders})")
            params.extend(m.lower() for m in marques)
        if taille:
            t = taille.lower()
            conditions.append("(LOWER(taille) = ? OR LOWER(taille) LIKE ?)")
            params.extend([t, t + " / %"])
        if prix_min is not None:
            conditions.append("prix >= ?")
            params.append(prix_min)
        if prix_max is not None:
            conditions.append("prix <= ?")
            params.append(prix_max)
        if search:
            conditions.append("(LOWER(titre) LIKE ? OR LOWER(marque) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        if since_ts:
            conditions.append("scraped_le >= ?")
            params.append(since_ts)
        if favs_min is not None:
            conditions.append("nb_favoris >= ?")
            params.append(favs_min)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        q = f"SELECT {','.join(cols)} FROM annonces {where} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def get_feed_annonces(offset: int = 0, limit: int = 40, marque: str = None,
                       taille: str = None, score_min: int = None,
                       prix_min: float = None, prix_max: float = None,
                       search: str = None, order: str = "recent",
                       since_hours: int = None, favs_min: int = None) -> list[dict]:
    conn, mode = get_conn()
    marques = [m.strip().lower() for m in marque.split(",") if m.strip()] if marque else None
    since_ts = (datetime.now() - timedelta(hours=since_hours)).isoformat() if since_hours else None

    if score_min is not None or order == "score":
        candidates = _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search,
                                       "scraped_le DESC", min(offset + limit * 5, 200), 0, since_ts=since_ts, favs_min=favs_min)
        scored = []
        for a in candidates:
            median = get_median_prix(a["marque"], a["taille"])
            a["prix_median"] = median
            a["score"] = scorer_annonce(a, median)
            if score_min is None or a["score"] >= score_min:
                scored.append(a)
        if order == "score":
            scored.sort(key=lambda a: a["score"], reverse=True)
        return scored[offset:offset + limit]

    order_clause = {
        "recent":    "scraped_le DESC",
        "prix_asc":  "prix ASC",
        "prix_desc": "prix DESC",
        "favs":      "nb_favoris DESC",
    }.get(order, "scraped_le DESC")

    return _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search, order_clause, limit, offset, since_ts=since_ts, favs_min=favs_min)


def stats_db() -> dict:
    conn, mode = get_conn()
    if mode == "pg":
        nb_a = conn.run("SELECT COUNT(*) FROM annonces")[0][0]
        nb_p = conn.run("SELECT COUNT(*) FROM prix_history")[0][0]
        niches_rows = conn.run("SELECT marque, COUNT(*) as nb, AVG(prix) as prix_moy, MIN(prix) as prix_min, MAX(prix) as prix_max FROM prix_history GROUP BY marque ORDER BY nb DESC LIMIT 200")
        niches = [{"marque": r[0], "nb": r[1], "prix_moy": r[2], "prix_min": r[3], "prix_max": r[4]} for r in niches_rows]
    else:
        nb_a = conn.execute("SELECT COUNT(*) FROM annonces").fetchone()[0]
        nb_p = conn.execute("SELECT COUNT(*) FROM prix_history").fetchone()[0]
        niches = [dict(r) for r in conn.execute("SELECT marque, COUNT(*) as nb, AVG(prix) as prix_moy, MIN(prix) as prix_min, MAX(prix) as prix_max FROM prix_history GROUP BY marque ORDER BY nb DESC LIMIT 200").fetchall()]
    return {"annonces": nb_a, "prix_history": nb_p, "niches": niches}


# ---- USER NICHES (saved feed filters) ----

def list_user_niches(user_id: str) -> list[dict]:
    conn, mode = get_conn()
    def fmt(v): return round(float(v), 2) if v is not None else None
    if mode == "pg":
        rows = conn.run("SELECT id,nom,marque,taille,score_min,prix_min,recherche,lien,created_le FROM niches WHERE user_id=:u ORDER BY id DESC", u=user_id)
        cols = ["id","nom","marque","taille","score_min","prix_min","recherche","lien","created_le"]
        result = [dict(zip(cols, r)) for r in rows]
        if result:
            ids = [n["id"] for n in result]
            placeholders = ",".join(f":id{i}" for i in range(len(ids)))
            kwargs = {f"id{i}": v for i, v in enumerate(ids)}
            stats_rows = conn.run(
                f"SELECT niche_id, COUNT(*), COUNT(sold_at), AVG(prix), MIN(prix), MAX(prix) FROM niche_items WHERE niche_id IN ({placeholders}) GROUP BY niche_id",
                **kwargs)
            stats = {r[0]: r[1:] for r in stats_rows}
            for n in result:
                r = stats.get(n["id"], (0, 0, None, None, None))
                n["nb_items"] = int(r[0] or 0); n["nb_vendus"] = int(r[1] or 0)
                n["prix_moyen"] = fmt(r[2]); n["prix_min_val"] = fmt(r[3]); n["prix_max_val"] = fmt(r[4])
        else:
            for n in result:
                n["nb_items"] = n["nb_vendus"] = 0; n["prix_moyen"] = n["prix_min_val"] = n["prix_max_val"] = None
    else:
        result = [dict(r) for r in conn.execute("SELECT id,nom,marque,taille,score_min,prix_min,recherche,lien,created_le FROM niches WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]
        if result:
            ids = [n["id"] for n in result]
            placeholders = ",".join("?" for _ in ids)
            stats_rows = conn.execute(
                f"SELECT niche_id, COUNT(*), COUNT(sold_at), AVG(prix), MIN(prix), MAX(prix) FROM niche_items WHERE niche_id IN ({placeholders}) GROUP BY niche_id",
                ids).fetchall()
            stats = {r[0]: r[1:] for r in stats_rows}
            for n in result:
                r = stats.get(n["id"], (0, 0, None, None, None))
                n["nb_items"] = int(r[0] or 0); n["nb_vendus"] = int(r[1] or 0)
                n["prix_moyen"] = fmt(r[2]); n["prix_min_val"] = fmt(r[3]); n["prix_max_val"] = fmt(r[4])
        else:
            for n in result:
                n["nb_items"] = n["nb_vendus"] = 0; n["prix_moyen"] = n["prix_min_val"] = n["prix_max_val"] = None
    return result

def _build_where(mode, marque, taille, prix_min, recherche):
    """Return (where_clause, params_list, kwargs_dict) for niche filters."""
    conditions, params, kwargs = [], [], {}
    if marque:
        conditions.append("LOWER(marque) = " + (":marque" if mode == "pg" else "?"))
        params.append(marque.lower()); kwargs["marque"] = marque.lower()
    if taille:
        conditions.append("LOWER(taille) = " + (":taille" if mode == "pg" else "?"))
        params.append(taille.lower()); kwargs["taille"] = taille.lower()
    if prix_min is not None:
        conditions.append("prix >= " + (":prix_min" if mode == "pg" else "?"))
        params.append(prix_min); kwargs["prix_min"] = prix_min
    if recherche:
        r = f"%{recherche.lower()}%"
        if mode == "pg":
            conditions.append("(LOWER(titre) LIKE :recherche OR LOWER(marque) LIKE :recherche2)")
            params.extend([r, r]); kwargs["recherche"] = r; kwargs["recherche2"] = r
        else:
            conditions.append("(LOWER(titre) LIKE ? OR LOWER(marque) LIKE ?)")
            params.extend([r, r])
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params, kwargs

def create_user_niche(user_id: str, nom: str, marque: str = None, taille: str = None,
                       score_min: int = None, prix_min: float = None, recherche: str = None,
                       lien: str = None) -> dict:
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    if mode == "pg":
        row = conn.run("""INSERT INTO niches (user_id,nom,marque,taille,score_min,prix_min,recherche,lien,created_le)
            VALUES (:u,:nom,:marque,:taille,:score_min,:prix_min,:recherche,:lien,:now) RETURNING id""",
            u=user_id, nom=nom, marque=marque, taille=taille, score_min=score_min, prix_min=prix_min,
            recherche=recherche, lien=lien, now=now)
        new_id = row[0][0]
    else:
        conn.execute("""INSERT INTO niches (user_id,nom,marque,taille,score_min,prix_min,recherche,lien,created_le)
            VALUES (?,?,?,?,?,?,?,?,?)""", (user_id, nom, marque, taille, score_min, prix_min, recherche, lien, now))
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": new_id, "nom": nom, "marque": marque, "taille": taille,
            "score_min": score_min, "prix_min": prix_min, "recherche": recherche, "lien": lien,
            "created_le": now, "nb_annonces": 0}


NICHE_ITEM_LIMITS = {"starter": 1000, "pro": 3000, "expert": 5000}

def get_active_niches() -> list[dict]:
    """Return all niches with their owner's plan (for the worker to enforce item limits)."""
    conn, mode = get_conn()
    if mode == "pg":
        rows = conn.run("""
            SELECT n.id, n.nom, n.lien, n.user_id,
                   COALESCE(s.plan, 'starter') AS plan
            FROM niches n
            LEFT JOIN subscriptions s ON LOWER(s.user_email) = LOWER(n.user_id)
            WHERE n.lien IS NOT NULL AND n.lien != ''
              AND (s.status = 'active' OR n.user_id IN (SELECT user_id FROM niches WHERE true))
        """)
        return [{"id": r[0], "nom": r[1], "lien": r[2], "user_id": r[3], "plan": r[4]} for r in rows]
    rows = conn.execute("""
        SELECT n.id, n.nom, n.lien, n.user_id,
               COALESCE(s.plan, 'starter') AS plan
        FROM niches n
        LEFT JOIN subscriptions s ON LOWER(s.user_email) = LOWER(n.user_id)
        WHERE n.lien IS NOT NULL AND n.lien != ''
    """).fetchall()
    return [dict(r) for r in rows]


def upsert_niche_items(niche_id: int, items: list[dict], max_items: int = None):
    """Insert new items; update last_seen for existing ones. Prune to max_items if set."""
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    for item in items:
        try:
            if mode == "pg":
                conn.run("""INSERT INTO niche_items
                    (niche_id,vinted_id,titre,prix,photo,url,marque,taille,first_seen,last_seen)
                    VALUES (:nid,:vid,:titre,:prix,:photo,:url,:marque,:taille,:now,:now)
                    ON CONFLICT (niche_id,vinted_id) DO UPDATE SET last_seen=:now""",
                    nid=niche_id, vid=int(item["id"]), titre=item.get("titre",""),
                    prix=item.get("prix",0), photo=item.get("photo",""),
                    url=item.get("url",""), marque=item.get("marque",""),
                    taille=item.get("taille",""), now=now)
            else:
                conn.execute("""INSERT OR IGNORE INTO niche_items
                    (niche_id,vinted_id,titre,prix,photo,url,marque,taille,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (niche_id, int(item["id"]), item.get("titre",""), item.get("prix",0),
                     item.get("photo",""), item.get("url",""), item.get("marque",""),
                     item.get("taille",""), now, now))
                conn.execute("UPDATE niche_items SET last_seen=? WHERE niche_id=? AND vinted_id=? AND sold_at IS NULL",
                    (now, niche_id, int(item["id"])))
                conn.commit()
        except Exception as e:
            log.error(f"upsert_niche_items {item.get('id')}: {e}")
    # Enforce plan item limit: delete oldest sold items first, then oldest unsold
    if max_items is not None:
        try:
            if mode == "pg":
                count = conn.run("SELECT COUNT(*) FROM niche_items WHERE niche_id=:nid", nid=niche_id)[0][0]
                if count > max_items:
                    excess = count - max_items
                    conn.run("""DELETE FROM niche_items WHERE id IN (
                        SELECT id FROM niche_items WHERE niche_id=:nid
                        ORDER BY sold_at NULLS LAST, first_seen ASC LIMIT :excess
                    )""", nid=niche_id, excess=excess)
            else:
                count = conn.execute("SELECT COUNT(*) FROM niche_items WHERE niche_id=?", (niche_id,)).fetchone()[0]
                if count > max_items:
                    excess = count - max_items
                    conn.execute("""DELETE FROM niche_items WHERE id IN (
                        SELECT id FROM niche_items WHERE niche_id=?
                        ORDER BY CASE WHEN sold_at IS NULL THEN 1 ELSE 0 END DESC, first_seen ASC LIMIT ?
                    )""", (niche_id, excess))
                    conn.commit()
        except Exception as e:
            log.error(f"upsert_niche_items trim {niche_id}: {e}")


def mark_niche_items_sold(niche_id: int, seen_ids: list[int], scan_interval_sec: int = 1200):
    """Mark items not seen in this scan as sold if they're old enough."""
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(seconds=scan_interval_sec * 2)).isoformat()
    if not seen_ids:
        return
    if mode == "pg":
        # pg8000 doesn't support Python list for ANY/ALL — use NOT IN with explicit placeholders
        placeholders = ",".join(f":s{i}" for i in range(len(seen_ids)))
        kwargs = {f"s{i}": v for i, v in enumerate(seen_ids)}
        kwargs.update({"now": now, "nid": niche_id, "cutoff": cutoff})
        conn.run(f"""UPDATE niche_items SET sold_at=:now
            WHERE niche_id=:nid AND sold_at IS NULL
            AND vinted_id NOT IN ({placeholders}) AND last_seen < :cutoff""",
            **kwargs)
    else:
        placeholders = ",".join("?" for _ in seen_ids)
        conn.execute(f"""UPDATE niche_items SET sold_at=?
            WHERE niche_id=? AND sold_at IS NULL
            AND vinted_id NOT IN ({placeholders}) AND last_seen < ?""",
            [now, niche_id] + seen_ids + [cutoff])
        conn.commit()


def get_niche_items(niche_id: int, limit: int = 100) -> list[dict]:
    conn, mode = get_conn()
    cols = ["id","vinted_id","titre","prix","photo","url","marque","taille","first_seen","sold_at"]
    if mode == "pg":
        rows = conn.run(f"SELECT {','.join(cols)} FROM niche_items WHERE niche_id=:nid ORDER BY first_seen DESC LIMIT :limit",
            nid=niche_id, limit=limit)
        return [dict(zip(cols, r)) for r in rows]
    rows = conn.execute(f"SELECT {','.join(cols)} FROM niche_items WHERE niche_id=? ORDER BY first_seen DESC LIMIT ?",
        (niche_id, limit)).fetchall()
    return [dict(r) for r in rows]

def delete_user_niche(user_id: str, niche_id: int) -> bool:
    conn, mode = get_conn()
    if mode == "pg":
        conn.run("DELETE FROM niches WHERE id=:id AND user_id=:u", id=niche_id, u=user_id)
        return conn.row_count > 0
    conn.execute("DELETE FROM niches WHERE id=? AND user_id=?", (niche_id, user_id))
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0] > 0


# ---- SURVEILLANCE (watchlist) ----

def list_surveillance(user_id: str) -> list[dict]:
    conn, mode = get_conn()
    cols = ["id","annonce_id","titre","marque","taille","prix","photo","url","added_le","last_seen_le","vendu"]
    if mode == "pg":
        rows = conn.run(f"SELECT {','.join(cols)} FROM surveillance WHERE user_id=:u ORDER BY id DESC", u=user_id)
        return [dict(zip(cols, r)) for r in rows]
    return [dict(r) for r in conn.execute(f"SELECT {','.join(cols)} FROM surveillance WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]

def add_surveillance(user_id: str, annonce: dict) -> dict:
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    if mode == "pg":
        conn.run("""INSERT INTO surveillance (user_id,annonce_id,titre,marque,taille,prix,photo,url,added_le,last_seen_le,vendu)
            VALUES (:u,:aid,:titre,:marque,:taille,:prix,:photo,:url,:now,:now,FALSE)
            ON CONFLICT (user_id, annonce_id) DO NOTHING""",
            u=user_id, aid=annonce["id"], titre=annonce.get("titre"), marque=annonce.get("marque"),
            taille=annonce.get("taille"), prix=annonce.get("prix"), photo=annonce.get("photo"),
            url=annonce.get("url"), now=now)
    else:
        conn.execute("""INSERT OR IGNORE INTO surveillance (user_id,annonce_id,titre,marque,taille,prix,photo,url,added_le,last_seen_le,vendu)
            VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (user_id, annonce["id"], annonce.get("titre"), annonce.get("marque"), annonce.get("taille"),
             annonce.get("prix"), annonce.get("photo"), annonce.get("url"), now, now))
        conn.commit()
    return {"ok": True}

def remove_surveillance(user_id: str, annonce_id: int) -> bool:
    conn, mode = get_conn()
    if mode == "pg":
        conn.run("DELETE FROM surveillance WHERE user_id=:u AND annonce_id=:aid", u=user_id, aid=annonce_id)
        return conn.row_count > 0
    conn.execute("DELETE FROM surveillance WHERE user_id=? AND annonce_id=?", (user_id, annonce_id))
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0] > 0

def refresh_surveillance(user_id: str) -> list[dict]:
    """Update last_seen_le/vendu/prix for watched items using a single batch query."""
    conn, mode = get_conn()
    items = list_surveillance(user_id)
    if not items:
        return items
    now = datetime.now().isoformat()
    ann_ids = [it["annonce_id"] for it in items]
    if mode == "pg":
        placeholders = ",".join(f":a{i}" for i in range(len(ann_ids)))
        kwargs = {f"a{i}": v for i, v in enumerate(ann_ids)}
        rows = conn.run(f"SELECT id, prix FROM annonces WHERE id IN ({placeholders})", **kwargs)
        found = {r[0]: r[1] for r in rows}
        for it in items:
            aid = it["annonce_id"]
            if aid in found:
                conn.run("UPDATE surveillance SET prix=:p, last_seen_le=:now, vendu=FALSE WHERE user_id=:u AND annonce_id=:aid",
                    p=found[aid], now=now, u=user_id, aid=aid)
                it["prix"], it["last_seen_le"], it["vendu"] = found[aid], now, False
            else:
                conn.run("UPDATE surveillance SET vendu=TRUE WHERE user_id=:u AND annonce_id=:aid", u=user_id, aid=aid)
                it["vendu"] = True
    else:
        placeholders = ",".join("?" for _ in ann_ids)
        rows = conn.execute(f"SELECT id, prix FROM annonces WHERE id IN ({placeholders})", ann_ids).fetchall()
        found = {r[0]: r[1] for r in rows}
        for it in items:
            aid = it["annonce_id"]
            if aid in found:
                conn.execute("UPDATE surveillance SET prix=?, last_seen_le=?, vendu=0 WHERE user_id=? AND annonce_id=?",
                    (found[aid], now, user_id, aid))
                it["prix"], it["last_seen_le"], it["vendu"] = found[aid], now, False
            else:
                conn.execute("UPDATE surveillance SET vendu=1 WHERE user_id=? AND annonce_id=?", (user_id, aid))
                it["vendu"] = True
        conn.commit()
    return items


# ---- SUBSCRIPTIONS ----

def get_subscription(user_email: str) -> dict | None:
    conn, mode = get_conn()
    if mode == "pg":
        rows = conn.run("SELECT user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,plan,trial_expires_at FROM subscriptions WHERE user_email=:e", e=user_email)
        if not rows:
            return None
        cols = ["user_email","stripe_customer_id","stripe_subscription_id","status","current_period_end","plan","trial_expires_at"]
        return dict(zip(cols, rows[0]))
    row = conn.execute("SELECT user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,plan,trial_expires_at FROM subscriptions WHERE user_email=?", (user_email,)).fetchone()
    return dict(row) if row else None

def get_config(key: str, default: str = None) -> str:
    conn, mode = get_conn()
    try:
        if mode == "pg":
            rows = conn.run("SELECT value FROM config WHERE key=:k", k=key)
            return rows[0][0] if rows else default
        else:
            row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
    except:
        return default

def set_config(key: str, value: str):
    conn, mode = get_conn()
    if mode == "pg":
        conn.run("INSERT INTO config (key,value) VALUES (:k,:v) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", k=key, v=value)
    else:
        conn.execute("INSERT INTO config (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()

def _trial_active(sub: dict) -> bool:
    """Return True if the subscription has an unexpired trial."""
    exp = sub.get("trial_expires_at")
    return bool(exp and exp > datetime.now().isoformat())

def is_subscribed(user_email: str) -> bool:
    sub = get_subscription(user_email)
    if not sub:
        return False
    if sub["status"] == "active":
        return True
    return _trial_active(sub)

def get_user_plan(user_email: str) -> str:
    """Returns 'starter', 'pro', 'expert', or 'free'."""
    sub = get_subscription(user_email)
    if not sub:
        return "free"
    if sub["status"] == "active":
        return sub.get("plan") or "starter"
    if _trial_active(sub):
        return "expert"
    return "free"

TRIAL_HOURS = 24

def start_trial(user_email: str) -> dict:
    """Grant a 24h Expert trial. No-op if user already subscribed or already used trial."""
    sub = get_subscription(user_email)
    # Block if active subscription or trial already started (even if expired)
    if sub and (sub["status"] == "active" or sub.get("trial_expires_at")):
        return {"ok": False, "reason": "already_used"}
    conn, mode = get_conn()
    now = datetime.now()
    expires = (now + timedelta(hours=TRIAL_HOURS)).isoformat()
    now_str = now.isoformat()
    if mode == "pg":
        conn.run("""INSERT INTO subscriptions (user_email, status, plan, trial_expires_at, updated_le)
            VALUES (:e, 'trial', 'expert', :exp, :now)
            ON CONFLICT (user_email) DO UPDATE SET
                trial_expires_at = EXCLUDED.trial_expires_at,
                updated_le = EXCLUDED.updated_le
            WHERE subscriptions.status != 'active' AND subscriptions.trial_expires_at IS NULL""",
            e=user_email, exp=expires, now=now_str)
    else:
        conn.execute("""INSERT INTO subscriptions (user_email, status, plan, trial_expires_at, updated_le)
            VALUES (?, 'trial', 'expert', ?, ?)
            ON CONFLICT(user_email) DO UPDATE SET
                trial_expires_at = excluded.trial_expires_at,
                updated_le = excluded.updated_le
            WHERE status != 'active' AND trial_expires_at IS NULL""",
            (user_email, expires, now_str))
        conn.commit()
    log.info(f"Trial started: {user_email} → expert until {expires}")
    return {"ok": True, "trial_expires_at": expires}

def upsert_subscription(user_email: str, stripe_customer_id: str = None,
                         stripe_subscription_id: str = None, status: str = "active",
                         current_period_end: str = None, plan: str = None):
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    if mode == "pg":
        conn.run("""INSERT INTO subscriptions (user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,plan,updated_le)
            VALUES (:e,:cid,:sid,:status,:cpe,:plan,:now)
            ON CONFLICT (user_email) DO UPDATE SET
                stripe_customer_id=EXCLUDED.stripe_customer_id,
                stripe_subscription_id=EXCLUDED.stripe_subscription_id,
                status=EXCLUDED.status,
                current_period_end=EXCLUDED.current_period_end,
                plan=EXCLUDED.plan,
                updated_le=EXCLUDED.updated_le""",
            e=user_email, cid=stripe_customer_id, sid=stripe_subscription_id,
            status=status, cpe=current_period_end, plan=plan or "starter", now=now)
    else:
        conn.execute("""INSERT INTO subscriptions (user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,plan,updated_le)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(user_email) DO UPDATE SET
                stripe_customer_id=excluded.stripe_customer_id,
                stripe_subscription_id=excluded.stripe_subscription_id,
                status=excluded.status,
                current_period_end=excluded.current_period_end,
                plan=excluded.plan,
                updated_le=excluded.updated_le""",
            (user_email, stripe_customer_id, stripe_subscription_id, status, current_period_end, plan or "starter", now))
        conn.commit()
