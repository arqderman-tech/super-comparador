"""
Scraper de Jumbo Argentina para SuperComparador
Extrae catalogo completo de Jumbo via API REST nativa de VTEX IO
y realiza cruce comparativo por codigo EAN con La Gallega.
"""
import os
import sys
import time
import logging
import re
import json
import sqlite3
import requests
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Importar funciones utilitarias compartidas
from import_carrefour import clean_ean, clean_image_url, check_pack_mismatch
from db import SUPERMARKET_CODES, get_connection

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
LOG_FILE = os.path.join(BASE_DIR, "scraper_jumbo.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("scraper_jumbo")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.jumbo.com.ar/",
}

# Categorias principales de supermercado en Jumbo
SUPER_CATEGORY_IDS = {
    1: "Almacén",
    2: "Bebidas",
    3: "Frutas y Verduras",
    4: "Carnes",
    5: "Pescados y Mariscos",
    6: "Quesos y Fiambres",
    7: "Lácteos",
    8: "Congelados",
    9: "Panadería y Pastelería",
    10: "Rotisería",
    11: "Perfumería",
    13: "Limpieza",
    14: "Mascotas",
    457: "Mundo Bebé",
    463: "Pastas Frescas"
}

VTEX_CURRENCY_DIVISOR = 82.644628
MAX_WORKERS = 6
PAGE_SIZE = 50

# Estado global para consulta desde API web
current_status = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "message": "Listo para iniciar",
    "matched": 0,
    "catalog_count": 0,
    "last_run": None
}

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=10, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def get_lagallega_eans() -> Dict[str, Dict[str, Any]]:
    """Obtiene los productos con EAN de La Gallega de la base de datos"""
    conn = get_connection()
    conn.execute("PRAGMA temp_store = MEMORY;")
    c = conn.cursor()
    c.execute("""
    SELECT id, ean, name, price_promo, category, image_url
    FROM products
    WHERE supermarket = 'La Gallega' AND ean != '' AND ean IS NOT NULL
    """)
    ean_dict = {}
    for r in c.fetchall():
        ean_clean = clean_ean(r['ean'])
        if ean_clean and len(ean_clean) >= 8:
            ean_dict[ean_clean] = {
                "id": r['id'],
                "name": r['name'],
                "price_promo": float(r['price_promo'] or 0.0),
                "category": r['category'],
                "image_url": r['image_url']
            }
    conn.close()
    return ean_dict

def get_subcategories(session: requests.Session) -> List[Dict[str, Any]]:
    """Descarga el arbol de categorias de Jumbo y filtra las subcategorias de supermercado"""
    url = "https://www.jumbo.com.ar/api/catalog_system/pub/category/tree/2"
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            tree = r.json()
            subcats = []
            for c in tree:
                cid = c.get('id')
                cname = c.get('name', 'General')
                if cid in SUPER_CATEGORY_IDS:
                    canonical_name = SUPER_CATEGORY_IDS[cid]
                    children = c.get('children', [])
                    if children:
                        for child in children:
                            subcats.append({
                                'parent_id': cid,
                                'parent_name': canonical_name,
                                'id': child.get('id'),
                                'name': child.get('name'),
                                'path': f"{cid}/{child.get('id')}"
                            })
                    else:
                        subcats.append({
                            'parent_id': cid,
                            'parent_name': canonical_name,
                            'id': cid,
                            'name': cname,
                            'path': f"{cid}"
                        })
            log.info(f"Descargadas {len(subcats)} subcategorías de Jumbo.")
            return subcats
    except Exception as e:
        log.warning(f"Error descargando arbol de categorias: {e}. Usando categorias raiz por defecto.")

    # Fallback si falla el arbol: usar las categorias principales directas
    return [{'parent_id': cid, 'parent_name': cname, 'id': cid, 'name': cname, 'path': str(cid)}
            for cid, cname in SUPER_CATEGORY_IDS.items()]

