"""
VintedSpy — API FastAPI
"""
from fastapi import FastAPI, Query, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sys, httpx, os, hmac, hashlib, logging
log = logging.getLogger("api")
from pathlib import Path

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
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
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_ExNINsgU98WsaiqBeW0x-A_HXqQFLz1")

# In-memory auth cache: token → (user_dict, expires_at)
import time
_auth_cache: dict = {}
_AUTH_CACHE_TTL = 60  # seconds

async def get_current_user(authorization: str = Header(None), require_sub: bool = False) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise")
    token = authorization.split(" ", 1)[1]
    now = time.monotonic()
    cached = _auth_cache.get(token)
    if cached and cached[1] > now:
        user = cached[0]
    else:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
                timeout=10,
            )
        if r.status_code != 200:
            _auth_cache.pop(token, None)
            raise HTTPException(status_code=401, detail="Session invalide ou expirée")
        user = r.json()
        _auth_cache[token] = (user, now + _AUTH_CACHE_TTL)
        # Evict old entries to prevent memory growth
        if len(_auth_cache) > 500:
            oldest = sorted(_auth_cache.items(), key=lambda x: x[1][1])[:100]
            for k, _ in oldest:
                _auth_cache.pop(k, None)
    if require_sub:
        from database import is_subscribed
        admin_emails = {e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
        email = user.get("email", "")
        if email not in admin_emails and not is_subscribed(email):
            raise HTTPException(status_code=403, detail="Abonnement requis")
    return user

async def get_subscribed_user(authorization: str = Header(None)) -> dict:
    return await get_current_user(authorization=authorization, require_sub=True)

def _is_subscribed(user: dict) -> bool:
    from database import is_subscribed
    email = user.get("email", "")
    admin_emails = {e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    return email in admin_emails or is_subscribed(email)

@app.on_event("startup")
def on_startup():
    try:
        from database import init_db
        init_db()
    except Exception as e:
        print(f"init_db failed: {e}")

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


@app.get("/feed")
async def feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(40, ge=1, le=100),
    marque: str = Query(None),
    taille: str = Query(None),
    score_min: int = Query(None, ge=0, le=100),
    prix_min: float = Query(None),
    prix_max: float = Query(None),
    search: str = Query(None),
    order: str = Query("recent"),
    user: dict = Depends(get_current_user),
):
    try:
        from database import get_feed_annonces
        since_hours = None if _is_subscribed(user) else 24
        return get_feed_annonces(offset=offset, limit=limit, marque=marque,
                                  taille=taille, score_min=score_min,
                                  prix_min=prix_min, prix_max=prix_max,
                                  search=search, order=order, since_hours=since_hours)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/vinted/brand/{brand_id}")
async def vinted_brand(brand_id: int):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            await client.get("https://www.vinted.fr/")
            r = await client.get(f"https://www.vinted.fr/api/v2/brands/{brand_id}", timeout=10)
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "vinted_unreachable"})
        data = r.json()
        return {"id": brand_id, "title": data.get("brand", {}).get("title")}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/me")
async def me(user: dict = Depends(get_current_user)):
    email = user.get("email", "")
    subscribed = _is_subscribed(user)
    plan = "pro" if subscribed else "free"
    return {"id": user.get("id"), "email": email, "subscribed": subscribed, "plan": plan}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    # Verify Stripe signature — always required in production
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    try:
        parts = {k: v for item in sig.split(",") for k, v in [item.split("=", 1)]}
        timestamp = parts.get("t", "")
        v1 = parts.get("v1", "")
        if not timestamp or not v1:
            raise ValueError("Missing signature parts")
        signed = f"{timestamp}.{payload.decode()}"
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, v1):
            raise HTTPException(status_code=400, detail="Invalid signature")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook error")

    import json
    from database import upsert_subscription
    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        email = obj.get("customer_email") or obj.get("customer_details", {}).get("email")
        if email:
            upsert_subscription(
                user_email=email,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
                status="active",
            )

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        status = "active" if obj.get("status") == "active" else "inactive"
        cpe = None
        if obj.get("current_period_end"):
            from datetime import datetime
            cpe = datetime.fromtimestamp(obj["current_period_end"]).isoformat()
        # Need customer email — fetch it via customer ID
        customer_id = obj.get("customer")
        if customer_id and STRIPE_WEBHOOK_SECRET:
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(
                        f"https://api.stripe.com/v1/customers/{customer_id}",
                        headers={"Authorization": f"Bearer {os.getenv('STRIPE_SECRET_KEY', '')}"},
                        timeout=10,
                    )
                email = r.json().get("email") if r.status_code == 200 else None
                if email:
                    upsert_subscription(
                        user_email=email,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=obj.get("id"),
                        status=status,
                        current_period_end=cpe,
                    )
            except Exception:
                pass

    elif etype in ("customer.subscription.deleted",):
        customer_id = obj.get("customer")
        if customer_id and STRIPE_WEBHOOK_SECRET:
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(
                        f"https://api.stripe.com/v1/customers/{customer_id}",
                        headers={"Authorization": f"Bearer {os.getenv('STRIPE_SECRET_KEY', '')}"},
                        timeout=10,
                    )
                email = r.json().get("email") if r.status_code == 200 else None
                if email:
                    upsert_subscription(user_email=email, status="inactive")
            except Exception:
                pass

    return {"received": True}


