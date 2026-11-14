"""
Scraper de La Anonima para SuperComparador Dev
Extrae el catalogo completo de La Anonima para la sucursal Rosario (CP 2000 / Sucursal 186)
e integra cruce multitienda por codigo de barras EAN con La Gallega y todos los supermercados.
"""
import os
import sys
import time
import logging
import re
import json
import sqlite3
import random
import threading
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# Soporte para curl_cffi con TLS Impersonation para sortear CloudFront WAF
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False

# Importar funciones utilitarias compartidas
from import_carrefour import clean_ean, clean_image_url, check_pack_mismatch
from db import SUPERMARKET_CODES, get_connection

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
LOG_FILE = os.path.join(BASE_DIR, "scraper_laanonima.log")
CATEGORIAS_JSON_PATH = os.path.join(BASE_DIR, "categorias_laanonima.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("scraper_laanonima")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-419,es;q=0.9",
    "Referer": "https://www.laanonima.com.ar/",
}

# Cookie oficial para Rosario (CP 2000, Sucursal 186)
COOKIES = {
    "codigoPostal": "2000",
    "Id-Sucursal-Super": "186"
}

# Departamentos N1 de Supermercado en La Anonima
SUPER_N1_IDS = ['512', '513', '516', '766', '517', '518', '820', '514', '877']

# Concurrencia moderada para evitar bloqueos por tasa de peticiones en CloudFront WAF
MAX_WORKERS = 8
BATCH_SIZE = 500

current_status: Dict[str, Any] = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "message": "Inactivo",
    "catalog_count": 0,
    "matched": 0,
    "last_run": None,
    "elapsed_seconds": 0
}

_thread_local = threading.local()


def get_session():
    """
    Obtiene una sesion HTTP configurada por hilo.
    Prioriza curl_cffi con impersonacion TLS de Chrome para mitigar bloqueos CloudFront.
    """
    if not hasattr(_thread_local, "session") or _thread_local.session is None:
        if HAS_CURL_CFFI:
            try:
                s = cffi_requests.Session(impersonate="chrome120")
                s.headers.update(HEADERS)
                s.cookies.update(COOKIES)
                _thread_local.session = s
                return s
            except Exception as e:
                log.warning(f"Fallo al inicializar curl_cffi session: {e}. Usando requests estandar.")

        s = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=2,
            pool_block=False
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(HEADERS)
        s.cookies.update(COOKIES)
        _thread_local.session = s

    return _thread_local.session


def load_cached_subcategories() -> List[Tuple[str, str]]:
    """Carga las subcategorias N3 desde el respaldo categorias_laanonima.json"""
    if not os.path.exists(CATEGORIAS_JSON_PATH):
        return []
    try:
        with open(CATEGORIAS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        subcats = []
        for item in data:
            name = item.get("name") or ""
            url = item.get("url") or ""
            if url:
                subcats.append((name, url))
        log.info(f"Cargadas {len(subcats)} subcategorias desde respaldo local '{os.path.basename(CATEGORIAS_JSON_PATH)}'")
        return subcats
    except Exception as e:
        log.warning(f"Error leyendo {CATEGORIAS_JSON_PATH}: {e}")
        return []


def save_cached_subcategories(subcats: List[Tuple[str, str]]):
    """Guarda las subcategorias N3 en categorias_laanonima.json si no existe o para actualizar"""
    try:
        data = [{"name": name, "url": url} for name, url in subcats]
        with open(CATEGORIAS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info(f"Respaldo de {len(data)} subcategorias guardado en '{CATEGORIAS_JSON_PATH}'")
    except Exception as e:
        log.warning(f"No se pudo guardar {CATEGORIAS_JSON_PATH}: {e}")


def get_lagallega_eans() -> Dict[str, Dict[str, Any]]:
    """Obtiene los productos con EAN de La Gallega para el cruce multitienda"""
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


def get_all_supermarkets_eans() -> set:
    """Obtiene todos los EANs unicos existentes en la base de datos de todos los supermercados"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT DISTINCT ean 
    FROM products 
    WHERE ean IS NOT NULL AND length(TRIM(ean)) >= 8
    """)
    all_eans = set(clean_ean(r[0]) for r in c.fetchall() if clean_ean(r[0]))
    conn.close()
    return all_eans


def get_existing_eans_laanonima() -> Dict[str, str]:
    """
    Carga el mapeo codigo -> ean de los productos de La Anonima ya catalogados en la BD.
    Permite reutilizar los EANs resueltos previamente sin saturar las PDPs de La Anonima.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT id, ean
    FROM products
    WHERE supermarket = 'La Anónima' AND ean != '' AND ean IS NOT NULL
    """)
    code_to_ean = {}
    for pid, ean in c.fetchall():
        clean_e = clean_ean(ean)
        if clean_e and len(clean_e) >= 8:
            codigo = pid.replace("laanonima_", "")
            code_to_ean[codigo] = clean_e
            code_to_ean[pid] = clean_e
    conn.close()
    return code_to_ean