def parse_jumbo_promo(clusters: List[str], teasers: List[Dict[str, Any]], disc_highlights: List[Dict[str, Any]]) -> Tuple[int, int, str]:
    """Detecta promociones y tipo de pack a partir de clusters y ofertas de VTEX"""
    has_promo = 0
    promo_units = 1
    promo_detail = ""

    all_texts = []
    if clusters:
        all_texts.extend([str(c) for c in clusters])
    if teasers:
        for t in teasers:
            name = t.get('<Name>k__BackingField') or t.get('Name') or ''
            if name: all_texts.append(name)
    if disc_highlights:
        for d in disc_highlights:
            name = d.get('<Name>k__BackingField') or d.get('Name') or ''
            if name: all_texts.append(name)

    for text in all_texts:
        t_low = text.lower()
        if '4x2' in t_low:
            return 1, 4, text
        elif '3x2' in t_low:
            return 1, 3, text
        elif '2x1' in t_low:
            return 1, 2, text
        elif '2do al' in t_low or 'segunda unidad' in t_low:
            return 1, 2, text
        elif any(x in t_low for x in ['%', 'descuento', 'oferta', 'promo', 'rebaja']):
            has_promo = 1
            if not promo_detail:
                promo_detail = text

    return has_promo, promo_units, promo_detail


def build_jumbo_promo_info(raw_promo_name: str, regular_price: float) -> Tuple[int, int, float, str]:
    """
    Parsea promociones comunes de Jumbo (3x2, 2x1, 2do al 70%, etc.) y devuelve:
    (has_promo, promo_units, promo_price, promo_detail_explicativo)
    """
    if not raw_promo_name or regular_price <= 0:
        return 0, 1, regular_price, ""

    text = raw_promo_name.strip()
    t_low = text.lower()

    def fmt(val):
        return f"${val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    # 1. Patrón NxM (ej: 3x2, 2x1, 4x2, 4x3)
    m_nxm = re.search(r'(\d+)\s*[xX]\s*(\d+)', t_low)
    if m_nxm:
        take = int(m_nxm.group(1))
        pay = int(m_nxm.group(2))
        if take > pay and pay > 0:
            promo_units = take
            promo_price = round((regular_price * pay) / take, 2)
            reg_fmt = fmt(regular_price)
            dto_fmt = fmt(promo_price)
            detail = f"¡Hay promo en Jumbo! {text}: tenés que comprar {take} unidades (pagás {pay}) y te queda a {dto_fmt} c/u (Precio regular: {reg_fmt})"
            return 1, promo_units, promo_price, detail

    # 2. Patrón 2do al X% / Segunda al X%
    m_pct2 = re.search(r'(?:2do|segunda|2da|3ra|tercera|4ta)[^\d]*(?:al\s*|al)(\d+)%', t_low)
    if m_pct2:
        pct = int(m_pct2.group(1))
        units = 2
        if '3ra' in t_low or 'tercera' in t_low:
            units = 3
        elif '4ta' in t_low or 'cuarta' in t_low:
            units = 4
        total = (regular_price * (units - 1)) + (regular_price * (100 - pct) / 100.0)
        promo_price = round(total / units, 2)
        reg_fmt = fmt(regular_price)
        dto_fmt = fmt(promo_price)
        detail = f"¡Hay promo en Jumbo! {text}: tenés que comprar {units} unidades (la {units}da con {pct}% de descuento) y te queda a {dto_fmt} c/u (Precio regular: {reg_fmt})"
        return 1, units, promo_price, detail

    # 3. Descuento porcentual directo (ej: 25% de descuento, 15% off)
    m_pct = re.search(r'(\d+)%\s*(?:de\s*descuento|off|dto|\+)', t_low)
    if m_pct:
        pct = int(m_pct.group(1))
        promo_price = round(regular_price * (100 - pct) / 100.0, 2)
        reg_fmt = fmt(regular_price)
        dto_fmt = fmt(promo_price)
        detail = f"¡Oferta directa en Jumbo! {text}: comprando 1 unidad te queda a {dto_fmt} (Precio regular: {reg_fmt})"
        return 1, 1, promo_price, detail

    # 4. Otras menciones de descuento/oferta
    if any(x in t_low for x in ['%', 'descuento', 'oferta', 'promo', 'rebaja']):
        reg_fmt = fmt(regular_price)
        detail = f"¡Oferta en Jumbo! {text} (Precio regular: {reg_fmt})"
        return 1, 1, regular_price, detail

    return 0, 1, regular_price, text

