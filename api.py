"""
VintedSpy — API FastAPI
"""
from fastapi import FastAPI, Query, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sys, httpx
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

app = FastAPI(title="VintedSpy API", version="1.0.0")

ALLOWED_ORIGINS = [
    "https://vintedspy.vercel.app",
    "http://localhost:3000",
    "http://localhost:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = "https://apwedqsklyzroeyrokqb.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_ExNINsgU98WsaiqBeW0x-A_HXqQFLz1"

async def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise")
    token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
            timeout=10,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée")
    return r.json()

@app.get("/")
def root():
    return {"status": "ok", "app": "VintedSpy API"}

@app.get("/opportunites")
def opportunites(limit: int = Query(20, ge=1, le=100)):
    try:
        from database import get_opportunites
        return get_opportunites(limit)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/stats")
def stats():
    try:
        from database import stats_db
        return stats_db()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/annonces")
def annonces(
    marque: str = Query(None),
    prix_max: float = Query(None),
    score_min: int = Query(30),
    limit: int = Query(20)
):
    try:
        from database import get_opportunites
        opps = get_opportunites(100)
        if marque:
            opps = [o for o in opps if marque.lower() in (o.get("marque") or "").lower()]
        if prix_max:
            opps = [o for o in opps if o["prix"] <= prix_max]
        opps = [o for o in opps if o.get("score", 0) >= score_min]
        return opps[:limit]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/feed")
def feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(40, ge=1, le=100),
    marque: str = Query(None),
    taille: str = Query(None),
    score_min: int = Query(None, ge=0, le=100),
    prix_min: float = Query(None),
    prix_max: float = Query(None),
    search: str = Query(None),
    order: str = Query("recent"),
):
    try:
        from database import get_feed_annonces
        return get_feed_annonces(offset=offset, limit=limit, marque=marque,
                                  taille=taille, score_min=score_min,
                                  prix_min=prix_min, prix_max=prix_max,
                                  search=search, order=order)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user.get("id"), "email": user.get("email")}


@app.get("/ping")
def ping():
    try:
        from database import stats_db
        s = stats_db()
        return {"status": "ok", "annonces": s.get("annonces", 0), "prix_history": s.get("prix_history", 0)}
    except Exception as e:
        return {"status": "db_error", "error": str(e)}