def get_supermarket_n3_subcategories(session: Optional[Any] = None) -> List[Tuple[str, str]]:
    """
    Obtiene las 91 subcategorias N3 de gondola.
    Intenta descargar la portada /supermercado/ en vivo; si falla (HTTP 403, timeout, etc.),
    utiliza transparentemente el respaldo local 'categorias_laanonima.json'.
    """
    if session is None:
        session = get_session()

    url = "https://www.laanonima.com.ar/supermercado/"
    try:
        r = session.get(url, timeout=12)
        if r.status_code == 200 and len(r.text) > 2000:
            soup = BeautifulSoup(r.text, 'html.parser')
            all_n3 = []
            for n1_id in SUPER_N1_IDS:
                link = soup.find('a', href=re.compile(rf'/n1_{n1_id}/'))
                if link:
                    parent = link.find_parent('li')
                    if parent:
                        n3_links = parent.find_all('a', href=re.compile(r'/n3_\d+/'))
                        for a in n3_links:
                            all_n3.append((a.get_text(strip=True), a['href']))

            seen = set()
            unique_n3 = []
            for name, href in all_n3:
                if href not in seen:
                    seen.add(href)
                    unique_n3.append((name, href))

            if len(unique_n3) >= 50:
                log.info(f"Subcategorias N3 encontradas en catalogo en vivo de La Anonima: {len(unique_n3)}")
                save_cached_subcategories(unique_n3)
                return unique_n3
        else:
            log.warning(f"Portada /supermercado/ retorno status HTTP {r.status_code}. Utilizando respaldo local.")
    except Exception as e:
        log.warning(f"Error consultando portada /supermercado/ en vivo: {e}. Utilizando respaldo local.")

    # Fallback inmediato y seguro al archivo respaldado
    cached = load_cached_subcategories()
    if cached:
        return cached

    log.error("No se pudieron obtener subcategorias ni en vivo ni desde categorias_laanonima.json")
    return []