def extract_jumbo_product(p: Dict[str, Any], default_category: str = "General") -> Optional[Dict[str, Any]]:
    """Extrae y normaliza un producto devuelto por el search API de VTEX"""
    items = p.get('items', [])
    if not items:
        return None

    item0 = items[0]
    sellers = item0.get('sellers', [])
    if not sellers:
        return None

    offer = sellers[0].get('commertialOffer', {})
    price_promo = float(offer.get('Price') or 0.0)
    if price_promo <= 0:
        return None

    # Normalizar regular price (ListPrice con divisor VTEX)
    raw_lp = float(offer.get('ListPrice') or 0.0)
    if raw_lp > 0 and (raw_lp / VTEX_CURRENCY_DIVISOR) >= price_promo:
        price_regular = round(raw_lp / VTEX_CURRENCY_DIVISOR, 2)
    elif price_promo <= raw_lp <= (price_promo * 3.5):
        price_regular = round(raw_lp, 2)
    else:
        price_regular = price_promo

    # Promociones y clusters
    clusters = list(p.get('productClusters', {}).values())
    teasers = offer.get('Teasers', [])
    disc_highlights = offer.get('DiscountHighLight', [])
    has_promo, promo_units, promo_detail = parse_jumbo_promo(clusters, teasers, disc_highlights)
    calc_promo_price = price_promo
    if has_promo and promo_detail:
        _, p_u, c_price, p_det = build_jumbo_promo_info(promo_detail, price_regular)
        if p_det:
            promo_units = p_u
            promo_detail = p_det
            calc_promo_price = c_price

    is_promo = 1 if (has_promo or price_regular > price_promo or price_regular > calc_promo_price) else 0
    discount_pct = 0.0
    if price_regular > price_promo:
        discount_pct = round(((price_regular - price_promo) / price_regular) * 100, 1)

    # Imagen
    images = item0.get('images', [])
    image_url = ""
    if images and isinstance(images, list):
        image_url = clean_image_url(images[0].get('imageUrl', ''))

    # Categoria
    categories = p.get('categories', [])
    category = default_category
    if categories and isinstance(categories, list) and len(categories) > 0:
        cat_parts = [c.strip() for c in categories[0].strip('/').split('/') if c.strip()]
        if cat_parts:
            category = cat_parts[0]

    ean = clean_ean(item0.get('ean'))
    item_id = str(item0.get('itemId') or p.get('productId') or '')

    link = p.get('link') or ""
    if not link and ean:
        link = f"https://www.jumbo.com.ar/{ean}?_q={ean}&map=ft"

    is_available = 1 if int(offer.get('AvailableQuantity') or 0) > 0 else 0

    return {
        'id': f"jumbo_{item_id}",
        'item_id': item_id,
        'ean': ean,
        'name': str(p.get('productName') or item0.get('name') or '').strip(),
        'category': category,
        'category_id': str(p.get('categoryId') or ''),
        'brand': str(p.get('brand') or '').strip(),
        'price_regular': price_regular,
        'price_promo': price_promo,
        'is_promo': is_promo,
        'discount_pct': discount_pct,
        'image_url': image_url,
        'url': link,
        'has_promo': has_promo,
        'promo_units': promo_units,
        'promo_detail': promo_detail,
        'promo_price_calculated': calc_promo_price,
        'is_available': is_available
    }

def scrape_subcategory(session: requests.Session, subcat: Dict[str, Any], max_items: int = 2500) -> List[Dict[str, Any]]:
    """Descarga todos los productos de una subcategoria paginando de a 50"""
    path = subcat['path']
    parent_name = subcat['parent_name']
    subcat_name = subcat['name']
    products = []
    offset = 0

    while offset < max_items:
        _to = offset + PAGE_SIZE - 1
        url = f"https://www.jumbo.com.ar/api/catalog_system/pub/products/search?fq=C:{path}&_from={offset}&_to={_to}"
        try:
            r = session.get(url, timeout=12)
            if r.status_code not in (200, 206):
                break

            items = r.json()
            if not items or not isinstance(items, list):
                break

            for raw in items:
                prod = extract_jumbo_product(raw, default_category=parent_name)
                if prod:
                    products.append(prod)

            if len(items) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            time.sleep(0.05)
        except Exception as e:
            log.debug(f"Error descargando subcat {path} offset {offset}: {e}")
            break

    return products

