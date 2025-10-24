import os, io, base64, time, hmac, hashlib, math
from fastapi import FastAPI, Request, HTTPException
import httpx
from PIL import Image
import numpy as np
from sklearn.cluster import KMeans

app = FastAPI()

WOO_BASE = os.getenv("WOO_BASE_URL", "https://ooblehome.com").rstrip("/")
WOO_KEY = os.getenv("WOO_KEY")
WOO_SECRET = os.getenv("WOO_SECRET")
HMAC_SECRET = os.getenv("ACTION_HMAC_SECRET", "test").encode()

_cache_products = {"ts": 0, "items": []}
_cache_colors = {}

def verify_hmac(body: bytes, sig: str):
    mac = hmac.new(HMAC_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, sig.lower()):
        raise HTTPException(401, "Bad signature")

async def fetch_products():
    now = time.time()
    if now - _cache_products["ts"] < 600 and _cache_products["items"]:
        return _cache_products["items"]
    items = []
    page = 1
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            r = await client.get(
                f"{WOO_BASE}/wp-json/wc/v3/products",
                params={
                    "consumer_key": WOO_KEY,
                    "consumer_secret": WOO_SECRET,
                    "status": "publish",
                    "stock_status": "instock",
                    "per_page": 100,
                    "page": page,
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            items += batch
            if len(batch) < 100:
                break
            page += 1
    _cache_products["ts"] = now
    _cache_products["items"] = items
    return items

def rgb_to_hex(rgb): return "#{:02x}{:02x}{:02x}".format(*rgb)

async def get_dominant_hex(url):
    if url in _cache_colors:
        return _cache_colors[url]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im = im.resize((224, 224))
            arr = np.array(im).reshape(-1, 3)
            km = KMeans(n_clusters=4, n_init="auto").fit(arr)
            labels, counts = np.unique(km.labels_, return_counts=True)
            dominant = km.cluster_centers_[counts.argmax()].astype(int)
            hexv = rgb_to_hex(tuple(dominant))
    except Exception:
        hexv = "#aaaaaa"
    _cache_colors[url] = hexv
    return hexv

@app.post("/select-products")
async def select_products(request: Request):
    sig = request.headers.get("X-Ooble-Signature")
    if not sig: raise HTTPException(401, "Missing signature")
    body = await request.body()
    verify_hmac(body, sig)
    data = await request.json()
    theme = data.get("theme_brief", "")
    products = await fetch_products()
    results = []
    for p in products:
        imgs = p.get("images") or []
        if not imgs: continue
        url = imgs[0]["src"]
        hexv = await get_dominant_hex(url)
        results.append({
            "title": p["name"],
            "category": (p.get("categories") or [{}])[0].get("name",""),
            "product_url": p["permalink"],
            "image_url": url,
            "dominant_hex": hexv
        })
    html_tiles = "".join([
        f"<article><img src='{r['image_url']}' alt='{r['title']}' style='width:100%;height:200px;object-fit:cover;border-radius:8px'><a href='{r['product_url']}' target='_blank'>View</a><a href='{r['image_url']}' target='_blank'>Get image</a></article>"
        for r in results[:9]
    ])
    html = f"<section style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px'>{html_tiles}</section>"
    return {"palette_hex":["#D38C1F","#B85C38","#E2B46D"],"products":results[:9],"html":html}