def scrape_subcategory(item: Tuple[str, str], session: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Descarga una subcategoria completa y extrae todos los productos con data-attributes.
    Usa sesion por hilo y pausa leve para no disparar CloudFront WAF.
    """
    cat_name, cat_url = item
    if session is None:
        session = get_session()

    products = []
    try:
        time.sleep(random.uniform(0.05, 0.15))
        r = session.get(cat_url, timeout=15)
        if r.status_code != 200:
            log.warning(f"HTTP {r.status_code} en subcategoria: {cat_name} ({cat_url})")
            return []

        soup = BeautifulSoup(r.text, 'html.parser')
        cards = soup.find_all('img', attrs={"data-codigo": True})

        for img in cards:
            codigo = img.get('data-codigo')
            if not codigo:
                continue

            parent_a = img.find_parent('a')
            href = parent_a.get('href') if parent_a else ''
            if href and not href.startswith("http"):
                href = "https://www.laanonima.com.ar" + href

            raw_name = img.get('data-nombre') or ''
            raw_brand = img.get('data-marca') or ''
            raw_cat = img.get('data-rutacategorias') or cat_name

            p_promo = float(img.get('data-precio') or 0.0)
            p_reg = float(img.get('data-precio_anterior') or p_promo or 0.0)
            if p_reg <= 0:
                p_reg = p_promo

            is_promo = 1 if (p_promo < p_reg and p_promo > 0) else 0
            disc_pct = round((1 - p_promo / p_reg) * 100, 1) if (is_promo and p_reg > 0) else 0.0

            img_url = clean_image_url(img.get('data-src') or img.get('src') or '')

            products.append({
                "codigo": codigo,
                "id": f"laanonima_{codigo}",
                "name": raw_name.strip(),
                "brand": raw_brand.strip(),
                "category": raw_cat.strip(),
                "price_promo": p_promo,
                "price_regular": p_reg,
                "is_promo": is_promo,
                "discount_pct": disc_pct,
                "image_url": img_url,
                "url": href,
                "ean": "",  # Se resolvera via cache local o PDP
                "supermarket": "La Anónima",
                "is_available": 1
            })

    except Exception as e:
        log.warning(f"Error extrayendo subcategoria {cat_name}: {e}")

    return products


def fetch_gtin_from_pdp(session: Any, prod: Dict[str, Any]) -> Optional[str]:
    """
    Visita la ficha de producto (PDP) para extraer el GTIN/EAN del JSON-LD schema.org
    """
    url = prod.get('url')
    if not url:
        return None
    try:
        r = session.get(url, timeout=7)
        if r.status_code == 200:
            m = re.search(r'"gtin":\s*"(\d+)"', r.text)
            if m:
                return clean_ean(m.group(1))
    except Exception:
        pass
    return None


def search_laanonima_by_ean(session: Any, ean: str) -> Optional[Dict[str, Any]]:
    """
    Busca un EAN en La Anonima via /buscar/{ean}.
    Si no arroja 'No encontramos resultados para', extrae la tarjeta encontrada.
    """
    clean_e = clean_ean(ean)
    if not clean_e:
        return None
    url = f"https://www.laanonima.com.ar/buscar/{clean_e}"
    try:
        time.sleep(random.uniform(0.05, 0.15))
        r = session.get(url, timeout=8)
        if r.status_code == 200 and "No encontramos resultados para" not in r.text:
            soup = BeautifulSoup(r.text, 'html.parser')
            img = soup.find('img', attrs={"data-codigo": True})
            if img:
                codigo = img.get('data-codigo')
                parent_a = img.find_parent('a')
                href = parent_a.get('href') if parent_a else ''
                if href and not href.startswith("http"):
                    href = "https://www.laanonima.com.ar" + href

                p_promo = float(img.get('data-precio') or 0.0)
                p_reg = float(img.get('data-precio_anterior') or p_promo or 0.0)
                if p_reg <= 0:
                    p_reg = p_promo
                is_promo = 1 if (p_promo < p_reg and p_promo > 0) else 0
                disc_pct = round((1 - p_promo / p_reg) * 100, 1) if (is_promo and p_reg > 0) else 0.0

                return {
                    "codigo": codigo,
                    "id": f"laanonima_{codigo}",
                    "name": (img.get('data-nombre') or '').strip(),
                    "brand": (img.get('data-marca') or '').strip(),
                    "category": (img.get('data-rutacategorias') or 'Supermercado').strip(),
                    "price_promo": p_promo,
                    "price_regular": p_reg,
                    "is_promo": is_promo,
                    "discount_pct": disc_pct,
                    "image_url": clean_image_url(img.get('data-src') or img.get('src') or ''),
                    "url": href,
                    "ean": clean_e,
                    "supermarket": "La Anónima",
                    "is_available": 1
                }
    except Exception:
        pass
    return None


def run_laanonima_deep_scan(
    session: Any,
    target_eans: List[str],
    max_lookups: int = 500
) -> List[Dict[str, Any]]:
    """
    Rastreo puntual de EANs prioritarios faltantes con concurrencia moderada.
    """
    if not target_eans:
        return []

    subset = target_eans[:max_lookups]
    log.info(f"Iniciando escaneo puntual de {len(subset):,} EANs en La Anonima...")
    found_products = []

    def fetch_ean(e):
        s = get_session()
        return search_laanonima_by_ean(s, e)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_ean, e): e for e in subset}
        for future in as_completed(futures):
            prod = future.result()
            if prod:
                found_products.append(prod)

    log.info(f"Escaneo puntual completado: {len(found_products):,} productos adicionales hallados.")
    return found_products


def update_laanonima_catalog_and_prices(
    all_laanonima_products: List[Dict[str, Any]],
    ean_dict_lagallega: Dict[str, Dict[str, Any]]
) -> Tuple[int, int]:
    """
    Inserta en la base de datos de manera atomica y segura:
    1. Tabla products (Catalogo completo con supermarket = 'La Anónima')
    2. Tabla competitor_prices (Cruce por EAN contra La Gallega)
    3. Tabla price_history (Serie historica diaria con supermarket_id = 5)

    PROTECCION: Si no se recibieron productos frescos, no borra los datos existentes.
    """
    if not all_laanonima_products:
        log.warning("No se recibieron productos de La Anonima para actualizar. Se preserva el catalogo existente.")
        return 0, 0

    conn = get_connection()
    c = conn.cursor()

    # 1. Obtener imagenes de referencia
    c.execute("""
    SELECT ean, image_url
    FROM products
    WHERE supermarket = 'La Gallega' AND ean != '' AND ean IS NOT NULL AND image_url != '' AND image_url IS NOT NULL
    """)
    lg_img_by_ean = {clean_ean(r[0]): clean_image_url(r[1]) for r in c.fetchall() if clean_ean(r[0])}

    c.execute("""
    SELECT id, ean, image_url
    FROM products
    WHERE supermarket = 'La Anónima' AND image_url != '' AND image_url IS NOT NULL
    """)
    existing_la_img_by_id = {}
    existing_la_img_by_ean = {}
    for pid, pean, pimg in c.fetchall():
        clean_img = clean_image_url(pimg)
        if clean_img:
            existing_la_img_by_id[pid] = clean_img
            c_ean = clean_ean(pean)
            if c_ean:
                existing_la_img_by_ean[c_ean] = clean_img

    # 2. Preparar inserciones en la tabla products
    products_inserts = []
    seen_ids = set()
    laanonima_by_ean = {}

    for p in all_laanonima_products:
        custom_id = p['id']
        if not custom_id or custom_id in seen_ids:
            continue
        seen_ids.add(custom_id)

        clean_e = p.get('ean') or ''
        if clean_e and clean_e not in laanonima_by_ean:
            laanonima_by_ean[clean_e] = p

        # Politica de imagenes:
        # Prioridad 1: La Gallega
        # Prioridad 2: Existente en BD para este producto
        # Prioridad 3: Descargada de La Anonima
        if clean_e and clean_e in lg_img_by_ean:
            final_img = lg_img_by_ean[clean_e]
        elif custom_id in existing_la_img_by_id:
            final_img = existing_la_img_by_id[custom_id]
        elif clean_e and clean_e in existing_la_img_by_ean:
            final_img = existing_la_img_by_ean[clean_e]
        else:
            final_img = p.get('image_url') or ''

        products_inserts.append((
            custom_id,
            clean_e,
            p['name'],
            p.get('category') or 'Supermercado',
            None,  # category_id
            p.get('brand') or '',
            p.get('price_regular') or 0.0,
            p.get('price_promo') or 0.0,
            p.get('is_promo') or 0,
            p.get('discount_pct') or 0.0,
            final_img,
            p.get('url') or '',
            'La Anónima',
            1,   # pack_units
            '',  # pack_detail
            1    # is_available
        ))

    log.info(f"Guardando {len(products_inserts):,} productos de La Anonima en 'products'...")
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
            ean = CASE WHEN excluded.ean != '' THEN excluded.ean ELSE products.ean END,
            name = excluded.name,
            category = excluded.category,
            brand = excluded.brand,
            price_regular = excluded.price_regular,
            price_promo = excluded.price_promo,
            is_promo = excluded.is_promo,
            discount_pct = excluded.discount_pct,
            image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE products.image_url END,
            url = excluded.url,
            is_available = excluded.is_available,
            updated_at = CURRENT_TIMESTAMP
        """, batch)
        conn.commit()

    # 3. Preparar cruce con La Gallega para competitor_prices
    comp_inserts = []
    history_inserts = []
    pack_adjusted_count = 0
    anomaly_count = 0
    today_int = int(datetime.now().strftime("%Y%m%d"))
    la_code = SUPERMARKET_CODES['La Anónima']

    for clean_e, la_prod in laanonima_by_ean.items():
        if not clean_e or clean_e not in ean_dict_lagallega:
            continue

        lg_info = ean_dict_lagallega[clean_e]
        lg_price = lg_info.get("price_promo") or 0.0
        raw_la_price = la_prod.get("price_promo") or 0.0

        if raw_la_price <= 0:
            continue

        lg_name = lg_info.get("name", "")
        la_name = la_prod.get("name", "")

        is_pack_adj, norm_price, pack_detail = check_pack_mismatch(
            lg_name, la_name, lg_price, raw_la_price
        )

        if is_pack_adj:
            la_price = norm_price
            has_promo = 1
            promo_units = 1
            promo_detail = pack_detail
            pack_adjusted_count += 1
        else:
            la_price = raw_la_price
            has_promo = la_prod.get("is_promo", 0)
            promo_units = 1
            promo_detail = f"{la_prod.get('discount_pct')}% OFF" if has_promo else ""

        if lg_price > 0:
            diff_pct = round(((la_price - lg_price) / lg_price) * 100, 1)
        else:
            diff_pct = 0.0

        # Filtro de anomalias extremas (>200% o <-70%)
        if diff_pct < -70 or diff_pct > 200:
            anomaly_count += 1
            continue

        reg_p = la_prod.get("price_regular") or la_price
        promo_p = la_price if has_promo else None

        comp_inserts.append((
            lg_info["id"],
            clean_e,
            'La Anónima',
            la_price,
            diff_pct,
            reg_p,
            promo_p,
            has_promo,
            promo_units,
            promo_detail
        ))

    # Almacenar histórico diario de La Anónima para TODO el catálogo con EAN
    for clean_e, la_prod in laanonima_by_ean.items():
        la_price = float(la_prod.get("price_promo") or la_prod.get("price_regular") or 0.0)
        if clean_e and la_price > 0:
            history_inserts.append((clean_e, today_int, la_price, la_code))

    # PROTECCION: Solo reemplazar competitor_prices si se generaron cruces validos
    if comp_inserts:
        log.info(f"Insertando {len(comp_inserts):,} precios coincidentes en 'competitor_prices'...")
        c.execute("DELETE FROM competitor_prices WHERE supermarket = 'La Anónima'")
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
    else:
        log.warning("No se generaron cruces nuevos con La Gallega; se preservan los precios previos en competitor_prices.")

    if history_inserts:
        for i in range(0, len(history_inserts), BATCH_SIZE):
            batch = history_inserts[i:i + BATCH_SIZE]
            c.executemany("""
            INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
            VALUES (?, ?, ?, ?)
            """, batch)
            conn.commit()
        log.info(f"Historial diario de La Anonima guardado: {len(history_inserts):,} registros en 'price_history'")

    conn.close()

    if pack_adjusted_count > 0:
        log.info(f"Packs de La Anonima ajustados equitativamente: {pack_adjusted_count}")
    if anomaly_count > 0:
        log.info(f"Omitidas {anomaly_count} anomalias extremas en La Anonima (>200% o <-70%)")

    return len(products_inserts), len(comp_inserts)


