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
        conn.run("""CREATE TABLE IF NOT EXISTS niches (
            id BIGSERIAL PRIMARY KEY, user_id TEXT NOT NULL, nom TEXT,
            marque TEXT, taille TEXT, score_min INTEGER, prix_min REAL,
            recherche TEXT, created_le TEXT)""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_niches_user ON niches(user_id)")
        except: pass
        try:
            conn.run("ALTER TABLE niches ADD COLUMN IF NOT EXISTS recherche TEXT")
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
            updated_le TEXT)""")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_sub_email ON subscriptions(user_email)")
        except: pass
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS annonces (
                id INTEGER PRIMARY KEY, titre TEXT, marque TEXT, taille TEXT,
                prix REAL, nb_favoris INTEGER, url TEXT, photo TEXT, vendeur TEXT, scraped_le TEXT);
            CREATE TABLE IF NOT EXISTS prix_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, marque TEXT, taille TEXT, prix REAL, scraped_le TEXT);
            CREATE INDEX IF NOT EXISTS idx_marque_taille ON prix_history(marque, taille);
            CREATE TABLE IF NOT EXISTS niches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, nom TEXT,
                marque TEXT, taille TEXT, score_min INTEGER, prix_min REAL, recherche TEXT, created_le TEXT);
            CREATE INDEX IF NOT EXISTS idx_niches_user ON niches(user_id);
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

def _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search, order_clause, limit, offset):
    cols = ["id","titre","marque","taille","prix","nb_favoris","url","photo","vendeur","scraped_le"]
    if mode == "pg":
        conditions, kwargs = [], {}
        if marques:
            placeholders = ",".join(f":marque{i}" for i in range(len(marques)))
            conditions.append(f"LOWER(marque) IN ({placeholders})")
            for i, m in enumerate(marques):
                kwargs[f"marque{i}"] = m.lower()
        if taille:
            conditions.append("LOWER(taille) = :taille")
            kwargs["taille"] = taille.lower()
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
            conditions.append("LOWER(taille) = ?")
            params.append(taille.lower())
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
        q = f"SELECT {','.join(cols)} FROM annonces {where} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def get_feed_annonces(offset: int = 0, limit: int = 40, marque: str = None,
                       taille: str = None, score_min: int = None,
                       prix_min: float = None, prix_max: float = None,
                       search: str = None, order: str = "recent") -> list[dict]:
    conn, mode = get_conn()
    marques = [m.strip().lower() for m in marque.split(",") if m.strip()] if marque else None

    # score_min / order=score require computing the score per row (median lookup),
    # so fetch a larger candidate window and rank/filter in Python.
    if score_min is not None or order == "score":
        candidates = _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search,
                                       "scraped_le DESC", min(offset + limit * 5, 200), 0)
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

    return _fetch_feed_rows(conn, mode, marques, taille, prix_min, prix_max, search, order_clause, limit, offset)


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


# ---- USER NICHES (saved feed filters) ----

