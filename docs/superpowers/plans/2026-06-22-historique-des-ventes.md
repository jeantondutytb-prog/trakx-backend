# Historique des ventes par tracker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Historique des ventes" tab on the tracker detail page (frontend) showing detailed, paginated (infinite scroll) lists of items in 3 categories — Ventes / Retirés / Invendus — with search, filters, sort and CSV export, backed by a real vendu-vs-retiré detection in the backend.

**Architecture:** Backend (`vintedspy-backend`, FastAPI + dual SQLite/Postgres `database.py`) gains 3 new `niche_items` columns, an HTTP-based classification step in the scan pipeline, and 3 new read endpoints (`/history`, `/history/facets`, `/history/export`). Frontend (`vintedspy-frontend`, single-file vanilla JS `app/index.html`) gains a 4th tab on the niche detail page reusing the existing infinite-scroll/card patterns from the Feed page.

**Tech Stack:** Python 3 / FastAPI / pg8000 (Postgres) / sqlite3 — backend. Vanilla JS / HTML / Tailwind-via-CDN — frontend. `pytest` for backend tests (new dependency, project currently has zero test infrastructure).

**Spec:** `docs/superpowers/specs/2026-06-22-historique-des-ventes-design.md` (this repo).

## Global Constraints

- New `niche_items` columns (`nb_favoris`, `etat`, `sold_status`) must be added in **both** the Postgres branch (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) and the SQLite branch (`ALTER TABLE ... ADD COLUMN` wrapped in try/except) of `init_db()`, matching the existing migration style in `database.py`.
- Vendu/retiré classification: HTTP **200** on the item's stored `url` → `'vendu'`. HTTP **404** → `'retire'`. Any other outcome (timeout, network error, other status code) → fallback `'vendu'`.
- Verification cap: at most 50 HTTP verification calls per niche per scan cycle (`verify_cap=50` default).
- Historical rows with `sold_status IS NULL` (pre-existing sold items from before this feature) are treated as `'vendu'` by all queries — never as `'retire'`.
- 3 status values used end-to-end: `actif` (still live, `sold_at IS NULL`), `vendu`, `retire`.
- History pagination: default `limit=30`, API validates `1 <= limit <= 100`, `offset >= 0`.
- CSV export has **no pagination** — it streams every row matching the filters, in batches of 200 read from `get_niche_history`, never holding the full result set in memory.
- CSV columns, in order: `Titre, Prix, État, Marque, Favoris, Date d'ajout, Date de vente`. Dates formatted `DD/MM/YYYY`; "Date de vente" is empty string when `sold_at` is null.
- Frontend export **must** use an authenticated `fetch()` + Blob download (anchor with `URL.createObjectURL`), **not** a plain `<a href>` navigation — `authHeader()` returns a Supabase Bearer token that only `fetch()` can attach. (This corrects an inaccuracy in the written spec, which suggested direct navigation.)
- Frontend reuses existing helpers/patterns: `esc()`, `safeUrl()`, `getEmoji()`, the `.vs-card`/`.tag`/`--green` CSS, and the `IntersectionObserver` infinite-scroll pattern already used for the Feed (`feedObserver`/`loadFeedPage`).
- Default sub-tab on opening "Historique des ventes": **Ventes**. Search input debounced 400ms (matches existing `feedFilterChange` convention).
- No automated frontend tests exist in this project (single HTML file, no test runner) — frontend tasks are verified manually per the spec's "Tests" section, consistent with prior work this session.

---

## Backend tasks (`vintedspy-backend`)

### Task 1: Test DB override + schema migration

**Files:**
- Modify: `database.py` (`get_conn()` sqlite branch, `init_db()` both branches)
- Create: `tests/conftest.py`
- Create: `tests/test_migration.py`
- Modify: `requirements.txt` (add `pytest`)

**Interfaces:**
- Produces: env var `TRAKR_DB_PATH` (sqlite mode only) overrides the default `~/Downloads/trakr.db` path — used by all later tests via the `db` fixture.
- Produces: `niche_items` table has columns `nb_favoris INTEGER`, `etat TEXT`, `sold_status TEXT` in both DB modes.

- [ ] **Step 1: Add `pytest` to requirements**

Append to `requirements.txt`:
```
pytest==8.2.0
```

Run: `cd /Users/jean/Desktop/vintedspy-backend && pip install -r requirements.txt`

- [ ] **Step 2: Add the `TRAKR_DB_PATH` override in `get_conn()`**

In `database.py`, replace the sqlite branch of `get_conn()`:
```python
    else:
        import sqlite3
        db_path = Path(os.getenv("TRAKR_DB_PATH") or (Path.home() / "Downloads" / "trakr.db"))
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"
```
(This is the same code, only the `db_path` line changes — `os` is already imported at the top of the file.)

- [ ] **Step 3: Write the `db` pytest fixture**

Create `tests/conftest.py`:
```python
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh, isolated SQLite database for one test, with the schema applied."""
    monkeypatch.setenv("TRAKR_DB_PATH", str(tmp_path / "test.db"))
    import database
    monkeypatch.setattr(database, "DATABASE_URL", None)
    database.init_db()
    return database
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_migration.py`:
```python
def test_niche_items_has_new_columns(db):
    conn, mode = db.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(niche_items)").fetchall()]
    assert "nb_favoris" in cols
    assert "etat" in cols
    assert "sold_status" in cols
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_migration.py -v`
Expected: FAIL — `nb_favoris` (and the other two) are not yet in `niche_items` (the table only has the original columns).

- [ ] **Step 6: Add the 3 new columns to the Postgres branch of `init_db()`**

In `database.py`, immediately after the existing block:
```python
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
```
add:
```python
        try:
            conn.run("ALTER TABLE niche_items ADD COLUMN IF NOT EXISTS nb_favoris INTEGER")
        except: pass
        try:
            conn.run("ALTER TABLE niche_items ADD COLUMN IF NOT EXISTS etat TEXT")
        except: pass
        try:
            conn.run("ALTER TABLE niche_items ADD COLUMN IF NOT EXISTS sold_status TEXT")
        except: pass
```

- [ ] **Step 7: Add the 3 new columns to the SQLite branch of `init_db()`**

In `database.py`, immediately after the existing block:
```python
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
```
add:
```python
        try:
            conn.execute("ALTER TABLE niche_items ADD COLUMN nb_favoris INTEGER")
            conn.commit()
        except: pass
        try:
            conn.execute("ALTER TABLE niche_items ADD COLUMN etat TEXT")
            conn.commit()
        except: pass
        try:
            conn.execute("ALTER TABLE niche_items ADD COLUMN sold_status TEXT")
            conn.commit()
        except: pass
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_migration.py -v`
Expected: `1 passed`

- [ ] **Step 9: Commit**

```bash
git add requirements.txt database.py tests/conftest.py tests/test_migration.py
git commit -m "feat: add niche_items columns (nb_favoris, etat, sold_status) + test DB override"
```

---

### Task 2: `classify_sold_status()` pure function

**Files:**
- Modify: `database.py`
- Create: `tests/test_classify_sold_status.py`