# ---- NICHES ----

@app.get("/niches")
async def niches_list(user: dict = Depends(get_current_user)):
    try:
        from database import list_user_niches
        niches = list_user_niches(user["id"])
        subscribed = _is_subscribed(user)
        return {"niches": niches, "subscribed": subscribed, "free_limit": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/niches")
async def niches_create(payload: dict, user: dict = Depends(get_current_user)):
    try:
        from database import create_user_niche, list_user_niches
        nom = (payload.get("nom") or "").strip()
        if not nom:
            return JSONResponse(status_code=400, content={"error": "Nom requis"})
        if not _is_subscribed(user):
            existing = list_user_niches(user["id"])
            if len(existing) >= 1:
                return JSONResponse(status_code=403, content={"error": "free_limit"})
        return create_user_niche(
            user["id"], nom,
            marque=payload.get("marque"), taille=payload.get("taille"),
            score_min=payload.get("score_min"), prix_min=payload.get("prix_min"),
            recherche=payload.get("recherche"), lien=payload.get("lien"),
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/niches/{niche_id}/items")
async def niches_items(niche_id: int, limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    try:
        from database import get_niche_items, list_user_niches
        # Verify the niche belongs to the requesting user
        user_niches = list_user_niches(user["id"])
        if not any(n["id"] == niche_id for n in user_niches):
            return JSONResponse(status_code=403, content={"error": "Niche introuvable"})
        return get_niche_items(niche_id, limit=limit)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/niches/{niche_id}")
async def niches_delete(niche_id: int, user: dict = Depends(get_current_user)):
    try:
        from database import delete_user_niche
        ok = delete_user_niche(user["id"], niche_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Niche introuvable"})
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- SURVEILLANCE ----

@app.get("/surveillance")
async def surveillance_list(user: dict = Depends(get_subscribed_user)):
    try:
        from database import refresh_surveillance
        return refresh_surveillance(user["id"])
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/surveillance")
async def surveillance_add(payload: dict, user: dict = Depends(get_subscribed_user)):
    try:
        from database import add_surveillance
        if not payload.get("id"):
            return JSONResponse(status_code=400, content={"error": "id requis"})
        return add_surveillance(user["id"], payload)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/surveillance/{annonce_id}")
async def surveillance_remove(annonce_id: int, user: dict = Depends(get_subscribed_user)):
    try:
        from database import remove_surveillance
        remove_surveillance(user["id"], annonce_id)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


STRIPE_DEFAULT_PRICE = "9,90€/mois"
STRIPE_DEFAULT_URL = "https://buy.stripe.com/00w8wOcJObg0fIw422c7u00"

@app.get("/config")
def config_get():
    from database import get_config
    return {
        "price_display": get_config("price_display", STRIPE_DEFAULT_PRICE),
        "stripe_url": get_config("stripe_url", STRIPE_DEFAULT_URL),
        "stripe_portal_url": get_config("stripe_portal_url", ""),
    }

@app.post("/admin/config")
async def config_set(payload: dict, user: dict = Depends(get_current_user)):
    admin_emails = {e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if user.get("email") not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin requis")
    from database import set_config
    allowed = {"price_display", "stripe_url", "stripe_portal_url"}
    for k, v in payload.items():
        if k in allowed:
            set_config(k, str(v))
    return {"ok": True}

@app.get("/ping")
def ping():
    try:
        from database import stats_db
        s = stats_db()
        return {"status": "ok", "annonces": s.get("annonces", 0), "prix_history": s.get("prix_history", 0)}
    except Exception as e:
        return {"status": "db_error", "error": str(e)}