def list_user_niches(user_id: str) -> list[dict]:
    conn, mode = get_conn()
    if mode == "pg":
        rows = conn.run("SELECT id,nom,marque,taille,score_min,prix_min,recherche,created_le FROM niches WHERE user_id=:u ORDER BY id DESC", u=user_id)
        cols = ["id","nom","marque","taille","score_min","prix_min","recherche","created_le"]
        result = [dict(zip(cols, r)) for r in rows]
    else:
        result = [dict(r) for r in conn.execute("SELECT id,nom,marque,taille,score_min,prix_min,recherche,created_le FROM niches WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]

    for n in result:
        n["nb_annonces"] = _count_matching(conn, mode, n["marque"], n["taille"], n["prix_min"], n.get("recherche"))
    return result

def _count_matching(conn, mode, marque, taille, prix_min, recherche=None):
    conditions, params = [], []
    if marque:
        conditions.append("LOWER(marque) = " + (":marque" if mode == "pg" else "?"))
        params.append(marque.lower())
    if taille:
        conditions.append("LOWER(taille) = " + (":taille" if mode == "pg" else "?"))
        params.append(taille.lower())
    if prix_min is not None:
        conditions.append("prix >= " + (":prix_min" if mode == "pg" else "?"))
        params.append(prix_min)
    if recherche:
        if mode == "pg":
            conditions.append("(LOWER(titre) LIKE :recherche OR LOWER(marque) LIKE :recherche2)")
            params.append(f"%{recherche.lower()}%")
            params.append(f"%{recherche.lower()}%")
        else:
            conditions.append("(LOWER(titre) LIKE ? OR LOWER(marque) LIKE ?)")
            params.append(f"%{recherche.lower()}%")
            params.append(f"%{recherche.lower()}%")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    q = f"SELECT COUNT(*) FROM annonces {where}"
    if mode == "pg":
        kwargs = {}
        i = 0
        if marque: kwargs["marque"] = params[i]; i += 1
        if taille: kwargs["taille"] = params[i]; i += 1
        if prix_min is not None: kwargs["prix_min"] = params[i]; i += 1
        if recherche:
            kwargs["recherche"] = params[i]; i += 1
            kwargs["recherche2"] = params[i]; i += 1  # two identical values, two named params
        return conn.run(q, **kwargs)[0][0]
    return conn.execute(q, params).fetchone()[0]

def create_user_niche(user_id: str, nom: str, marque: str = None, taille: str = None,
                       score_min: int = None, prix_min: float = None, recherche: str = None) -> dict:
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    if mode == "pg":
        row = conn.run("""INSERT INTO niches (user_id,nom,marque,taille,score_min,prix_min,recherche,created_le)
            VALUES (:u,:nom,:marque,:taille,:score_min,:prix_min,:recherche,:now) RETURNING id""",
            u=user_id, nom=nom, marque=marque, taille=taille, score_min=score_min, prix_min=prix_min, recherche=recherche, now=now)
        new_id = row[0][0]
    else:
        conn.execute("""INSERT INTO niches (user_id,nom,marque,taille,score_min,prix_min,recherche,created_le)
            VALUES (?,?,?,?,?,?,?,?)""", (user_id, nom, marque, taille, score_min, prix_min, recherche, now))
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": new_id, "nom": nom, "marque": marque, "taille": taille,
            "score_min": score_min, "prix_min": prix_min, "recherche": recherche, "created_le": now, "nb_annonces": 0}

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
    """Update last_seen_le/vendu/prix for watched items by checking if they're still in `annonces`."""
    conn, mode = get_conn()
    items = list_surveillance(user_id)
    now = datetime.now().isoformat()
    for it in items:
        if mode == "pg":
            row = conn.run("SELECT prix FROM annonces WHERE id=:id", id=it["annonce_id"])
            if row:
                conn.run("UPDATE surveillance SET prix=:p, last_seen_le=:now, vendu=FALSE WHERE user_id=:u AND annonce_id=:aid",
                    p=row[0][0], now=now, u=user_id, aid=it["annonce_id"])
                it["prix"], it["last_seen_le"], it["vendu"] = row[0][0], now, False
            else:
                conn.run("UPDATE surveillance SET vendu=TRUE WHERE user_id=:u AND annonce_id=:aid", u=user_id, aid=it["annonce_id"])
                it["vendu"] = True
        else:
            row = conn.execute("SELECT prix FROM annonces WHERE id=?", (it["annonce_id"],)).fetchone()
            if row:
                conn.execute("UPDATE surveillance SET prix=?, last_seen_le=?, vendu=0 WHERE user_id=? AND annonce_id=?",
                    (row[0], now, user_id, it["annonce_id"]))
                it["prix"], it["last_seen_le"], it["vendu"] = row[0], now, False
            else:
                conn.execute("UPDATE surveillance SET vendu=1 WHERE user_id=? AND annonce_id=?", (user_id, it["annonce_id"]))
                it["vendu"] = True
            conn.commit()
    return items


# ---- SUBSCRIPTIONS ----

def get_subscription(user_email: str) -> dict | None:
    conn, mode = get_conn()
    if mode == "pg":
        rows = conn.run("SELECT user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end FROM subscriptions WHERE user_email=:e", e=user_email)
        if not rows:
            return None
        cols = ["user_email","stripe_customer_id","stripe_subscription_id","status","current_period_end"]
        return dict(zip(cols, rows[0]))
    row = conn.execute("SELECT user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end FROM subscriptions WHERE user_email=?", (user_email,)).fetchone()
    return dict(row) if row else None

def is_subscribed(user_email: str) -> bool:
    sub = get_subscription(user_email)
    if not sub:
        return False
    return sub["status"] == "active"

def upsert_subscription(user_email: str, stripe_customer_id: str = None,
                         stripe_subscription_id: str = None, status: str = "active",
                         current_period_end: str = None):
    conn, mode = get_conn()
    now = datetime.now().isoformat()
    if mode == "pg":
        conn.run("""INSERT INTO subscriptions (user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,updated_le)
            VALUES (:e,:cid,:sid,:status,:cpe,:now)
            ON CONFLICT (user_email) DO UPDATE SET
                stripe_customer_id=EXCLUDED.stripe_customer_id,
                stripe_subscription_id=EXCLUDED.stripe_subscription_id,
                status=EXCLUDED.status,
                current_period_end=EXCLUDED.current_period_end,
                updated_le=EXCLUDED.updated_le""",
            e=user_email, cid=stripe_customer_id, sid=stripe_subscription_id,
            status=status, cpe=current_period_end, now=now)
    else:
        conn.execute("""INSERT INTO subscriptions (user_email,stripe_customer_id,stripe_subscription_id,status,current_period_end,updated_le)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(user_email) DO UPDATE SET
                stripe_customer_id=excluded.stripe_customer_id,
                stripe_subscription_id=excluded.stripe_subscription_id,
                status=excluded.status,
                current_period_end=excluded.current_period_end,
                updated_le=excluded.updated_le""",
            (user_email, stripe_customer_id, stripe_subscription_id, status, current_period_end, now))
        conn.commit()