**Interfaces:**
- Produces: `classify_sold_status(http_status_code: int | None) -> str` — returns `"retire"` for `404`, `"vendu"` otherwise (including `None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_classify_sold_status.py`:
```python
import database


def test_classify_200_is_vendu():
    assert database.classify_sold_status(200) == "vendu"


def test_classify_404_is_retire():
    assert database.classify_sold_status(404) == "retire"


def test_classify_none_falls_back_to_vendu():
    assert database.classify_sold_status(None) == "vendu"


def test_classify_other_status_falls_back_to_vendu():
    assert database.classify_sold_status(500) == "vendu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify_sold_status.py -v`
Expected: FAIL with `AttributeError: module 'database' has no attribute 'classify_sold_status'`

- [ ] **Step 3: Implement**

In `database.py`, add near `mark_niche_items_sold` (the function this will replace in Task 5):
```python
def classify_sold_status(http_status_code) -> str:
    """Classify a disappeared niche item from the HTTP status of its Vinted page.
    Vinted keeps sold listings reachable (200, shown with a 'Vendu' badge) but
    fully deletes withdrawn ones (404). Any other outcome (timeout, 429, etc.)
    falls back to 'vendu' rather than leaving the item unclassified."""
    if http_status_code == 404:
        return "retire"
    return "vendu"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify_sold_status.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_classify_sold_status.py
git commit -m "feat: add classify_sold_status() for vendu/retire detection"
```

---

### Task 3: Capture `etat` (condition) in the scraper

**Files:**
- Modify: `scheduler.py` (`parse_item()`)
- Create: `tests/test_parse_item.py`

**Interfaces:**
- Produces: `parse_item(item)` return dict now includes key `"etat"` (string, may be empty).

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_item.py`:
```python
import scheduler


def test_parse_item_captures_etat():
    raw = {
        "id": 123,
        "title": "T-shirt",
        "brand_title": "Nike",
        "size_title": "M",
        "price": {"amount": "10.0"},
        "favourite_count": 5,
        "path": "/items/123-t-shirt",
        "photo": {"url": "https://images1.vinted.net/x.jpg"},
        "user": {"login": "bob"},
        "catalog_title": "Hommes",
        "created_at": "2026-01-01T00:00:00Z",
        "status": "Très bon état",
    }
    result = scheduler.parse_item(raw)
    assert result["etat"] == "Très bon état"


def test_parse_item_defaults_etat_to_empty_string():
    raw = {
        "id": 124, "title": "Pull", "brand_title": "", "size_title": "",
        "price": {"amount": "5.0"}, "path": "/items/124-pull",
    }
    result = scheduler.parse_item(raw)
    assert result["etat"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parse_item.py -v`
Expected: FAIL with `KeyError: 'etat'`

- [ ] **Step 3: Implement**

In `scheduler.py`, `parse_item()` currently returns:
```python
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
            "publie_le":  item.get("created_at", ""),
        }
```
Add one key, `"etat"`:
```python
        return {
            "id":         int(item["id"]),
            "titre":      item.get("title", ""),
            "marque":     item.get("brand_title", "") or "Sans marque",
            "taille":     item.get("size_title", ""),
            "prix":       prix,
            "nb_favoris": int(item.get("favourite_count", 0)),
            "etat":       item.get("status", ""),
            "url":        BASE + item.get("path", ""),
            "photo":      (item.get("photo") or {}).get("url", ""),
            "vendeur":    (item.get("user") or {}).get("login", ""),
            "categorie":  item.get("catalog_title", ""),
            "publie_le":  item.get("created_at", ""),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_parse_item.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_parse_item.py
git commit -m "feat: capture item condition (etat) from Vinted's status field"
```

---

### Task 4: Store `nb_favoris` and `etat` in `niche_items`

**Files:**
- Modify: `database.py` (`upsert_niche_items()`)
- Create: `tests/test_upsert_niche_items.py`

**Interfaces:**
- Consumes: `classify_sold_status` not needed here. Relies on Task 1's `nb_favoris`/`etat` columns and Task 3's `parse_item` output shape (dict with `"nb_favoris"`, `"etat"` keys, used by `upsert_niche_items`'s `items` argument).
- Produces: `upsert_niche_items(niche_id, items)` now persists `nb_favoris` and `etat` on insert, and refreshes them on every subsequent scan for still-active rows.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_upsert_niche_items.py`:
```python
def test_upsert_stores_favoris_and_etat(db):
    item = {
        "id": 1, "titre": "Robe", "prix": 20.0, "photo": "",
        "url": "https://vinted.fr/items/1", "marque": "Zara", "taille": "M",
        "nb_favoris": 7, "etat": "Neuf avec étiquette",
    }
    db.upsert_niche_items(1, [item])

    conn, mode = db.get_conn()
    row = conn.execute(
        "SELECT nb_favoris, etat FROM niche_items WHERE niche_id=1 AND vinted_id=1"
    ).fetchone()
    assert row["nb_favoris"] == 7
    assert row["etat"] == "Neuf avec étiquette"


def test_upsert_refreshes_favoris_and_etat_on_existing_item(db):
    item1 = {
        "id": 2, "titre": "Sac", "prix": 30.0, "photo": "",
        "url": "https://vinted.fr/items/2", "marque": "Lulu", "taille": "U",
        "nb_favoris": 1, "etat": "Bon état",
    }
    db.upsert_niche_items(1, [item1])
    item2 = {**item1, "nb_favoris": 9, "etat": "Très bon état"}
    db.upsert_niche_items(1, [item2])

    conn, mode = db.get_conn()
    row = conn.execute(
        "SELECT nb_favoris, etat FROM niche_items WHERE niche_id=1 AND vinted_id=2"
    ).fetchone()
    assert row["nb_favoris"] == 9
    assert row["etat"] == "Très bon état"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upsert_niche_items.py -v`
Expected: FAIL — `row["nb_favoris"]` is `None` (column exists from Task 1 but is never written).

- [ ] **Step 3: Implement**

In `database.py`, replace the body of `upsert_niche_items()`'s per-item insert block:
```python
    for item in items:
        try:
            if mode == "pg":
                conn.run("""INSERT INTO niche_items
                    (niche_id,vinted_id,titre,prix,photo,url,marque,taille,nb_favoris,etat,first_seen,last_seen)
                    VALUES (:nid,:vid,:titre,:prix,:photo,:url,:marque,:taille,:nb_favoris,:etat,:now,:now)
                    ON CONFLICT (niche_id,vinted_id) DO UPDATE SET last_seen=:now, nb_favoris=:nb_favoris, etat=:etat""",
                    nid=niche_id, vid=int(item["id"]), titre=item.get("titre",""),
                    prix=item.get("prix",0), photo=item.get("photo",""),
                    url=item.get("url",""), marque=item.get("marque",""),
                    taille=item.get("taille",""), nb_favoris=item.get("nb_favoris",0),
                    etat=item.get("etat",""), now=now)
            else:
                conn.execute("""INSERT OR IGNORE INTO niche_items
                    (niche_id,vinted_id,titre,prix,photo,url,marque,taille,nb_favoris,etat,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (niche_id, int(item["id"]), item.get("titre",""), item.get("prix",0),
                     item.get("photo",""), item.get("url",""), item.get("marque",""),
                     item.get("taille",""), item.get("nb_favoris",0), item.get("etat",""), now, now))
                conn.execute("UPDATE niche_items SET last_seen=?, nb_favoris=?, etat=? WHERE niche_id=? AND vinted_id=? AND sold_at IS NULL",
                    (now, item.get("nb_favoris",0), item.get("etat",""), niche_id, int(item["id"])))
                conn.commit()
        except Exception as e:
            log.error(f"upsert_niche_items {item.get('id')}: {e}")
```
(Only the SQL strings and bound params change — the `try`/`except Exception as e` wrapper and everything after this loop, the `max_items` pruning block, stay exactly as-is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_upsert_niche_items.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_upsert_niche_items.py
git commit -m "feat: persist nb_favoris and etat on niche_items"
```

---

### Task 5: Real vendu/retiré detection in `mark_niche_items_sold()`

**Files:**
- Modify: `database.py` (replace `mark_niche_items_sold`, add `get_disappeared_niche_items` + `set_niche_item_sold`)
- Modify: `scheduler.py` (`run_niche_scans()`, add `_fetch_item_status()`)
- Create: `tests/test_mark_niche_items_sold.py`

**Interfaces:**
- Consumes: `classify_sold_status` (Task 2).
- Produces: `get_disappeared_niche_items(niche_id, seen_ids, cutoff) -> list[dict]` (each `{"vinted_id": int, "url": str}`). `set_niche_item_sold(niche_id, vinted_id, sold_status, when=None)`. `async def mark_niche_items_sold(niche_id, seen_ids, scan_interval_sec, fetch_status_fn, verify_cap=50, verify_delay_s=0.3)` where `fetch_status_fn` is `async def fetch_status_fn(url: str) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mark_niche_items_sold.py`:
```python
import asyncio
from datetime import datetime, timedelta


def _insert_stale_item(db, niche_id, vinted_id, url, last_seen_offset_sec):
    conn, mode = db.get_conn()
    last_seen = (datetime.now() - timedelta(seconds=last_seen_offset_sec)).isoformat()
    conn.execute(
        "INSERT INTO niche_items "
        "(niche_id, vinted_id, titre, prix, photo, url, marque, taille, nb_favoris, etat, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (niche_id, vinted_id, "Item", 10.0, "", url, "Marque", "M", 0, "", last_seen, last_seen),
    )
    conn.commit()


def test_mark_niche_items_sold_classifies_via_fetch_status(db):
    _insert_stale_item(db, niche_id=1, vinted_id=10, url="https://vinted.fr/items/10", last_seen_offset_sec=5000)
    _insert_stale_item(db, niche_id=1, vinted_id=11, url="https://vinted.fr/items/11", last_seen_offset_sec=5000)

    async def fake_fetch(url):
        return 200 if url.endswith("/10") else 404

    asyncio.run(db.mark_niche_items_sold(
        niche_id=1, seen_ids=[999], scan_interval_sec=1200,
        fetch_status_fn=fake_fetch, verify_cap=50, verify_delay_s=0,
    ))

    conn, mode = db.get_conn()
    row10 = conn.execute("SELECT sold_at, sold_status FROM niche_items WHERE vinted_id=10").fetchone()
    row11 = conn.execute("SELECT sold_at, sold_status FROM niche_items WHERE vinted_id=11").fetchone()
    assert row10["sold_at"] is not None and row10["sold_status"] == "vendu"
    assert row11["sold_at"] is not None and row11["sold_status"] == "retire"


def test_mark_niche_items_sold_respects_verify_cap(db):
    for i in range(5):
        _insert_stale_item(db, niche_id=1, vinted_id=100 + i, url=f"https://vinted.fr/items/{100+i}", last_seen_offset_sec=5000)

    calls = []

    async def counting_fetch(url):
        calls.append(url)
        return 200

    asyncio.run(db.mark_niche_items_sold(
        niche_id=1, seen_ids=[999999], scan_interval_sec=1200,
        fetch_status_fn=counting_fetch, verify_cap=2, verify_delay_s=0,
    ))
    assert len(calls) == 2


def test_mark_niche_items_sold_ignores_recently_seen_items(db):
    _insert_stale_item(db, niche_id=1, vinted_id=200, url="https://vinted.fr/items/200", last_seen_offset_sec=60)

    async def fake_fetch(url):
        return 200

    asyncio.run(db.mark_niche_items_sold(
        niche_id=1, seen_ids=[999999], scan_interval_sec=1200,
        fetch_status_fn=fake_fetch, verify_cap=50, verify_delay_s=0,
    ))

    conn, mode = db.get_conn()
    row = conn.execute("SELECT sold_at FROM niche_items WHERE vinted_id=200").fetchone()
    assert row["sold_at"] is None


def test_mark_niche_items_sold_stops_on_rate_limit(db):
    for i in range(3):
        _insert_stale_item(db, niche_id=1, vinted_id=300 + i, url=f"https://vinted.fr/items/{300+i}", last_seen_offset_sec=5000)

    calls = []

    async def rate_limited_fetch(url):
        calls.append(url)
        return 429

    asyncio.run(db.mark_niche_items_sold(
        niche_id=1, seen_ids=[999999], scan_interval_sec=1200,
        fetch_status_fn=rate_limited_fetch, verify_cap=50, verify_delay_s=0,
    ))

    # Only the first candidate should have been tried — the loop must stop on 429
    # rather than burning through the rest of the cap.
    assert len(calls) == 1
    conn, mode = db.get_conn()
    untouched = conn.execute(
        "SELECT COUNT(*) FROM niche_items WHERE vinted_id IN (301, 302) AND sold_at IS NOT NULL"
    ).fetchone()[0]
    assert untouched == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mark_niche_items_sold.py -v`
Expected: FAIL — `mark_niche_items_sold()` is a sync bulk-UPDATE function with a different signature (no `fetch_status_fn`), so this raises a `TypeError`.

- [ ] **Step 3: Implement**

In `database.py`, replace the entire existing `mark_niche_items_sold` function with three functions:
```python
def get_disappeared_niche_items(niche_id: int, seen_ids: list[int], cutoff: str) -> list[dict]:
    """Items in this niche that are still unsold but weren't seen in the latest
    scan and haven't been seen since before `cutoff` — candidates for vendu/retire
    classification."""
    conn, mode = get_conn()
    if not seen_ids:
        return []
    if mode == "pg":
        placeholders = ",".join(f":s{i}" for i in range(len(seen_ids)))
        kwargs = {f"s{i}": v for i, v in enumerate(seen_ids)}
        kwargs.update({"nid": niche_id, "cutoff": cutoff})
        rows = conn.run(f"""SELECT vinted_id, url FROM niche_items
            WHERE niche_id=:nid AND sold_at IS NULL
            AND vinted_id NOT IN ({placeholders}) AND last_seen < :cutoff""", **kwargs)
        return [{"vinted_id": r[0], "url": r[1]} for r in rows]
    placeholders = ",".join("?" for _ in seen_ids)
    rows = conn.execute(f"""SELECT vinted_id, url FROM niche_items
        WHERE niche_id=? AND sold_at IS NULL
        AND vinted_id NOT IN ({placeholders}) AND last_seen < ?""",
        [niche_id] + seen_ids + [cutoff]).fetchall()
    return [{"vinted_id": r["vinted_id"], "url": r["url"]} for r in rows]


def set_niche_item_sold(niche_id: int, vinted_id: int, sold_status: str, when: str = None):
    conn, mode = get_conn()
    now = when or datetime.now().isoformat()
    if mode == "pg":
        conn.run("UPDATE niche_items SET sold_at=:now, sold_status=:status WHERE niche_id=:nid AND vinted_id=:vid",
            now=now, status=sold_status, nid=niche_id, vid=vinted_id)
    else:
        conn.execute("UPDATE niche_items SET sold_at=?, sold_status=? WHERE niche_id=? AND vinted_id=?",
            (now, sold_status, niche_id, vinted_id))
        conn.commit()


async def mark_niche_items_sold(niche_id: int, seen_ids: list[int], scan_interval_sec: int,
                                  fetch_status_fn, verify_cap: int = 50, verify_delay_s: float = 0.3):
    """For items not seen in this scan and stale enough, verify via fetch_status_fn
    whether the item's page still resolves (sold) or is gone (retire), then mark
    sold_at + sold_status. fetch_status_fn(url) -> int | None is injected by the
    caller so this stays testable without real network access. Stops verifying
    for the rest of this cycle as soon as a 429 (rate limited) is seen, instead
    of burning through the remaining candidates."""
    if not seen_ids:
        return
    cutoff = (datetime.now() - timedelta(seconds=scan_interval_sec * 2)).isoformat()
    candidates = get_disappeared_niche_items(niche_id, seen_ids, cutoff)[:verify_cap]
    for i, item in enumerate(candidates):
        try:
            code = await fetch_status_fn(item["url"])
        except Exception:
            code = None
        if code == 429:
            break
        status = classify_sold_status(code)
        set_niche_item_sold(niche_id, item["vinted_id"], status)
        if verify_delay_s and i < len(candidates) - 1:
            await asyncio.sleep(verify_delay_s)
```
This requires `asyncio` to be importable in `database.py`. At the top of `database.py`, change:
```python
import os, statistics, logging, threading
```
to:
```python
import os, statistics, logging, threading, asyncio
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mark_niche_items_sold.py -v`
Expected: `4 passed`

- [ ] **Step 5: Wire the real HTTP check into the scheduler**

In `scheduler.py`, add a small helper right after `parse_item()`:
```python
async def _fetch_item_status(s, url: str):
    try:
        r = await s.get(url, headers={"User-Agent": UA}, timeout=10)
        return r.status_code
    except Exception:
        return None
```
Then in `run_niche_scans()`, change:
```python
                upsert_niche_items(niche_id, annonces, max_items=max_items)
                seen_ids = [a["id"] for a in annonces]
                mark_niche_items_sold(niche_id, seen_ids, INTERVAL)
```
to:
```python
                upsert_niche_items(niche_id, annonces, max_items=max_items)
                seen_ids = [a["id"] for a in annonces]
                await mark_niche_items_sold(
                    niche_id, seen_ids, INTERVAL,
                    fetch_status_fn=lambda url: _fetch_item_status(s, url),
                )
```
This wiring isn't covered by an automated test (it needs real network access to Vinted); it will be smoke-tested manually after deploy by watching `/tmp/trakr.log` (or the Downloads log) for a niche with disappearing items, and checking `sold_status` gets populated in the DB.

- [ ] **Step 6: Run the full backend test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: all tests pass (no regressions from the signature change — `mark_niche_items_sold` had exactly one caller, just updated in Step 5).

- [ ] **Step 7: Commit**

```bash
git add database.py scheduler.py tests/test_mark_niche_items_sold.py
git commit -m "feat: real vendu/retire detection via item page HTTP status"
```

---

### Task 6: `get_niche_history()` — filtered, sorted, paginated query

**Files:**
- Modify: `database.py`
- Create: `tests/test_get_niche_history.py`

**Interfaces:**
- Produces: `get_niche_history(niche_id, status, search=None, marque=None, etat=None, sort="recent", limit=30, offset=0) -> {"items": [...], "total": int}`. Each item dict has keys: `id, vinted_id, titre, prix, photo, url, marque, taille, etat, nb_favoris, first_seen, sold_at`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_get_niche_history.py`:
```python
def _seed(db, niche_id=1):
    items = [
        {"id": 1, "titre": "Robe rouge", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/1",
         "marque": "Zara", "taille": "M", "nb_favoris": 3, "etat": "Neuf avec étiquette"},
        {"id": 2, "titre": "Sac noir", "prix": 50.0, "photo": "", "url": "https://vinted.fr/items/2",
         "marque": "Lulu", "taille": "U", "nb_favoris": 1, "etat": "Très bon état"},
        {"id": 3, "titre": "Pull bleu", "prix": 10.0, "photo": "", "url": "https://vinted.fr/items/3",
         "marque": "Zara", "taille": "S", "nb_favoris": 0, "etat": "Bon état"},
    ]
    db.upsert_niche_items(niche_id, items)
    db.set_niche_item_sold(niche_id, 1, "vendu")
    db.set_niche_item_sold(niche_id, 2, "retire")
    # item 3 stays active (sold_at IS NULL)
    return items


def test_actif_returns_only_unsold_items(db):
    _seed(db)
    result = db.get_niche_history(1, "actif")
    assert result["total"] == 1
    assert result["items"][0]["titre"] == "Pull bleu"


def test_vendu_returns_only_sold_items(db):
    _seed(db)
    result = db.get_niche_history(1, "vendu")
    assert result["total"] == 1
    assert result["items"][0]["titre"] == "Robe rouge"


def test_retire_returns_only_withdrawn_items(db):
    _seed(db)
    result = db.get_niche_history(1, "retire")
    assert result["total"] == 1
    assert result["items"][0]["titre"] == "Sac noir"


def test_legacy_sold_items_without_sold_status_count_as_vendu(db):
    _seed(db)
    conn, mode = db.get_conn()
    conn.execute("UPDATE niche_items SET sold_status=NULL WHERE vinted_id=1")
    conn.commit()
    result = db.get_niche_history(1, "vendu")
    assert result["total"] == 1


def test_search_filters_by_title_or_marque(db):
    _seed(db)
    result = db.get_niche_history(1, "actif", search="pull")
    assert result["total"] == 1
    result2 = db.get_niche_history(1, "actif", search="zara")
    assert result2["total"] == 1


def test_marque_filter(db):
    _seed(db)
    result = db.get_niche_history(1, "vendu", marque="Zara")
    assert result["total"] == 1
    result_none = db.get_niche_history(1, "retire", marque="Zara")
    assert result_none["total"] == 0


def test_sort_prix_asc_and_desc(db):
    _seed(db)
    conn, mode = db.get_conn()
    conn.execute("UPDATE niche_items SET sold_at=?, sold_status='vendu' WHERE vinted_id=3", ("2026-01-01T00:00:00",))
    conn.commit()
    asc = db.get_niche_history(1, "vendu", sort="prix_asc")
    desc = db.get_niche_history(1, "vendu", sort="prix_desc")
    assert [it["prix"] for it in asc["items"]] == [10.0, 20.0]
    assert [it["prix"] for it in desc["items"]] == [20.0, 10.0]


def test_pagination_limit_and_offset(db):
    niche_id = 1
    items = [
        {"id": i, "titre": f"Item {i}", "prix": float(i), "photo": "", "url": f"https://vinted.fr/items/{i}",
         "marque": "M", "taille": "M", "nb_favoris": 0, "etat": ""}
        for i in range(1, 6)
    ]
    db.upsert_niche_items(niche_id, items)
    page1 = db.get_niche_history(niche_id, "actif", sort="recent", limit=2, offset=0)
    page2 = db.get_niche_history(niche_id, "actif", sort="recent", limit=2, offset=2)
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    assert {it["vinted_id"] for it in page1["items"]} != {it["vinted_id"] for it in page2["items"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_get_niche_history.py -v`
Expected: FAIL with `AttributeError: module 'database' has no attribute 'get_niche_history'`

- [ ] **Step 3: Implement**

In `database.py`, add:
```python
def get_niche_history(niche_id: int, status: str, search: str = None, marque: str = None,
                       etat: str = None, sort: str = "recent", limit: int = 30, offset: int = 0) -> dict:
    """Paginated, filtered listing of niche_items for one of three categories:
    'actif' (still live), 'vendu' (sold), 'retire' (withdrawn by the seller)."""
    conn, mode = get_conn()
    cols = ["id", "vinted_id", "titre", "prix", "photo", "url", "marque", "taille",
            "etat", "nb_favoris", "first_seen", "sold_at"]
    sort_map = {
        "recent": "first_seen DESC",
        "prix_asc": "prix ASC",
        "prix_desc": "prix DESC",
        "vente_recent": "sold_at DESC",
    }
    order_by = sort_map.get(sort, sort_map["recent"])

    def status_clause():
        if status == "actif":
            return "sold_at IS NULL"
        if status == "retire":
            return "sold_at IS NOT NULL AND sold_status='retire'"
        return "sold_at IS NOT NULL AND (sold_status='vendu' OR sold_status IS NULL)"

    if mode == "pg":
        conditions = ["niche_id=:nid", status_clause()]
        kwargs = {"nid": niche_id}
        if search:
            conditions.append("(LOWER(titre) LIKE :search1 OR LOWER(marque) LIKE :search2)")
            kwargs["search1"] = kwargs["search2"] = f"%{search.lower()}%"
        if marque:
            conditions.append("LOWER(marque)=:marque")
            kwargs["marque"] = marque.lower()
        if etat:
            conditions.append("LOWER(etat)=:etat")
            kwargs["etat"] = etat.lower()
        where = " AND ".join(conditions)
        total = conn.run(f"SELECT COUNT(*) FROM niche_items WHERE {where}", **kwargs)[0][0]
        rows = conn.run(
            f"SELECT {','.join(cols)} FROM niche_items WHERE {where} ORDER BY {order_by} LIMIT :limit OFFSET :offset",
            limit=limit, offset=offset, **kwargs)
        items = [dict(zip(cols, r)) for r in rows]
    else:
        conditions = ["niche_id=?", status_clause()]
        params = [niche_id]
        if search:
            conditions.append("(LOWER(titre) LIKE ? OR LOWER(marque) LIKE ?)")
            params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        if marque:
            conditions.append("LOWER(marque)=?")
            params.append(marque.lower())
        if etat:
            conditions.append("LOWER(etat)=?")
            params.append(etat.lower())
        where = " AND ".join(conditions)
        total = conn.execute(f"SELECT COUNT(*) FROM niche_items WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM niche_items WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        items = [dict(r) for r in rows]
    return {"items": items, "total": total}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_get_niche_history.py -v`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_get_niche_history.py
git commit -m "feat: add get_niche_history() with filters, sort and pagination"
```

---

### Task 7: `get_niche_history_facets()`

**Files:**
- Modify: `database.py`
- Create: `tests/test_get_niche_history_facets.py`

**Interfaces:**
- Produces: `get_niche_history_facets(niche_id, status) -> {"marques": [str, ...], "etats": [str, ...]}` (sorted, non-empty, distinct).

- [ ] **Step 1: Write the failing test**

Create `tests/test_get_niche_history_facets.py`:
```python
def test_facets_returns_distinct_sorted_non_empty_values(db):
    items = [
        {"id": 1, "titre": "A", "prix": 10.0, "photo": "", "url": "https://vinted.fr/items/1",
         "marque": "Zara", "taille": "M", "nb_favoris": 0, "etat": "Bon état"},
        {"id": 2, "titre": "B", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/2",
         "marque": "Lulu", "taille": "M", "nb_favoris": 0, "etat": "Bon état"},
        {"id": 3, "titre": "C", "prix": 30.0, "photo": "", "url": "https://vinted.fr/items/3",
         "marque": "", "taille": "M", "nb_favoris": 0, "etat": ""},
    ]
    db.upsert_niche_items(1, items)
    facets = db.get_niche_history_facets(1, "actif")
    assert facets["marques"] == ["Lulu", "Zara"]
    assert facets["etats"] == ["Bon état"]


def test_facets_are_scoped_to_the_requested_status(db):
    items = [
        {"id": 1, "titre": "A", "prix": 10.0, "photo": "", "url": "https://vinted.fr/items/1",
         "marque": "Zara", "taille": "M", "nb_favoris": 0, "etat": "Bon état"},
    ]
    db.upsert_niche_items(1, items)
    db.set_niche_item_sold(1, 1, "vendu")
    assert db.get_niche_history_facets(1, "actif")["marques"] == []
    assert db.get_niche_history_facets(1, "vendu")["marques"] == ["Zara"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_get_niche_history_facets.py -v`
Expected: FAIL with `AttributeError: module 'database' has no attribute 'get_niche_history_facets'`

- [ ] **Step 3: Implement**

In `database.py`, add:
```python
def get_niche_history_facets(niche_id: int, status: str) -> dict:
    """Distinct, non-empty marque/etat values available within one history
    category — used to populate the frontend's filter dropdowns."""
    conn, mode = get_conn()
    if status == "actif":
        where = "sold_at IS NULL"
    elif status == "retire":
        where = "sold_at IS NOT NULL AND sold_status='retire'"
    else:
        where = "sold_at IS NOT NULL AND (sold_status='vendu' OR sold_status IS NULL)"

    if mode == "pg":
        marques = conn.run(
            f"SELECT DISTINCT marque FROM niche_items WHERE niche_id=:nid AND {where} "
            f"AND marque IS NOT NULL AND marque != '' ORDER BY marque", nid=niche_id)
        etats = conn.run(
            f"SELECT DISTINCT etat FROM niche_items WHERE niche_id=:nid AND {where} "
            f"AND etat IS NOT NULL AND etat != '' ORDER BY etat", nid=niche_id)
        return {"marques": [r[0] for r in marques], "etats": [r[0] for r in etats]}

    marques = conn.execute(
        f"SELECT DISTINCT marque FROM niche_items WHERE niche_id=? AND {where} "
        f"AND marque IS NOT NULL AND marque != '' ORDER BY marque", (niche_id,)).fetchall()
    etats = conn.execute(
        f"SELECT DISTINCT etat FROM niche_items WHERE niche_id=? AND {where} "
        f"AND etat IS NOT NULL AND etat != '' ORDER BY etat", (niche_id,)).fetchall()
    return {"marques": [r[0] for r in marques], "etats": [r[0] for r in etats]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_get_niche_history_facets.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_get_niche_history_facets.py
git commit -m "feat: add get_niche_history_facets() for filter dropdowns"
```

---

### Task 8: `export_niche_history_csv()` streaming generator

**Files:**
- Modify: `database.py`
- Create: `tests/test_export_niche_history_csv.py`

**Interfaces:**
- Consumes: `get_niche_history` (Task 6).
- Produces: `export_niche_history_csv(niche_id, status, search=None, marque=None, etat=None)` — generator yielding CSV text chunks (header first, then one chunk per row), for `StreamingResponse`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_export_niche_history_csv.py`:
```python
import csv
import io


def test_export_yields_header_and_matching_rows(db):
    items = [
        {"id": 1, "titre": "Robe", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/1",
         "marque": "Zara", "taille": "M", "nb_favoris": 3, "etat": "Neuf avec étiquette"},
        {"id": 2, "titre": "Sac", "prix": 50.0, "photo": "", "url": "https://vinted.fr/items/2",
         "marque": "Lulu", "taille": "U", "nb_favoris": 1, "etat": "Bon état"},
    ]
    db.upsert_niche_items(1, items)
    db.set_niche_item_sold(1, 1, "vendu")
    db.set_niche_item_sold(1, 2, "retire")

    chunks = list(db.export_niche_history_csv(1, "vendu"))
    text = "".join(chunks)
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0] == ["Titre", "Prix", "État", "Marque", "Favoris", "Date d'ajout", "Date de vente"]
    assert len(rows) == 2
    assert rows[1][0] == "Robe"
    assert rows[1][3] == "Zara"
    assert rows[1][4] == "3"


def test_export_handles_more_rows_than_one_batch(db):
    items = [
        {"id": i, "titre": f"Item {i}", "prix": float(i), "photo": "", "url": f"https://vinted.fr/items/{i}",
         "marque": "M", "taille": "M", "nb_favoris": 0, "etat": ""}
        for i in range(1, 251)
    ]
    db.upsert_niche_items(1, items)
    text = "".join(db.export_niche_history_csv(1, "actif"))
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == 251  # header + 250 items, batch_size=200 must not drop any
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_niche_history_csv.py -v`
Expected: FAIL with `AttributeError: module 'database' has no attribute 'export_niche_history_csv'`

- [ ] **Step 3: Implement**

In `database.py`, add (near the top, with the other imports, add `csv, io`):
```python
import csv, io
```
Then add the generator and its date helper:
```python
def _format_csv_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except Exception:
        return iso


def export_niche_history_csv(niche_id: int, status: str, search: str = None,
                              marque: str = None, etat: str = None):
    """Yield CSV text chunks (header first) for every row matching the filters —
    no pagination, fetched in batches of 200 so the full result set is never
    held in memory at once."""
    buf = io.StringIO()
    csv.writer(buf).writerow(["Titre", "Prix", "État", "Marque", "Favoris", "Date d'ajout", "Date de vente"])
    yield buf.getvalue()

    batch_size = 200
    offset = 0
    while True:
        result = get_niche_history(niche_id, status, search=search, marque=marque, etat=etat,
                                    sort="recent", limit=batch_size, offset=offset)
        items = result["items"]
        if not items:
            break
        for it in items:
            buf = io.StringIO()
            csv.writer(buf).writerow([
                it.get("titre", ""), it.get("prix", ""), it.get("etat", ""),
                it.get("marque", ""), it.get("nb_favoris", 0),
                _format_csv_date(it.get("first_seen")),
                _format_csv_date(it.get("sold_at")) if it.get("sold_at") else "",
            ])
            yield buf.getvalue()
        offset += batch_size
        if len(items) < batch_size:
            break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_niche_history_csv.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_export_niche_history_csv.py
git commit -m "feat: add export_niche_history_csv() streaming generator"
```

---

### Task 9: API endpoints

**Files:**
- Modify: `api.py`
- Create: `tests/test_api_history.py`

**Interfaces:**
- Consumes: `get_niche_history`, `get_niche_history_facets`, `export_niche_history_csv` (Tasks 6-8), `list_user_niches` (existing), `get_subscribed_user` (existing dependency).
- Produces: `GET /niches/{niche_id}/history`, `GET /niches/{niche_id}/history/facets`, `GET /niches/{niche_id}/history/export`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_history.py`:
```python
from fastapi.testclient import TestClient
import api


def _client():
    api.app.dependency_overrides[api.get_subscribed_user] = lambda: {"id": "user-123", "email": "u@test.com"}
    return TestClient(api.app)


def _make_niche(db, user_id="user-123"):
    conn, mode = db.get_conn()
    cur = conn.execute(
        "INSERT INTO niches (user_id, nom, lien, created_le) VALUES (?,?,?,?)",
        (user_id, "Test", "https://vinted.fr/catalog", "2026-01-01T00:00:00"),
    )
    conn.commit()
    return cur.lastrowid


def test_history_endpoint_returns_items_for_owner(db):
    niche_id = _make_niche(db)
    db.upsert_niche_items(niche_id, [{
        "id": 1, "titre": "Robe", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/1",
        "marque": "Zara", "taille": "M", "nb_favoris": 3, "etat": "Neuf avec étiquette",
    }])
    db.set_niche_item_sold(niche_id, 1, "vendu")

    client = _client()
    r = client.get(f"/niches/{niche_id}/history", params={"status": "vendu"})
    api.app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["titre"] == "Robe"


def test_history_endpoint_rejects_other_users_niche(db):
    niche_id = _make_niche(db, user_id="someone-else")
    client = _client()
    r = client.get(f"/niches/{niche_id}/history", params={"status": "vendu"})
    api.app.dependency_overrides.clear()
    assert r.status_code == 403


def test_history_endpoint_rejects_invalid_status(db):
    niche_id = _make_niche(db)
    client = _client()
    r = client.get(f"/niches/{niche_id}/history", params={"status": "bogus"})
    api.app.dependency_overrides.clear()
    assert r.status_code == 422


def test_facets_endpoint(db):
    niche_id = _make_niche(db)
    db.upsert_niche_items(niche_id, [{
        "id": 1, "titre": "Robe", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/1",
        "marque": "Zara", "taille": "M", "nb_favoris": 3, "etat": "Neuf avec étiquette",
    }])
    client = _client()
    r = client.get(f"/niches/{niche_id}/history/facets", params={"status": "actif"})
    api.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"marques": ["Zara"], "etats": ["Neuf avec étiquette"]}


def test_export_endpoint_returns_csv(db):
    niche_id = _make_niche(db)
    db.upsert_niche_items(niche_id, [{
        "id": 1, "titre": "Robe", "prix": 20.0, "photo": "", "url": "https://vinted.fr/items/1",
        "marque": "Zara", "taille": "M", "nb_favoris": 3, "etat": "Neuf avec étiquette",
    }])
    db.set_niche_item_sold(niche_id, 1, "vendu")
    client = _client()
    r = client.get(f"/niches/{niche_id}/history/export", params={"status": "vendu"})
    api.app.dependency_overrides.clear()
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "Robe" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_history.py -v`
Expected: FAIL with `404 Not Found` (routes don't exist yet).

- [ ] **Step 3: Implement**

In `api.py`, add `StreamingResponse` to the existing import line:
```python
from fastapi.responses import JSONResponse
```
becomes:
```python
from fastapi.responses import JSONResponse, StreamingResponse
```
Then add the 3 endpoints right after the existing `niches_stats` endpoint (after line ~551, before `niches_delete`):
```python
@app.get("/niches/{niche_id}/history")
async def niches_history(
    niche_id: int,
    status: str = Query(..., pattern="^(actif|vendu|retire)$"),
    search: str = Query(None),
    marque: str = Query(None),
    etat: str = Query(None),
    sort: str = Query("recent"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_subscribed_user),
):
    try:
        from database import get_niche_history, list_user_niches
        user_niches = list_user_niches(user["id"])
        if not any(n["id"] == niche_id for n in user_niches):
            return JSONResponse(status_code=403, content={"error": "Niche introuvable"})
        return get_niche_history(niche_id, status, search=search, marque=marque, etat=etat, sort=sort, limit=limit, offset=offset)
    except Exception as e:
        log.error(f"niches_history: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur interne"})


@app.get("/niches/{niche_id}/history/facets")
async def niches_history_facets(
    niche_id: int,
    status: str = Query(..., pattern="^(actif|vendu|retire)$"),
    user: dict = Depends(get_subscribed_user),
):
    try:
        from database import get_niche_history_facets, list_user_niches
        user_niches = list_user_niches(user["id"])
        if not any(n["id"] == niche_id for n in user_niches):
            return JSONResponse(status_code=403, content={"error": "Niche introuvable"})
        return get_niche_history_facets(niche_id, status)
    except Exception as e:
        log.error(f"niches_history_facets: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur interne"})


@app.get("/niches/{niche_id}/history/export")
async def niches_history_export(
    niche_id: int,
    status: str = Query(..., pattern="^(actif|vendu|retire)$"),
    search: str = Query(None),
    marque: str = Query(None),
    etat: str = Query(None),
    user: dict = Depends(get_subscribed_user),
):
    from database import export_niche_history_csv, list_user_niches
    user_niches = list_user_niches(user["id"])
    if not any(n["id"] == niche_id for n in user_niches):
        return JSONResponse(status_code=403, content={"error": "Niche introuvable"})
    return StreamingResponse(
        export_niche_history_csv(niche_id, status, search=search, marque=marque, etat=etat),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="historique-{niche_id}-{status}.csv"'},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_history.py -v`
Expected: `5 passed`

- [ ] **Step 5: Run the full backend test suite**

Run: `pytest -v`
Expected: all tests pass (Tasks 1-9 combined).

- [ ] **Step 6: Commit**

```bash
git add api.py tests/test_api_history.py
git commit -m "feat: add /niches/{id}/history, /history/facets, /history/export endpoints"
```

- [ ] **Step 7: Push**

```bash
git push origin main
```

---

## Frontend tasks (`vintedspy-frontend`)

### Task 10: Tab + panel markup

**Files:**
- Modify: `app/index.html` (HTML only — tab button, panel skeleton)

**Interfaces:**
- Produces: DOM elements `nd-tab-history`, `nd-panel-history`, and inside it: `nd-history-subtab-ventes|retires|actifs`, `nd-history-search`, `nd-history-marque`, `nd-history-etat`, `nd-history-sort`, `nd-history-export-btn`, `nd-history-list`, `nd-history-sentinel`, `nd-history-empty`.

- [ ] **Step 1: Add the 4th tab button**

In `app/index.html`, the tabs block currently reads (around line 900):
```html
      <!-- Tabs -->
      <div class="flex gap-0" style="border-bottom:1px solid var(--border);">
        <button id="nd-tab-stats" onclick="switchDetailTab('stats')" class="tab-btn tab-btn-sm active">Statistiques</button>
        <button id="nd-tab-grid" onclick="switchDetailTab('grid')" class="tab-btn tab-btn-sm">Annonces</button>
        <button id="nd-tab-table" onclick="switchDetailTab('table')" class="tab-btn tab-btn-sm">Tableau</button>
      </div>
```
Add a 4th button:
```html
      <!-- Tabs -->
      <div class="flex gap-0" style="border-bottom:1px solid var(--border);">
        <button id="nd-tab-stats" onclick="switchDetailTab('stats')" class="tab-btn tab-btn-sm active">Statistiques</button>
        <button id="nd-tab-grid" onclick="switchDetailTab('grid')" class="tab-btn tab-btn-sm">Annonces</button>
        <button id="nd-tab-table" onclick="switchDetailTab('table')" class="tab-btn tab-btn-sm">Tableau</button>
        <button id="nd-tab-history" onclick="switchDetailTab('history')" class="tab-btn tab-btn-sm">Historique des ventes</button>
      </div>
```

- [ ] **Step 2: Add the panel skeleton**

Right after the closing `</div>` of `nd-panel-stats` (the stats tab's content — find it by searching for the end of the block that starts at `<div id="nd-panel-stats" ...>`, which ends with the `Distribution des prix de vente` card's closing `</div>` followed by the page's closing `</div></div>`), add a sibling panel:
```html
    <!-- Tab: Historique des ventes -->
    <div id="nd-panel-history" class="hidden flex-1 p-5 md:p-6 overflow-auto">
      <div class="flex gap-0 mb-4" style="border-bottom:1px solid var(--border);">
        <button id="nd-history-subtab-vendu" onclick="switchHistorySubtab('vendu')" class="tab-btn tab-btn-sm active">Ventes</button>
        <button id="nd-history-subtab-retire" onclick="switchHistorySubtab('retire')" class="tab-btn tab-btn-sm">Retirés</button>
        <button id="nd-history-subtab-actif" onclick="switchHistorySubtab('actif')" class="tab-btn tab-btn-sm">Invendus</button>
      </div>

      <div class="flex flex-wrap gap-2 mb-4">
        <input id="nd-history-search" type="text" placeholder="Titre, marque…"
               oninput="historyFilterChange()"
               class="px-3 py-2 rounded-xl text-sm" style="border:1px solid var(--border);background:var(--card);min-width:180px;flex:1;">
        <select id="nd-history-marque" onchange="historyFilterChange()" class="px-3 py-2 rounded-xl text-sm" style="border:1px solid var(--border);background:var(--card);">
          <option value="">Toutes les marques</option>
        </select>
        <select id="nd-history-etat" onchange="historyFilterChange()" class="px-3 py-2 rounded-xl text-sm" style="border:1px solid var(--border);background:var(--card);">
          <option value="">Tous les états</option>
        </select>
        <select id="nd-history-sort" onchange="historyFilterChange()" class="px-3 py-2 rounded-xl text-sm" style="border:1px solid var(--border);background:var(--card);">
          <option value="recent">Plus récent</option>
          <option value="prix_asc">Prix croissant</option>
          <option value="prix_desc">Prix décroissant</option>
        </select>
        <button id="nd-history-export-btn" onclick="exportNicheHistory()" class="btn-ghost px-3 py-2 text-sm rounded-xl">
          <span class="material-symbols-outlined text-[14px]">download</span> Exporter
        </button>
      </div>

      <div id="nd-history-list" class="space-y-3"></div>
      <p id="nd-history-empty" class="hidden text-center py-10 text-sm" style="color:var(--muted);"></p>
      <div id="nd-history-sentinel" style="height:1px;"></div>
    </div>
```

- [ ] **Step 3: Manually verify the markup renders**

Open `app/index.html` in a browser (or run the project's existing local dev flow), open any tracker, and confirm a 4th tab "Historique des ventes" appears and clicking it shows an (empty, non-wired) panel with 3 sub-tab buttons and the toolbar. No JS errors expected yet for the tab switch itself (the `switchHistorySubtab`/`historyFilterChange`/`exportNicheHistory` functions don't exist yet — clicking those will throw in the console, which is expected until Tasks 11-12 land; just confirm the tab button and panel layout look right).

- [ ] **Step 4: Commit**

```bash
cd /Users/jean/Desktop/vintedspy-frontend
git add app/index.html
git commit -m "Ajouter le squelette de l'onglet Historique des ventes (markup)"
git push origin main
```

---

### Task 11: Tab wiring + infinite-scroll list

**Files:**
- Modify: `app/index.html` (JS only)

**Interfaces:**
- Consumes: backend `GET /niches/{id}/history`, `GET /niches/{id}/history/facets` (Task 9). Existing helpers `esc()`, `safeUrl()`, `getEmoji()`, `authHeader()`, `fetchWithTimeout()`, `API` constant, `_currentNicheDetailId`.
- Produces: `switchDetailTab('history')` support (extends existing function), `switchHistorySubtab(status)`, `historyFilterChange()`, `resetNicheHistory()`, `loadNicheHistoryPage()`.

- [ ] **Step 1: Extend `switchDetailTab` to show/hide the new panel**

In `app/index.html`, `switchDetailTab` currently reads:
```javascript
function switchDetailTab(tab) {
  document.getElementById('nd-panel-grid').classList.toggle('hidden', tab !== 'grid');
  document.getElementById('nd-panel-table').classList.toggle('hidden', tab !== 'table');
  document.getElementById('nd-panel-stats').classList.toggle('hidden', tab !== 'stats');
  document.getElementById('nd-tab-grid').classList.toggle('active', tab === 'grid');
  document.getElementById('nd-tab-table').classList.toggle('active', tab === 'table');
  document.getElementById('nd-tab-stats').classList.toggle('active', tab === 'stats');
  if (tab === 'stats') loadNicheStats();
}
```
Replace with:
```javascript
function switchDetailTab(tab) {
  document.getElementById('nd-panel-grid').classList.toggle('hidden', tab !== 'grid');
  document.getElementById('nd-panel-table').classList.toggle('hidden', tab !== 'table');
  document.getElementById('nd-panel-stats').classList.toggle('hidden', tab !== 'stats');
  document.getElementById('nd-panel-history').classList.toggle('hidden', tab !== 'history');
  document.getElementById('nd-tab-grid').classList.toggle('active', tab === 'grid');
  document.getElementById('nd-tab-table').classList.toggle('active', tab === 'table');
  document.getElementById('nd-tab-stats').classList.toggle('active', tab === 'stats');
  document.getElementById('nd-tab-history').classList.toggle('active', tab === 'history');
  if (tab === 'stats') loadNicheStats();
  if (tab === 'history') resetNicheHistory();
}
```

- [ ] **Step 2: Add history state + sub-tab/filter handlers**

Add this block right after the `formatNicheStatsDuration` function (defined earlier in the file, near `loadNicheStats`):
```javascript
let historyStatus = 'vendu', historyOffset = 0, historyLoading = false, historyDone = false, historyTimer = null;

function switchHistorySubtab(status) {
  historyStatus = status;
  ['vendu', 'retire', 'actif'].forEach(s =>
    document.getElementById('nd-history-subtab-' + s).classList.toggle('active', s === status));
  loadNicheHistoryFacets();
  resetNicheHistory();
}

function historyFilterChange() {
  clearTimeout(historyTimer);
  historyTimer = setTimeout(resetNicheHistory, 400);
}

function resetNicheHistory() {
  historyOffset = 0;
  historyDone = false;
  document.getElementById('nd-history-list').innerHTML = '';
  document.getElementById('nd-history-empty').classList.add('hidden');
  loadNicheHistoryPage();
}
```

- [ ] **Step 3: Add the facets loader**

Add right after `resetNicheHistory`:
```javascript
async function loadNicheHistoryFacets() {
  const marqueSel = document.getElementById('nd-history-marque');
  const etatSel = document.getElementById('nd-history-etat');
  const prevMarque = marqueSel.value, prevEtat = etatSel.value;
  try {
    const r = await fetchWithTimeout(
      `${API}/niches/${_currentNicheDetailId}/history/facets?status=${historyStatus}`,
      { headers: await authHeader() });
    const facets = await r.json();
    marqueSel.innerHTML = '<option value="">Toutes les marques</option>' +
      (facets.marques || []).map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    etatSel.innerHTML = '<option value="">Tous les états</option>' +
      (facets.etats || []).map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join('');
    if (facets.marques?.includes(prevMarque)) marqueSel.value = prevMarque;
    if (facets.etats?.includes(prevEtat)) etatSel.value = prevEtat;
  } catch {}
}
```

- [ ] **Step 4: Add the card renderer**

Add right after `loadNicheHistoryFacets`:
```javascript
function renderHistoryCard(a) {
  const ajoutLabel = formatNicheDate(a.first_seen) || '—';
  const venteLabel = formatNicheDate(a.sold_at);
  return `<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener"
      class="vs-card rounded-xl p-3 flex gap-3 hover:-translate-y-0.5 transition-transform">
    <div class="flex-shrink-0 rounded-lg overflow-hidden" style="width:72px;height:72px;">
      ${safeUrl(a.photo) ? `<img src="${esc(safeUrl(a.photo))}" class="w-full h-full object-cover">`
        : `<div class="w-full h-full flex items-center justify-center text-2xl" style="background:var(--hi);">${getEmoji(a.marque, a.titre)}</div>`}
    </div>
    <div class="flex-1 min-w-0">
      <p class="text-sm font-semibold truncate">${esc(a.titre) || '—'}</p>
      <div class="flex items-center gap-2 mt-0.5">
        <span class="text-base font-bold" style="color:var(--pri);">${a.prix}€</span>
        ${a.etat ? `<span class="tag" style="color:var(--green);">${esc(a.etat)}</span>` : ''}
      </div>
      <p class="text-xs mt-1" style="color:var(--muted);">
        ${a.marque ? esc(a.marque) : ''}${a.taille ? ' · ' + esc(a.taille) : ''}
      </p>
      <div class="flex items-center gap-3 mt-1 text-xs" style="color:var(--muted);">
        <span>♡ ${a.nb_favoris || 0}</span>
        <span>Ajouté le ${ajoutLabel}</span>
        ${venteLabel ? `<span>Vendu le ${venteLabel}</span>` : ''}
      </div>
    </div>
  </a>`;
}
```

- [ ] **Step 5: Add the paginated loader + infinite-scroll observer**

Add right after `renderHistoryCard`:
```javascript
async function loadNicheHistoryPage() {
  if (historyLoading || historyDone) return;
  historyLoading = true;
  const params = new URLSearchParams({
    status: historyStatus,
    sort: document.getElementById('nd-history-sort').value,
    limit: 30,
    offset: historyOffset,
  });
  const search = document.getElementById('nd-history-search').value.trim();
  const marque = document.getElementById('nd-history-marque').value;
  const etat = document.getElementById('nd-history-etat').value;
  if (search) params.set('search', search);
  if (marque) params.set('marque', marque);
  if (etat) params.set('etat', etat);
  try {
    const r = await fetchWithTimeout(
      `${API}/niches/${_currentNicheDetailId}/history?${params}`,
      { headers: await authHeader() });
    const data = await r.json();
    const items = data.items || [];
    historyOffset += items.length;
    if (items.length < 30) historyDone = true;
    const list = document.getElementById('nd-history-list');
    items.forEach(a => list.insertAdjacentHTML('beforeend', renderHistoryCard(a)));
    const emptyEl = document.getElementById('nd-history-empty');
    if (list.children.length === 0) {
      const messages = { vendu: 'Aucune vente enregistrée.', retire: 'Aucun retrait détecté.', actif: 'Aucune annonce active.' };
      emptyEl.textContent = messages[historyStatus];
      emptyEl.classList.remove('hidden');
    } else {
      emptyEl.classList.add('hidden');
    }
  } catch {
    historyDone = true;
  } finally {
    historyLoading = false;
  }
}

const historyObserver = new IntersectionObserver(entries => {
  if (entries[0].isIntersecting && !document.getElementById('nd-panel-history').classList.contains('hidden')) {
    loadNicheHistoryPage();
  }
}, { rootMargin: '300px' });
historyObserver.observe(document.getElementById('nd-history-sentinel'));
```
This reuses the exact same `IntersectionObserver` pattern as `feedObserver`/`loadFeedPage` (already in the codebase), and reuses `formatNicheDate` (already added earlier in this project for the tracker creation-date badge).

- [ ] **Step 6: Manual verification**

1. Run the frontend locally (open `app/index.html` directly in a browser, or via the project's existing static-serve flow) against the live `https://api.trakx.fr` backend (Task 9 must be deployed first).
2. Open a tracker that has at least one sold and one active item.
3. Click "Historique des ventes" — confirm "Ventes" sub-tab loads cards with photo/titre/prix/état/marque/favoris/dates.
4. Switch to "Retirés" and "Invendus" — confirm each loads its own filtered set (or the correct empty-state message).
5. Type in the search box — confirm the list refilters after ~400ms without a full page reload.
6. Pick a marque/état filter and a sort option — confirm the list updates.
7. Scroll to the bottom of a tracker with more than 30 matching items — confirm more cards load automatically.

- [ ] **Step 7: Commit**

```bash
cd /Users/jean/Desktop/vintedspy-frontend
git add app/index.html
git commit -m "Brancher l'onglet Historique des ventes : sous-onglets, filtres, tri et scroll infini"
git push origin main
```

---

### Task 12: CSV export button

**Files:**
- Modify: `app/index.html` (JS only)

**Interfaces:**
- Consumes: `GET /niches/{id}/history/export` (Task 9), `authHeader()`.
- Produces: `exportNicheHistory()` (already referenced by the `onclick` added in Task 10, Step 2).

- [ ] **Step 1: Implement the authenticated download**

Add right after `loadNicheHistoryPage`/`historyObserver` block:
```javascript
async function exportNicheHistory() {
  const btn = document.getElementById('nd-history-export-btn');
  btn.disabled = true;
  try {
    const params = new URLSearchParams({ status: historyStatus });
    const search = document.getElementById('nd-history-search').value.trim();
    const marque = document.getElementById('nd-history-marque').value;
    const etat = document.getElementById('nd-history-etat').value;
    if (search) params.set('search', search);
    if (marque) params.set('marque', marque);
    if (etat) params.set('etat', etat);
    const r = await fetch(`${API}/niches/${_currentNicheDetailId}/history/export?${params}`, {
      headers: await authHeader(),
    });
    if (!r.ok) throw new Error('export failed');
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `historique-${_currentNicheDetailId}-${historyStatus}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch {
    showToast('Export impossible, réessaie.', 'error');
  } finally {
    btn.disabled = false;
  }
}
```
This deliberately uses a plain `fetch()` (not `fetchWithTimeout`, which applies a default timeout meant for JSON API calls) since the response is a file download, not bound by the same latency expectations, and needs the `Authorization` header that a plain `<a href>` navigation cannot send.

- [ ] **Step 2: Manual verification**

1. Open "Historique des ventes" on a tracker with at least one sold item.
2. Click "Exporter" — confirm a `.csv` file downloads.
3. Open the downloaded file — confirm the header row and rows match what's on screen (Titre, Prix, État, Marque, Favoris, Date d'ajout, Date de vente).
4. Switch to "Invendus" and export again — confirm "Date de vente" is empty for every row.

- [ ] **Step 3: Commit**

```bash
cd /Users/jean/Desktop/vintedspy-frontend
git add app/index.html
git commit -m "Ajouter l'export CSV de l'historique des ventes"
git push origin main
```

---

### Task 13: Full manual QA pass

**Files:** none (verification only)

- [ ] **Step 1: Run the spec's full test checklist end-to-end**

Against the deployed backend + frontend (after Vercel/Render redeploy from the pushes in Tasks 9, 11, 12):
1. Open a tracker with a mix of sold, withdrawn, and active items (if no such tracker exists yet in production, wait for at least one scan cycle after deploying Task 9, or use a tracker that already has historical `sold_at` data — those will show under "Ventes" by the legacy-fallback rule).
2. Confirm all 3 sub-tabs (Ventes / Retirés / Invendus) show plausible data and correct empty-state copy when empty.
3. Confirm search, marque filter, état filter, and sort all behave correctly and combine (e.g. search + marque filter together).
4. Confirm infinite scroll loads further pages without duplicating or skipping items.
5. Confirm CSV export downloads the full filtered set, not just the currently-loaded page (test with a tracker that has more than 30 matching items, if available).
6. Confirm no console errors when switching between the 4 main tabs repeatedly.

- [ ] **Step 2: Report results**

Note any discrepancies found against the spec (`docs/superpowers/specs/2026-06-22-historique-des-ventes-design.md`) before considering this feature complete.