def search_jumbo_by_ean(session: requests.Session, ean: str) -> Optional[Dict[str, Any]]:
    """Busca un producto especifico en Jumbo por codigo de barras (EAN)"""
    url = f"https://www.jumbo.com.ar/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}"
    try:
        r = session.get(url, timeout=6)
        if r.status_code in (200, 206) and r.json():
            items = r.json()
            if items and isinstance(items, list):
                return extract_jumbo_product(items[0])
    except Exception:
        pass
    return None

def update_jumbo_catalog_and_prices(
    all_jumbo_products: List[Dict[str, Any]],
    ean_dict: Dict[str, Dict[str, Any]]
) -> Tuple[int, int]:
    """
    Inserta en la base de datos:
    1. Tabla products (Catalogo completo con supermarket = 'Jumbo')
    2. Tabla competitor_prices (Cruce por EAN contra La Gallega)
    3. Tabla price_history (Serie historica diaria con supermarket_id = 4)
    """
    conn = get_connection()
    c = conn.cursor()

    # 1. Obtener imagenes de referencia de La Gallega y las ya existentes de Jumbo
    c.execute("""
    SELECT ean, image_url
    FROM products
    WHERE supermarket = 'La Gallega' AND ean != '' AND ean IS NOT NULL AND image_url != '' AND image_url IS NOT NULL
    """)
    lg_img_by_ean = {clean_ean(r[0]): clean_image_url(r[1]) for r in c.fetchall() if clean_ean(r[0])}

    c.execute("""
    SELECT id, ean, image_url
    FROM products
    WHERE supermarket = 'Jumbo' AND image_url != '' AND image_url IS NOT NULL
    """)
    existing_jumbo_img_by_id = {}
    existing_jumbo_img_by_ean = {}
    for pid, pean, pimg in c.fetchall():
        clean_img = clean_image_url(pimg)
        if clean_img:
            existing_jumbo_img_by_id[pid] = clean_img
            c_ean = clean_ean(pean)
            if c_ean:
                existing_jumbo_img_by_ean[c_ean] = clean_img

    # 2. Preparar inserciones en la tabla products
    products_inserts = []
    seen_ids = set()
    jumbo_by_ean = {}

    for p in all_jumbo_products:
        custom_id = p['id']
        if not custom_id or custom_id in seen_ids:
            continue
        seen_ids.add(custom_id)

        clean_e = p.get('ean') or ''
        if clean_e and clean_e not in jumbo_by_ean:
            jumbo_by_ean[clean_e] = p

        # Politica de imagenes:
        # Prioridad 1: La Gallega
        # Prioridad 2: Existente en BD para este producto Jumbo
        # Prioridad 3: Descargada de Jumbo
        if clean_e and clean_e in lg_img_by_ean:
            final_img = lg_img_by_ean[clean_e]
        elif custom_id in existing_jumbo_img_by_id:
            final_img = existing_jumbo_img_by_id[custom_id]
        elif clean_e and clean_e in existing_jumbo_img_by_ean:
            final_img = existing_jumbo_img_by_ean[clean_e]
        else:
            final_img = p.get('image_url') or ''

        products_inserts.append((
            custom_id,
            clean_e,
            p['name'],
            p['category'],
            p['category_id'],
            p['brand'],
            p['price_regular'],
            p['price_promo'],
            p['is_promo'],
            p['discount_pct'],
            final_img,
            p['url'],
            'Jumbo',
            p.get('promo_units') or 1,
            p.get('promo_detail') or '',
            p.get('is_available', 1)
        ))

    log.info(f"Actualizando {len(products_inserts):,} productos de Jumbo en 'products'...")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    BATCH_SIZE = 500
    for i in range(0, len(products_inserts), BATCH_SIZE):
        batch = products_inserts[i:i + BATCH_SIZE]
        c.executemany("""
        INSERT INTO products (
            id, ean, name, category, category_id, brand,
            price_regular, price_promo, is_promo, discount_pct,
            image_url, url, supermarket, pack_units, pack_detail, is_available, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            price_regular = excluded.price_regular,
            price_promo = excluded.price_promo,
            is_promo = excluded.is_promo,
            discount_pct = excluded.discount_pct,
            image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE products.image_url END,
            url = excluded.url,
            pack_units = excluded.pack_units,
            pack_detail = excluded.pack_detail,
            is_available = excluded.is_available,
            updated_at = CURRENT_TIMESTAMP
        """, batch)
        conn.commit()

    # 3. Preparar cruce con La Gallega para competitor_prices
    # Limpiar precios previos de Jumbo para actualizar de forma limpia
    c.execute("DELETE FROM competitor_prices WHERE supermarket = 'Jumbo'")
    conn.commit()

    comp_inserts = []
    history_inserts = []
    pack_adjusted_count = 0
    anomaly_count = 0
    today_int = int(datetime.now().strftime("%Y%m%d"))
    jumbo_code = SUPERMARKET_CODES['Jumbo']

    for clean_e, j_prod in jumbo_by_ean.items():
        if not clean_e or clean_e not in ean_dict:
            continue

        lg_info = ean_dict[clean_e]
        lg_price = lg_info.get("price_promo") or 0.0
        raw_j_price = j_prod.get("price_promo") or 0.0

        if raw_j_price <= 0:
            continue

        lg_name = lg_info.get("name", "")
        j_name = j_prod.get("name", "")

        is_pack_adj, norm_price, pack_detail = check_pack_mismatch(
            lg_name, j_name, lg_price, raw_j_price
        )

        if is_pack_adj:
            j_price = norm_price
            has_promo = 1
            promo_units = 1
            promo_detail = pack_detail
            pack_adjusted_count += 1
        else:
            j_price = raw_j_price
            has_promo = j_prod.get("has_promo", 0)
            promo_units = j_prod.get("promo_units", 1)
            promo_detail = j_prod.get("promo_detail", "")

        if lg_price > 0:
            diff_pct = round(((j_price - lg_price) / lg_price) * 100, 1)
        else:
            diff_pct = 0.0

        # Filtro de anomalias extremas (>200% o <-70%)
        if diff_pct < -70 or diff_pct > 200:
            anomaly_count += 1
            continue

        reg_p = j_prod.get("price_regular") or j_price
        calc_promo_p = j_prod.get("promo_price_calculated")
        if (not calc_promo_p or calc_promo_p >= reg_p) and promo_detail:
            _, p_u, calc_promo_p, enriched_d = build_jumbo_promo_info(promo_detail, reg_p)
            if enriched_d:
                promo_units = p_u
                promo_detail = enriched_d
        promo_p = calc_promo_p if (has_promo and calc_promo_p and calc_promo_p < reg_p) else (j_price if has_promo else None)

        comp_inserts.append((
            lg_info["id"],
            clean_e,
            "Jumbo",
            j_price,
            diff_pct,
            reg_p,
            promo_p,
            has_promo,
            promo_units,
            promo_detail
        ))

        history_inserts.append((clean_e, today_int, j_price, jumbo_code))

    log.info(f"Insertando {len(comp_inserts):,} precios coincidentes en 'competitor_prices'...")
    for i in range(0, len(comp_inserts), BATCH_SIZE):
        batch = comp_inserts[i:i + BATCH_SIZE]
        c.executemany("""
        INSERT INTO competitor_prices (
            product_id, ean, supermarket, price, difference_pct,
            regular_price, promo_price, has_promo, promo_units, promo_detail
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        conn.commit()

    if history_inserts:
        for i in range(0, len(history_inserts), BATCH_SIZE):
            batch = history_inserts[i:i + BATCH_SIZE]
            c.executemany("""
            INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
            VALUES (?, ?, ?, ?)
            """, batch)
            conn.commit()
        log.info(f"Histórico diario de Jumbo guardado: {len(history_inserts):,} registros en 'price_history'")

    conn.close()

    if pack_adjusted_count > 0:
        log.info(f"Packs de Jumbo ajustados equitativamente: {pack_adjusted_count}")
    if anomaly_count > 0:
        log.info(f"Omitidas {anomaly_count} anomalías extremas en Jumbo (>200% o <-70%)")

    return len(products_inserts), len(comp_inserts)

def run_jumbo_deep_scan(
    session: requests.Session,
    ean_dict: Dict[str, Dict[str, Any]],
    already_matched_eans: set,
    max_lookups: int = 1500
) -> List[Dict[str, Any]]:
    """
    Rastreo puntual de EANs de La Gallega que no aparecieron en el recorrido de categorias
    """
    missing_eans = [e for e in ean_dict.keys() if e not in already_matched_eans]
    if not missing_eans:
        return []

    target_eans = missing_eans[:max_lookups]
    log.info(f"Iniciando escaneo puntual de {len(target_eans):,} EANs restantes en Jumbo...")
    found_products = []

    def fetch_ean(e):
        return search_jumbo_by_ean(session, e)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_ean, e): e for e in target_eans}
        for future in as_completed(futures):
            prod = future.result()
            if prod:
                found_products.append(prod)

    log.info(f"Escaneo puntual completado: {len(found_products):,} productos adicionales encontrados.")
    return found_products

def main(quick_mode: bool = False):
    global current_status
    start_time = time.time()
    current_status["is_running"] = True
    current_status["message"] = "Iniciando extracción de Jumbo..."
    current_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log.info("=" * 60)
    log.info("INICIANDO SCRAPER DE JUMBO ARGENTINA (VTEX IO)")
    log.info("=" * 60)

    try:
        session = get_session()

        # 1. Cargar EANs de La Gallega
        ean_dict = get_lagallega_eans()
        log.info(f"Cargados {len(ean_dict):,} EANs de La Gallega para cruce multitienda")

        # 2. Descargar arbol de subcategorias
        subcats = get_subcategories(session)
        if quick_mode:
            subcats = subcats[:15]
            log.info("MODO RAPIDO ACTIVO: Limitado a primeras 15 subcategorias")

        current_status["total"] = len(subcats)
        current_status["message"] = f"Descargando catálogo ({len(subcats)} subcategorías)..."

        all_products = []
        completed_subcats = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            future_to_cat = {ex.submit(scrape_subcategory, session, sc): sc for sc in subcats}
            for future in as_completed(future_to_cat):
                sc = future_to_cat[future]
                try:
                    prods = future.result()
                    all_products.extend(prods)
                    completed_subcats += 1
                    current_status["progress"] = completed_subcats
                    current_status["catalog_count"] = len(all_products)
                    if completed_subcats % 10 == 0 or completed_subcats == len(subcats):
                        log.info(f"  [{completed_subcats}/{len(subcats)}] Subcategorías procesadas. Total prods: {len(all_products):,}")
                except Exception as e:
                    log.error(f"Error procesando subcat {sc.get('name')}: {e}")

        # 3. Deduplicar productos por itemId / EAN
        seen = set()
        unique_prods = []
        matched_eans = set()
        for p in all_products:
            key = p['id']
            if key not in seen:
                seen.add(key)
                unique_prods.append(p)
                if p.get('ean') and p['ean'] in ean_dict:
                    matched_eans.add(p['ean'])

        log.info(f"Productos únicos recolectados de categorías: {len(unique_prods):,} (Coincidencias EAN: {len(matched_eans):,})")

        # 4. Escaneo puntual para EANs faltantes
        if not quick_mode:
            current_status["message"] = "Ejecutando escaneo puntual de EANs restantes..."
            deep_prods = run_jumbo_deep_scan(session, ean_dict, matched_eans, max_lookups=1000)
            for p in deep_prods:
                if p['id'] not in seen:
                    seen.add(p['id'])
                    unique_prods.append(p)

        # 5. Guardar en Base de Datos
        current_status["message"] = "Guardando productos y precios en base de datos..."
        total_saved, total_comps = update_jumbo_catalog_and_prices(unique_prods, ean_dict)

        elapsed = round(time.time() - start_time, 2)
        current_status["is_running"] = False
        current_status["progress"] = current_status["total"]
        current_status["catalog_count"] = total_saved
        current_status["matched"] = total_comps
        current_status["message"] = f"Finalizado en {elapsed}s: {total_saved:,} prods en catálogo, {total_comps:,} coincidencias"

        log.info("=" * 60)
        log.info("PROCESO DE JUMBO FINALIZADO CON EXITO")
        log.info(f"Catálogo completo guardado en 'products': {total_saved:,}")
        log.info(f"Coincidencias cruzadas en 'competitor_prices': {total_comps:,}")
        log.info(f"Tiempo total: {elapsed} segundos")
        log.info("=" * 60)
        return total_saved, total_comps

    except Exception as e:
        log.error(f"Error fatal en scraper de Jumbo: {e}", exc_info=True)
        current_status["is_running"] = False
        current_status["message"] = f"Error: {e}"
        raise

if __name__ == "__main__":
    is_quick = "--quick" in sys.argv
    main(quick_mode=is_quick)
