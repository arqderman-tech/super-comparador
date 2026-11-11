import os
import threading
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import init_db, get_stats, search_products, get_top_deals, compare_basket, get_connection, get_product_history, audit_product_deal, get_supermarket_updates
import scraper
import scraper_coto
import scraper_reina
import scraper_jumbo
import scraper_laanonima

app = FastAPI(title="Super Comparador - Precios de Supermercados", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/index.html") or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Ensure static directory exists
os.makedirs(STATIC_DIR, exist_ok=True)

class BasketRequest(BaseModel):
    product_ids: List[str]

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/api/stats")
def api_stats():
    return get_stats()

@app.get("/api/updates")
def api_updates():
    return get_supermarket_updates()

@app.get("/api/categories")
def api_categories(supermarket: str = "La Gallega"):
    conn = get_connection()
    c = conn.cursor()
    norm_super = "La Gallega"
    if supermarket and isinstance(supermarket, str) and supermarket.lower() in ("coto", "cotodigital"):
        norm_super = "Coto"
    elif supermarket and isinstance(supermarket, str) and supermarket.lower() in ("carrefour", "carre"):
        norm_super = "Carrefour"
    elif supermarket and isinstance(supermarket, str) and supermarket.lower() in ("la reina", "lareina", "reina"):
        norm_super = "La Reina"

    c.execute("SELECT COUNT(*) FROM products WHERE supermarket = ?", (norm_super,))
    has_native = (c.fetchone()[0] > 0)

    if has_native:
        c.execute("""
        SELECT category, category_id, COUNT(*) as count, AVG(price_promo) as avg_price,
               SUM(CASE WHEN is_promo = 1 OR discount_pct > 0 THEN 1 ELSE 0 END) as promo_count
        FROM products
        WHERE supermarket = ?
        GROUP BY category
        ORDER BY count DESC
        """, (norm_super,))
    else:
        c.execute("""
        SELECT p.category, p.category_id, COUNT(*) as count, AVG(cp.price) as avg_price,
               SUM(CASE WHEN cp.has_promo = 1 THEN 1 ELSE 0 END) as promo_count
        FROM competitor_prices cp
        JOIN products p ON cp.product_id = p.id
        WHERE cp.supermarket = ?
        GROUP BY p.category
        ORDER BY count DESC
        """, (norm_super,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

@app.get("/api/products")
def api_products(
    q: str = "",
    category: str = "",
    promo: bool = False,
    sort: str = "relevance",
    min_price: float = 0.0,
    max_price: float = 0.0,
    page: int = 1,
    limit: int = 30,
    supermarket: str = "La Gallega"
):
    offset = (page - 1) * limit
    results = search_products(
        query=q,
        category=category,
        promo_only=promo,
        sort_by=sort,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        offset=offset,
        supermarket=supermarket
    )
    results['page'] = page
    results['total_pages'] = (results['total'] + limit - 1) // limit if results['total'] > 0 else 1
    return results

@app.get("/api/products/{product_id}")
def api_product_detail(product_id: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT id, ean, name, category, brand, price_regular, price_promo, 
           is_promo, discount_pct, image_url, url, supermarket, updated_at,
           pack_units, pack_detail
    FROM products
    WHERE id = ?
    """, (product_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    product = dict(row)
    cur_price = product.get('price_promo') or 0.0
    ean = product.get('ean')
    comps = []

    if ean:
        # Cross-match other supermarkets by EAN
        # Pre-cargar metadatos enriquecidos de competitor_prices (regular_price, promo_price calculado, promo_detail explicativo)
        c.execute("""
        SELECT supermarket, price, difference_pct, regular_price, promo_price, has_promo, promo_units, promo_detail
        FROM competitor_prices
        WHERE (product_id = ? OR ean = ?) AND supermarket != ?
        """, (product_id, ean, product['supermarket']))
        cp_map = {row['supermarket']: dict(row) for row in c.fetchall()}

        c.execute("""
        SELECT id, supermarket, price_promo, price_regular, is_promo, discount_pct, pack_units, pack_detail, url
        FROM products
        WHERE ean = ? AND supermarket != ? AND price_promo > 0
        ORDER BY price_promo ASC
        """, (ean, product['supermarket']))
        for r in c.fetchall():
            s = r['supermarket']
            cp_data = cp_map.get(s, {})
            m_price = r['price_promo']
            diff = round(((m_price - cur_price) / cur_price) * 100, 1) if cur_price else 0.0

            reg_price = cp_data.get('regular_price') or r['price_regular'] or m_price
            p_price = cp_data.get('promo_price')
            if not p_price and r['is_promo']:
                p_price = m_price if (reg_price and m_price < reg_price) else None

            p_detail = cp_data.get('promo_detail') or r['pack_detail'] or ''
            p_units = cp_data.get('promo_units') or r['pack_units'] or 1
            has_p = bool(cp_data.get('has_promo') or r['is_promo'] or (reg_price and p_price and p_price < reg_price))

            comps.append({
                'supermarket': s,
                'price': m_price,
                'difference_pct': diff,
                'regular_price': reg_price,
                'promo_price': p_price,
                'has_promo': has_p,
                'promo_units': p_units,
                'promo_detail': p_detail
            })

        # Also fallback to competitor_prices if not found in products
        for s, r in cp_map.items():
            if not any(x['supermarket'] == s for x in comps):
                m_price = r['price']
                diff = round(((m_price - cur_price) / cur_price) * 100, 1) if cur_price else 0.0
                comps.append({
                    'supermarket': s,
                    'price': m_price,
                    'difference_pct': diff,
                    'regular_price': r['regular_price'] or m_price,
                    'promo_price': r['promo_price'],
                    'has_promo': bool(r['has_promo']),
                    'promo_units': r['promo_units'] or 1,
                    'promo_detail': r['promo_detail'] or ''
                })

    comps.sort(key=lambda x: x['price'])
    product['competitors'] = comps
    conn.close()
    
    # Enrich with historical price points and audit verdict for THIS specific supermarket
    superm = product.get('supermarket') or 'La Gallega'
    hist_res = get_product_history(product.get('ean'), supermarket=superm)
    product['history'] = hist_res['history']
    product['history_stats'] = hist_res['stats']
    product['history_supermarket'] = superm
    product['audit'] = audit_product_deal(product, hist_res['stats'], hist_res['history'])
    return product

@app.get("/api/products/by-ean/{ean}")
def api_product_by_ean(ean: str):
    conn = get_connection()
    c = conn.cursor()
    clean_ean = ean.strip()

    if not clean_ean:
        conn.close()
        raise HTTPException(status_code=400, detail="Código de barras vacío")

    candidate_eans = [clean_ean]
    if len(clean_ean) == 12:
        candidate_eans.append('0' + clean_ean)
    elif len(clean_ean) == 13 and clean_ean.startswith('0'):
        stripped = clean_ean.lstrip('0')
        if stripped:
            candidate_eans.append(stripped)

    candidate_eans = [x for x in candidate_eans if x]
    found_id = None

    # 1. Search in products by EAN (prefer La Gallega if multiple, or any store with price)
    for cand in candidate_eans:
        c.execute("""
        SELECT id FROM products 
        WHERE ean = ? AND ean != '' 
        ORDER BY CASE WHEN supermarket = 'La Gallega' THEN 0 ELSE 1 END, price_promo ASC 
        LIMIT 1
        """, (cand,))
        row = c.fetchone()
        if row:
            found_id = row['id']
            break

    # 2. Search in competitor_prices table by EAN
    if not found_id:
        for cand in candidate_eans:
            c.execute("""
            SELECT product_id FROM competitor_prices 
            WHERE ean = ? AND ean != '' AND product_id IS NOT NULL AND product_id != '' 
            LIMIT 1
            """, (cand,))
            row = c.fetchone()
            if row:
                found_id = row['product_id']
                break

    # 3. Direct match on product ID
    if not found_id:
        c.execute("SELECT id FROM products WHERE id = ? LIMIT 1", (clean_ean,))
        row = c.fetchone()
        if row:
            found_id = row['id']

    # 4. Partial substring match in products EAN if >= 6 digits and not all zeroes
    if not found_id and len(clean_ean) >= 6 and not set(clean_ean) == {'0'}:
        c.execute("SELECT id FROM products WHERE ean LIKE ? AND ean != '' LIMIT 1", (f"%{clean_ean}%",))
        row = c.fetchone()
        if row:
            found_id = row['id']

    conn.close()

    if not found_id:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró ningún producto con el código '{clean_ean}'"
        )

    return api_product_detail(found_id)

@app.get("/api/products/{product_id}/history")
def api_product_history(product_id: str, supermarket: Optional[str] = None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT ean, supermarket FROM products WHERE id = ?", (product_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    ean = row['ean']
    target_super = supermarket or row['supermarket']
    return get_product_history(ean, supermarket=target_super)

@app.get("/api/history/audit")
def api_history_audit(limit: int = 30):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT id, ean, name, category, brand, price_regular, price_promo, 
           is_promo, discount_pct, image_url, url
    FROM products
    WHERE is_promo = 1 AND ean != ''
    ORDER BY discount_pct DESC
    LIMIT 150
    """)
    promos = [dict(r) for r in c.fetchall()]
    conn.close()
    
    verified_deals = []
    inflated_deals = []

    for p in promos:
        hist = get_product_history(p['ean'])
        audit = audit_product_deal(p, hist['stats'], hist['history'])
        p['audit'] = audit
        p['history_stats'] = hist['stats']
        if audit['status'] == 'VERIFIED_DEAL':
            verified_deals.append(p)
        elif audit['status'] == 'INFLATED_DEAL':
            inflated_deals.append(p)

    return {
        'verified_deals': verified_deals[:limit],
        'inflated_deals': inflated_deals[:limit],
        'total_verified': len(verified_deals),
        'total_inflated': len(inflated_deals)
    }

@app.get("/api/history/inflation")
def api_history_inflation(limit: int = 15):
    conn = get_connection()
    c = conn.cursor()
    # Find products with prices in Feb (20260220-20260228) and today
    c.execute("""
    WITH feb_prices AS (
        SELECT ean, AVG(precio) as feb_price
        FROM price_history
        WHERE fecha >= 20260220 AND fecha <= 20260228
        GROUP BY ean
    )
    SELECT p.id, p.ean, p.name, p.category, p.brand, p.price_promo as current_price,
           f.feb_price,
           ROUND(((p.price_promo - f.feb_price) / f.feb_price) * 100, 1) as inflation_pct
    FROM products p
    JOIN feb_prices f ON p.ean = f.ean
    WHERE f.feb_price > 0 AND p.price_promo > 0
    ORDER BY inflation_pct DESC
    LIMIT ?
    """, (limit,))
    top_increases = [dict(r) for r in c.fetchall()]
    
    c.execute("""
    WITH feb_prices AS (
        SELECT ean, AVG(precio) as feb_price
        FROM price_history
        WHERE fecha >= '20260220' AND fecha <= '20260228'
        GROUP BY ean
    )
    SELECT p.id, p.ean, p.name, p.category, p.brand, p.price_promo as current_price,
           f.feb_price,
           ROUND(((p.price_promo - f.feb_price) / f.feb_price) * 100, 1) as inflation_pct
    FROM products p
    JOIN feb_prices f ON p.ean = f.ean
    WHERE f.feb_price > 0 AND p.price_promo > 0
    ORDER BY inflation_pct ASC
    LIMIT ?
    """, (limit,))
    lowest_increases = [dict(r) for r in c.fetchall()]
    conn.close()
    return {
        'top_increases': top_increases,
        'lowest_increases': lowest_increases
    }

@app.get("/api/deals")
def api_deals(limit: int = 12, supermarket: str = "La Gallega"):
    return get_top_deals(limit=limit, supermarket=supermarket)

@app.post("/api/basket/compare")
def api_basket_compare(basket: BasketRequest):
    return compare_basket(basket.product_ids)

@app.get("/api/scraper/status")
def api_scraper_status():
    return scraper.current_status

@app.post("/api/scraper/start")
def api_scraper_start(max_pages: int = 50):
    if scraper.current_status["is_running"]:
        return {"status": "busy", "message": "El proceso de scraping ya está en ejecución."}

    def background_task():
        scraper.run_full_scrape(max_pages_per_cat=max_pages)

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": f"Extracción iniciada en segundo plano (máx {max_pages} págs por categoría)."}

# COTO Scraper endpoints
@app.get("/api/scraper/coto/status")
def api_coto_scraper_status():
    return {"status": "ready", "message": "Scraper de COTO disponible"}

@app.post("/api/scraper/coto/start")
def api_coto_scraper_start():
    """Inicia el scraping de COTO (filtrado por EAN de La Gallega)"""
    def background_task():
        scraper_coto.main()

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": "Extracción de precios de COTO iniciada en segundo plano."}

# Carrefour Scraper endpoints
@app.get("/api/scraper/carrefour/status")
def api_carrefour_scraper_status():
    return {"status": "ready", "message": "Scraper de Carrefour disponible"}

@app.post("/api/scraper/carrefour/start")
def api_carrefour_scraper_start():
    """Inicia el scraping y sincronización de Carrefour"""
    def background_task():
        try:
            import carrefour_scraper
            carrefour_scraper.main()
        except Exception as e:
            print(f"Error en scraping Carrefour: {e}")

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": "Extracción y sincronización de precios de Carrefour iniciada en segundo plano."}

@app.post("/api/scraper/carrefour/import")
def api_carrefour_import():
    """Sincroniza el último archivo CSV de Carrefour existente con la base de datos"""
    try:
        from import_carrefour import import_latest_carrefour
        count = import_latest_carrefour()
        return {"status": "success", "imported": count, "message": f"{count} productos de Carrefour sincronizados."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# La Reina Scraper endpoints
@app.get("/api/scraper/reina/status")
def api_reina_scraper_status():
    return {"status": "ready", "message": "Scraper de La Reina disponible"}

@app.post("/api/scraper/reina/start")
def api_reina_scraper_start():
    """Inicia el scraping de La Reina (filtrado por EAN de La Gallega)"""
    def background_task():
        import asyncio
        asyncio.run(scraper_reina.main())

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": "Extracción de precios de La Reina iniciada en segundo plano."}

# Jumbo Scraper endpoints
@app.get("/api/scraper/jumbo/status")
def api_jumbo_scraper_status():
    return scraper_jumbo.current_status

@app.post("/api/scraper/jumbo/start")
def api_jumbo_scraper_start(quick: bool = False):
    """Inicia el scraping de Jumbo (catalogo completo y cruce por EAN)"""
    if scraper_jumbo.current_status.get("is_running"):
        return {"status": "busy", "message": "El proceso de scraping de Jumbo ya está en ejecución."}

    def background_task():
        scraper_jumbo.main(quick_mode=quick)

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": "Extracción y sincronización de precios de Jumbo iniciada en segundo plano."}

# La Anonima Scraper endpoints
@app.get("/api/scraper/laanonima/status")
def api_laanonima_scraper_status():
    return scraper_laanonima.current_status

@app.post("/api/scraper/laanonima/start")
def api_laanonima_scraper_start(quick: bool = False):
    """Inicia el scraping de La Anonima (catalogo completo y cruce por EAN)"""
    if scraper_laanonima.current_status.get("is_running"):
        return {"status": "busy", "message": "El proceso de scraping de La Anonima ya está en ejecución."}

    def background_task():
        scraper_laanonima.main(quick_mode=quick)

    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()
    return {"status": "started", "message": "Extracción y sincronización de precios de La Anonima iniciada en segundo plano."}

@app.get("/api/comparator/products")
def api_comparator_products(
    q: str = Query("", description="Búsqueda por texto o EAN"),
    category: str = Query("", description="Filtrar por categoría"),
    sort: str = Query("diff_desc", description="diff_desc, diff_coto, diff_carrefour, diff_reina, diff_jumbo, diff_gallega, discount, price_asc, price_desc, name"),
    competitor: str = Query("all", description="all, coto, carrefour, reina, jumbo, laanonima, both, all_three, all_four, all_five"),
    page: int = Query(1, ge=1),
    limit: int = Query(250, ge=1, le=5000)
):
    """Devuelve productos que tienen precio en COTO y/o Carrefour con cálculo multitienda y paginación"""
    # Manejar llamadas directas sin pasar por inyección de FastAPI
    if not isinstance(q, str): q = ""
    if not isinstance(category, str): category = ""
    if not isinstance(sort, str): sort = "diff_desc"
    if not isinstance(competitor, str): competitor = "all"
    if not isinstance(page, int): page = 1
    if not isinstance(limit, int): limit = 250

    conn = get_connection()
    c = conn.cursor()

    params = []
    where_clauses = ["1=1"]

    if category:
        where_clauses.append("p.category = ?")
        params.append(category)

    if q and q.strip():
        clean_q = q.strip()
        where_clauses.append("(p.name LIKE ? OR p.ean LIKE ? OR p.id = ?)")
        search_term = f"%{clean_q}%"
        params.extend([search_term, search_term, clean_q])

    # Filtro por competidor
    if competitor == "coto":
        where_clauses.append("coto_cp.price IS NOT NULL")
    elif competitor == "carrefour":
        where_clauses.append("carr_cp.price IS NOT NULL")
    elif competitor == "reina":
        where_clauses.append("reina_cp.price IS NOT NULL")
    elif competitor == "jumbo":
        where_clauses.append("jumbo_cp.price IS NOT NULL")
    elif competitor == "both":
        where_clauses.append("coto_cp.price IS NOT NULL AND carr_cp.price IS NOT NULL")
    elif competitor == "all_three":
        where_clauses.append("coto_cp.price IS NOT NULL AND carr_cp.price IS NOT NULL AND reina_cp.price IS NOT NULL")
    elif competitor in ("laanonima", "anonima"):
        where_clauses.append("la_cp.price IS NOT NULL")
    elif competitor == "all_four":
        where_clauses.append("coto_cp.price IS NOT NULL AND carr_cp.price IS NOT NULL AND reina_cp.price IS NOT NULL AND jumbo_cp.price IS NOT NULL")
    elif competitor == "all_five":
        where_clauses.append("coto_cp.price IS NOT NULL AND carr_cp.price IS NOT NULL AND reina_cp.price IS NOT NULL AND jumbo_cp.price IS NOT NULL AND la_cp.price IS NOT NULL")
    else:  # all
        where_clauses.append("(coto_cp.price IS NOT NULL OR carr_cp.price IS NOT NULL OR reina_cp.price IS NOT NULL OR jumbo_cp.price IS NOT NULL OR la_cp.price IS NOT NULL)")

    where_sql = " AND ".join(where_clauses)

    # Order by clause
    if sort == "diff_coto":
        order_sql = "COALESCE(coto_cp.difference_pct, 9999) ASC, p.price_promo ASC"
    elif sort == "diff_carrefour":
        order_sql = "COALESCE(carr_cp.difference_pct, 9999) ASC, p.price_promo ASC"
    elif sort == "diff_reina":
        order_sql = "COALESCE(reina_cp.difference_pct, 9999) ASC, p.price_promo ASC"
    elif sort == "diff_jumbo":
        order_sql = "COALESCE(jumbo_cp.difference_pct, 9999) ASC, p.price_promo ASC"
    elif sort in ("diff_laanonima", "diff_anonima"):
        order_sql = "COALESCE(la_cp.difference_pct, 9999) ASC, p.price_promo ASC"
    elif sort == "diff_gallega":
        order_sql = "MAX(COALESCE(coto_cp.difference_pct, -9999), COALESCE(carr_cp.difference_pct, -9999), COALESCE(reina_cp.difference_pct, -9999), COALESCE(jumbo_cp.difference_pct, -9999), COALESCE(la_cp.difference_pct, -9999)) DESC, p.price_promo ASC"
    elif sort == "price_asc":
        order_sql = "p.price_promo ASC"
    elif sort == "price_desc":
        order_sql = "p.price_promo DESC"
    elif sort == "discount":
        order_sql = "p.discount_pct DESC, p.price_promo ASC"
    elif sort == "name":
        order_sql = "p.name ASC"
    else:  # diff_desc
        order_sql = "MAX(COALESCE(ABS(coto_cp.difference_pct), 0), COALESCE(ABS(carr_cp.difference_pct), 0), COALESCE(ABS(reina_cp.difference_pct), 0), COALESCE(ABS(jumbo_cp.difference_pct), 0), COALESCE(ABS(la_cp.difference_pct), 0)) DESC, p.price_promo ASC"

    # Obtener total
    count_query = f"""
    SELECT COUNT(*)
    FROM products p
    LEFT JOIN competitor_prices coto_cp ON p.id = coto_cp.product_id AND coto_cp.supermarket = 'Coto'
    LEFT JOIN competitor_prices carr_cp ON p.id = carr_cp.product_id AND carr_cp.supermarket = 'Carrefour'
    LEFT JOIN competitor_prices reina_cp ON p.id = reina_cp.product_id AND reina_cp.supermarket = 'La Reina'
    LEFT JOIN competitor_prices jumbo_cp ON p.id = jumbo_cp.product_id AND jumbo_cp.supermarket = 'Jumbo'
    LEFT JOIN competitor_prices la_cp ON p.id = la_cp.product_id AND la_cp.supermarket = 'La Anónima'
    WHERE {where_sql}
    """
    c.execute(count_query, params)
    total = c.fetchone()[0]

    offset = (page - 1) * limit

    # Query items con paginación
    query = f"""
    SELECT p.id, p.ean, p.name, p.category, p.brand, p.price_regular, p.price_promo,
           p.is_promo, p.discount_pct, p.image_url, p.url, p.supermarket, p.updated_at,
           coto_cp.price as coto_price, coto_cp.difference_pct as coto_diff_pct,
           coto_cp.regular_price as coto_regular_price, coto_cp.promo_price as coto_promo_price,
           coto_cp.has_promo as coto_has_promo, coto_cp.promo_units as coto_promo_units,
           coto_cp.promo_detail as coto_promo_detail,
           carr_cp.price as carrefour_price, carr_cp.difference_pct as carrefour_diff_pct,
           carr_cp.regular_price as carrefour_regular_price, carr_cp.promo_price as carrefour_promo_price,
           carr_cp.has_promo as carrefour_has_promo, carr_cp.promo_units as carrefour_promo_units,
           carr_cp.promo_detail as carrefour_promo_detail,
           reina_cp.price as reina_price, reina_cp.difference_pct as reina_diff_pct,
           reina_cp.regular_price as reina_regular_price, reina_cp.promo_price as reina_promo_price,
           reina_cp.has_promo as reina_has_promo, reina_cp.promo_units as reina_promo_units,
           reina_cp.promo_detail as reina_promo_detail,
           jumbo_cp.price as jumbo_price, jumbo_cp.difference_pct as jumbo_diff_pct,
           jumbo_cp.regular_price as jumbo_regular_price, jumbo_cp.promo_price as jumbo_promo_price,
           jumbo_cp.has_promo as jumbo_has_promo, jumbo_cp.promo_units as jumbo_promo_units,
           jumbo_cp.promo_detail as jumbo_promo_detail,
           la_cp.price as la_price, la_cp.difference_pct as la_diff_pct,
           la_cp.regular_price as la_regular_price, la_cp.promo_price as la_promo_price,
           la_cp.has_promo as la_has_promo, la_cp.promo_units as la_promo_units,
           la_cp.promo_detail as la_promo_detail
    FROM products p
    LEFT JOIN competitor_prices coto_cp ON p.id = coto_cp.product_id AND coto_cp.supermarket = 'Coto'
    LEFT JOIN competitor_prices carr_cp ON p.id = carr_cp.product_id AND carr_cp.supermarket = 'Carrefour'
    LEFT JOIN competitor_prices reina_cp ON p.id = reina_cp.product_id AND reina_cp.supermarket = 'La Reina'
    LEFT JOIN competitor_prices jumbo_cp ON p.id = jumbo_cp.product_id AND jumbo_cp.supermarket = 'Jumbo'
    LEFT JOIN competitor_prices la_cp ON p.id = la_cp.product_id AND la_cp.supermarket = 'La Anónima'
    WHERE {where_sql}
    ORDER BY {order_sql}
    LIMIT ? OFFSET ?
    """
    query_params = list(params) + [limit, offset]

    c.execute(query, query_params)
    items = []
    for row in c.fetchall():
        item = dict(row)
        coto_price = item['coto_price']
        carr_price = item['carrefour_price']
        reina_price = item['reina_price']
        jumbo_price = item.get('jumbo_price')
        lg_price = item['price_promo']

        competitors = []
        available_prices = []

        if lg_price and lg_price > 0:
            available_prices.append(('La Gallega', lg_price))

        if coto_price and coto_price > 0:
            competitors.append({
                'supermarket': 'Coto',
                'price': coto_price,
                'difference_pct': item['coto_diff_pct'],
                'regular_price': item.get('coto_regular_price') or coto_price,
                'promo_price': item.get('coto_promo_price'),
                'has_promo': bool(item.get('coto_has_promo')),
                'promo_units': item.get('coto_promo_units') or 1,
                'promo_detail': item.get('coto_promo_detail') or ''
            })
            available_prices.append(('COTO', coto_price))

        if carr_price and carr_price > 0:
            competitors.append({
                'supermarket': 'Carrefour',
                'price': carr_price,
                'difference_pct': item['carrefour_diff_pct'],
                'regular_price': item.get('carrefour_regular_price') or carr_price,
                'promo_price': item.get('carrefour_promo_price'),
                'has_promo': bool(item.get('carrefour_has_promo')),
                'promo_units': item.get('carrefour_promo_units') or 1,
                'promo_detail': item.get('carrefour_promo_detail') or ''
            })
            available_prices.append(('Carrefour', carr_price))

        if reina_price and reina_price > 0:
            # Marcar como sospechoso si el diferencial es muy alto (probablemente pack vs unidad)
            is_suspicious = abs(item['reina_diff_pct']) > 50
            competitors.append({
                'supermarket': 'La Reina',
                'price': reina_price,
                'difference_pct': item['reina_diff_pct'],
                'regular_price': item.get('reina_regular_price') or reina_price,
                'promo_price': item.get('reina_promo_price'),
                'has_promo': bool(item.get('reina_has_promo')),
                'promo_units': item.get('reina_promo_units') or 1,
                'promo_detail': item.get('reina_promo_detail') or '',
                'suspicious': is_suspicious
            })
            available_prices.append(('La Reina', reina_price))

        if jumbo_price and jumbo_price > 0:
            competitors.append({
                'supermarket': 'Jumbo',
                'price': jumbo_price,
                'difference_pct': item['jumbo_diff_pct'],
                'regular_price': item.get('jumbo_regular_price') or jumbo_price,
                'promo_price': item.get('jumbo_promo_price'),
                'has_promo': bool(item.get('jumbo_has_promo')),
                'promo_units': item.get('jumbo_promo_units') or 1,
                'promo_detail': item.get('jumbo_promo_detail') or ''
            })
            available_prices.append(('Jumbo', jumbo_price))

        la_price = item.get('la_price')
        if la_price and la_price > 0:
            competitors.append({
                'supermarket': 'La Anónima',
                'price': la_price,
                'difference_pct': item['la_diff_pct'],
                'regular_price': item.get('la_regular_price') or la_price,
                'promo_price': item.get('la_promo_price'),
                'has_promo': bool(item.get('la_has_promo')),
                'promo_units': item.get('la_promo_units') or 1,
                'promo_detail': item.get('la_promo_detail') or ''
            })
            available_prices.append(('La Anónima', la_price))

        item['competitors'] = competitors

        # Determine winner
        if available_prices and len(available_prices) > 1:
            min_price = min(p[1] for p in available_prices)
            cheapest_stores = [p[0] for p in available_prices if abs(p[1] - min_price) < 0.01]

            if len(cheapest_stores) == len(available_prices):
                cheaper_supermarket = 'Igual'
                diff_amount = 0.0
                cheaper_diff_pct = 0.0
            else:
                cheaper_supermarket = ' / '.join(cheapest_stores)
                if 'La Gallega' in cheapest_stores:
                    comp_prices = [p[1] for p in available_prices if p[0] != 'La Gallega']
                    best_comp = min(comp_prices) if comp_prices else lg_price
                    diff_amount = round(best_comp - lg_price, 2)
                    cheaper_diff_pct = round(((best_comp - lg_price) / best_comp) * 100, 1) if best_comp > 0 else 0.0
                else:
                    diff_amount = round(lg_price - min_price, 2)
                    cheaper_diff_pct = round(((lg_price - min_price) / lg_price) * 100, 1) if lg_price > 0 else 0.0
        else:
            cheaper_supermarket = 'Desconocido'
            diff_amount = 0.0
            cheaper_diff_pct = 0.0

        item['cheaper_supermarket'] = cheaper_supermarket
        item['cheaper_diff_pct'] = cheaper_diff_pct
        item['diff_amount'] = diff_amount

        del item['coto_price']
        del item['coto_diff_pct']
        if 'coto_regular_price' in item: del item['coto_regular_price']
        if 'coto_promo_price' in item: del item['coto_promo_price']
        if 'coto_has_promo' in item: del item['coto_has_promo']
        if 'coto_promo_units' in item: del item['coto_promo_units']
        if 'coto_promo_detail' in item: del item['coto_promo_detail']
        del item['carrefour_price']
        del item['carrefour_diff_pct']
        if 'carrefour_regular_price' in item: del item['carrefour_regular_price']
        if 'carrefour_promo_price' in item: del item['carrefour_promo_price']
        if 'carrefour_has_promo' in item: del item['carrefour_has_promo']
        if 'carrefour_promo_units' in item: del item['carrefour_promo_units']
        if 'carrefour_promo_detail' in item: del item['carrefour_promo_detail']
        del item['reina_price']
        del item['reina_diff_pct']
        if 'reina_regular_price' in item: del item['reina_regular_price']
        if 'reina_promo_price' in item: del item['reina_promo_price']
        if 'reina_has_promo' in item: del item['reina_has_promo']
        if 'reina_promo_units' in item: del item['reina_promo_units']
        if 'reina_promo_detail' in item: del item['reina_promo_detail']
        if 'jumbo_price' in item: del item['jumbo_price']
        if 'jumbo_diff_pct' in item: del item['jumbo_diff_pct']
        if 'jumbo_regular_price' in item: del item['jumbo_regular_price']
        if 'jumbo_promo_price' in item: del item['jumbo_promo_price']
        if 'jumbo_has_promo' in item: del item['jumbo_has_promo']
        if 'jumbo_promo_units' in item: del item['jumbo_promo_units']
        if 'jumbo_promo_detail' in item: del item['jumbo_promo_detail']
        if 'la_price' in item: del item['la_price']
        if 'la_diff_pct' in item: del item['la_diff_pct']
        if 'la_regular_price' in item: del item['la_regular_price']
        if 'la_promo_price' in item: del item['la_promo_price']
        if 'la_has_promo' in item: del item['la_has_promo']
        if 'la_promo_units' in item: del item['la_promo_units']
        if 'la_promo_detail' in item: del item['la_promo_detail']
        items.append(item)

    conn.close()

    total_pages = (total + limit - 1) // limit if total > 0 else 1

    return {
        'total': total,
        'page': page,
        'limit': limit,
        'total_pages': total_pages,
        'items': items
    }

@app.get("/api/export/csv")
def api_export_csv():
    csv_path = os.path.join(PROJECT_DIR, "lagallega_products.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="Archivo CSV no disponible aún. Ejecute el scraper.")
    return FileResponse(csv_path, media_type="text/csv", filename="lagallega_productos.csv")

@app.get("/api/export/json")
def api_export_json():
    json_path = os.path.join(PROJECT_DIR, "lagallega_products.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Archivo JSON no disponible aún. Ejecute el scraper.")
    return FileResponse(json_path, media_type="application/json", filename="lagallega_productos.json")

# Serve UI
@app.get("/")
def serve_ui():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    return {"message": "Super Comparador API activa. UI index.html se está generando."}

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