def main(quick_mode: bool = False):
    global current_status
    start_time = time.time()
    current_status["is_running"] = True
    current_status["message"] = "Iniciando extraccion de La Anonima..."
    current_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log.info("=" * 60)
    log.info("INICIANDO SCRAPER DE LA ANONIMA ONLINE (SUCURSAL ROSARIO 186)")
    log.info("=" * 60)

    try:
        session = get_session()

        # 1. Cargar EANs de La Gallega y base global
        ean_dict_lg = get_lagallega_eans()
        all_store_eans = get_all_supermarkets_eans()
        log.info(f"Cargados {len(ean_dict_lg):,} EANs de La Gallega y {len(all_store_eans):,} EANs globales de todas las tiendas")

        # 2. Cargar subcategorias N3 (con fallback automatico a categorias_laanonima.json)
        subcats = get_supermarket_n3_subcategories(session)
        if not subcats:
            log.error("No se obtuvieron subcategorias. Abortando corrida de La Anonima sin modificar BD.")
            current_status["is_running"] = False
            current_status["message"] = "Error: Sin subcategorias disponibles"
            return 0, 0

        if quick_mode:
            subcats = subcats[:15]
            log.info("MODO RAPIDO ACTIVO: Limitado a primeras 15 subcategorias")

        current_status["total"] = len(subcats)
        current_status["message"] = f"Descargando catalogo ({len(subcats)} subcategorias)..."

        all_products = []
        completed_subcats = 0

        # Fase 1: Descargar subcategorias con concurrencia controlada
        def fetch_subcat_task(sc):
            s = get_session()
            return scrape_subcategory(sc, session=s)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            future_to_cat = {ex.submit(fetch_subcat_task, sc): sc for sc in subcats}
            for future in as_completed(future_to_cat):
                sc = future_to_cat[future]
                try:
                    prods = future.result()
                    all_products.extend(prods)
                    completed_subcats += 1
                    current_status["progress"] = completed_subcats
                    current_status["catalog_count"] = len(all_products)
                    if completed_subcats % 10 == 0 or completed_subcats == len(subcats):
                        log.info(f"  [{completed_subcats}/{len(subcats)}] Subcategorias procesadas. Total prods: {len(all_products):,}")
                except Exception as e:
                    log.error(f"Error procesando subcat {sc[0]}: {e}")

        # Si no se pudo obtener ningun producto (ej. bloqueo temporal severo), no borrar la BD
        if not all_products:
            log.warning("No se pudo obtener ningun producto de las subcategorias. Conservando estado previo en BD.")
            current_status["is_running"] = False
            current_status["message"] = "Aviso: Sin productos extraidos (posible bloqueo temporal). Se preservo la BD."
            return 0, 0

        # Deduplicar productos de catalogo por codigo
        seen_codes = set()
        unique_catalog_prods = []
        for p in all_products:
            cid = p['codigo']
            if cid not in seen_codes:
                seen_codes.add(cid)
                unique_catalog_prods.append(p)

        log.info(f"Productos unicos obtenidos del catalogo: {len(unique_catalog_prods):,}")

        # Fase 2: Resolucion de GTIN/EAN mediante cache local y fichas PDP para faltantes
        existing_eans = get_existing_eans_laanonima()
        log.info(f"EANs de La Anonima precargados desde la BD: {len(existing_eans):,}")

        prods_needing_pdp = []
        resolved_from_cache = 0
        for p in unique_catalog_prods:
            cid = p['codigo']
            if cid in existing_eans:
                p['ean'] = existing_eans[cid]
                resolved_from_cache += 1
            elif p['id'] in existing_eans:
                p['ean'] = existing_eans[p['id']]
                resolved_from_cache += 1
            else:
                prods_needing_pdp.append(p)

        log.info(f"EANs resueltos instantaneamente via cache BD: {resolved_from_cache:,}")
        log.info(f"Productos nuevos que requieren consulta PDP: {len(prods_needing_pdp):,}")

        if prods_needing_pdp:
            current_status["message"] = f"Resolviendo EANs para {len(prods_needing_pdp):,} articulos nuevos..."
            def fetch_prod_gtin(p):
                s = get_session()
                time.sleep(random.uniform(0.05, 0.15))
                gtin = fetch_gtin_from_pdp(s, p)
                if gtin:
                    p['ean'] = gtin
                return p

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = [ex.submit(fetch_prod_gtin, p) for p in prods_needing_pdp]
                for future in as_completed(futures):
                    future.result()

        gtin_total_resolved = sum(1 for p in unique_catalog_prods if p.get('ean'))
        matched_lg = sum(1 for p in unique_catalog_prods if p.get('ean') and p['ean'] in ean_dict_lg)
        log.info(f"Total EANs resueltos: {gtin_total_resolved:,} ({matched_lg:,} coinciden con La Gallega)")

        # Fase 3: Escaneo puntual para EANs faltantes
        already_matched_eans = set(p['ean'] for p in unique_catalog_prods if p.get('ean'))
        missing_lg_eans = [e for e in ean_dict_lg.keys() if e not in already_matched_eans]
        log.info(f"EANs de La Gallega pendientes de busqueda directa: {len(missing_lg_eans):,}")

        if not quick_mode and missing_lg_eans:
            current_status["message"] = "Ejecutando escaneo puntual de EANs restantes..."
            deep_prods = run_laanonima_deep_scan(session, missing_lg_eans, max_lookups=500)
            for p in deep_prods:
                cid = p['codigo']
                if cid not in seen_codes:
                    seen_codes.add(cid)
                    unique_catalog_prods.append(p)
                else:
                    for existing in unique_catalog_prods:
                        if existing['codigo'] == cid and not existing.get('ean'):
                            existing['ean'] = p['ean']

        # Fase 4: Guardar catalogo y precios cruzados en Base de Datos de manera segura
        current_status["message"] = "Guardando productos y precios en base de datos..."
        total_saved, total_comps = update_laanonima_catalog_and_prices(unique_catalog_prods, ean_dict_lg)

        elapsed = round(time.time() - start_time, 2)
        current_status["is_running"] = False
        current_status["progress"] = current_status["total"]
        current_status["catalog_count"] = total_saved
        current_status["matched"] = total_comps
        current_status["message"] = f"Finalizado en {elapsed}s: {total_saved:,} prods en catalogo, {total_comps:,} coincidencias"

        log.info("=" * 60)
        log.info("PROCESO DE LA ANONIMA FINALIZADO CON EXITO")
        log.info(f"Catalogo completo guardado en 'products': {total_saved:,}")
        log.info(f"Coincidencias cruzadas en 'competitor_prices': {total_comps:,}")
        log.info(f"Tiempo total: {elapsed} segundos")
        log.info("=" * 60)
        return total_saved, total_comps

    except Exception as e:
        log.error(f"Error fatal en scraper de La Anonima: {e}", exc_info=True)
        current_status["is_running"] = False
        current_status["message"] = f"Error: {e}"
        raise

if __name__ == "__main__":
    is_quick = "--quick" in sys.argv
    main(quick_mode=is_quick)
