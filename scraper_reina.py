"""
Scraper de La Reina Online adaptado para SuperComparador
Scrapea productos filtrando por EAN de La Gallega
"""
import asyncio
import sqlite3
import re
import time
import logging
import sys
from datetime import datetime
from playwright.async_api import async_playwright
from pathlib import Path

# Configurar UTF-8 para Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_URL = "https://www.lareinaonline.com.ar"
DELAY = 0.5
N_PARALELO = 3
PP_POR_PAGINA = 50
MAX_PAGINAS_SEGURIDAD = 100
MAX_RETRIES = 3
WAIT_INICIAL_MS = 1500

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

RECURSOS_BLOQUEADOS = {"image", "media", "font", "stylesheet"}

DB_PATH = Path(__file__).parent / "database.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def limpiar_precio(texto):
    if not texto:
        return None
    m = re.search(r'([\d\.]+,\d{2})', str(texto))
    if m:
        try:
            return float(m.group(1).replace('.', '').replace(',', '.'))
        except ValueError:
            pass
    limpio = re.sub(r"[^\d,]", "", str(texto)).replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None


def slug_a_nombre(slug):
    slug = slug.replace(" ", "n")
    partes = [p.strip() for p in slug.split("/") if p.strip()]
    return " > ".join(p.title() for p in partes)


def rubro_de_slug(slug):
    return slug.split("/")[0].lower().strip() if slug else ""


def get_lagallega_eans():
    """Obtiene todos los EAN de La Gallega desde la base de datos"""
    from import_carrefour import clean_ean
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000;")
    c = conn.cursor()

    c.execute("SELECT id, ean, price_promo, image_url, name FROM products WHERE ean != '' AND ean IS NOT NULL AND supermarket = 'La Gallega'")
    rows = c.fetchall()

    conn.close()

    ean_dict = {}
    for row in rows:
        c_ean = clean_ean(row[1])
        if c_ean:
            ean_dict[c_ean] = {
                "id": row[0],
                "price_promo": row[2],
                "image_url": row[3],
                "name": row[4] or ""
            }

    print(f"EAN de La Gallega cargados: {len(ean_dict)} productos")
    return ean_dict


def log(msg):
    print(msg, flush=True)


async def warmup(page):
    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[WARN] warmup falló: {e}")


async def preparar_pagina(browser):
    page = await browser.new_page(user_agent=USER_AGENT)

    async def bloquear(route):
        if route.request.resource_type in RECURSOS_BLOQUEADOS:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", bloquear)
    return page


async def descubrir_categorias(page):
    log(f"Descubriendo catálogo desde el menú...")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)
    html = await page.content()

    patron = re.compile(
        r'href="productosnl\.asp\?nl=(\d{8})&(?:amp;)?TM=cx&(?:amp;)?categoria=([^"&]+)"',
        re.IGNORECASE
    )

    categorias = []
    vistos = set()
    for m in patron.finditer(html):
        nl, slug = m.group(1), m.group(2)
        if nl in vistos:
            continue
        vistos.add(nl)
        categorias.append({
            "nl": nl,
            "slug": slug,
            "rubro": rubro_de_slug(slug),
            "nombre": slug_a_nombre(slug),
        })

    log(f"Descubiertas {len(categorias)} categorías de nivel hoja\n")
    return categorias


async def scrapear_pagina(page, categoria_nombre, ean_dict):
    productos = []
    items = await page.query_selector_all("li.cuadProd")

    for item in items:
        try:
            desc_el = await item.query_selector(".desc")
            nombre = (await desc_el.inner_text()).strip() if desc_el else ""

            oferta_el = await item.query_selector(".OferProd")
            oferta_texto = (await oferta_el.inner_text()).strip() if oferta_el else ""

            izqdes_el = await item.query_selector(".precio .izqdes")
            izq_el = await item.query_selector(".precio .izq")

            regular_price = None
            promo_price = None
            promo_units = 1

            if izqdes_el:
                regular_price = limpiar_precio(await izqdes_el.inner_text())
            if izq_el:
                promo_price = limpiar_precio(await izq_el.inner_text())

            if promo_price is None and regular_price is not None:
                promo_price = regular_price
            elif regular_price is None and promo_price is not None:
                regular_price = promo_price
            elif regular_price is None and promo_price is None:
                precio_el = await item.query_selector(".precio")
                if precio_el:
                    ptxt = (await precio_el.inner_text()).strip()
                    precios_encontrados = re.findall(r'(\d[\d\.]*,\d{2})', ptxt)
                    if len(precios_encontrados) >= 2:
                        regular_price = limpiar_precio(precios_encontrados[0])
                        promo_price = limpiar_precio(precios_encontrados[1])
                    elif len(precios_encontrados) == 1:
                        promo_price = limpiar_precio(precios_encontrados[0])
                        regular_price = promo_price

            if not promo_price or promo_price <= 0:
                continue

            has_promo = 0
            discount_pct = 0.0
            promo_detail = ""

            if regular_price and promo_price and regular_price > promo_price:
                has_promo = 1
                discount_pct = round(((regular_price - promo_price) / regular_price) * 100, 1)
                promo_detail = oferta_texto if oferta_texto else f"Descuento: {discount_pct}%"
            elif oferta_texto:
                has_promo = 1
                m_pct = re.search(r'(\d+)\s*%', oferta_texto)
                if m_pct:
                    discount_pct = float(m_pct.group(1))
                promo_detail = oferta_texto

            # Extraer cantidad de unidades de la promo (ej: "3 u.", "3x2", "2x1")
            m_units = re.search(r'(\d+)\s*(?:u\.|x|unidades)', oferta_texto.lower())
            if m_units:
                promo_units = int(m_units.group(1))
            else:
                m_x = re.search(r'(\d+)x(\d+)', oferta_texto.lower())
                if m_x:
                    promo_units = int(m_x.group(1))

            ean = ""
            url_prod = ""
            link_el = await item.query_selector("a[href*='productosdet.asp']")
            if link_el:
                href = await link_el.get_attribute("href") or ""
                m = re.search(r"Pr=(\d+)", href)
                if m:
                    ean = m.group(1)
                if href.startswith("http"):
                    url_prod = href
                else:
                    url_prod = f"{BASE_URL}/{href.lstrip('/')}"

            imagen = ""
            img_el = await item.query_selector("img")
            if img_el:
                src = await img_el.get_attribute("src") or ""
                if src.startswith("http"):
                    imagen = src
                else:
                    imagen = f"{BASE_URL}/{src.lstrip('/')}"
                if not ean:
                    m_img = re.search(r"Articulos/(\d+)\.", src)
                    if m_img:
                        ean = m_img.group(1)

            if not ean:
                html_li = await item.inner_html()
                m_id = re.search(r"(?:BotonAg|Articulos\/|P)(\d+)", html_li)
                if m_id:
                    ean = m_id.group(1)

            if nombre:
                productos.append({
                    "ean": ean,
                    "nombre": nombre,
                    "categoria": categoria_nombre,
                    "precio": promo_price,
                    "precio_regular": regular_price,
                    "precio_promo": promo_price,
                    "oferta": bool(has_promo),
                    "discount_pct": discount_pct,
                    "promo_detail": promo_detail,
                    "promo_units": promo_units,
                    "url": url_prod,
                    "imagen": imagen,
                })
        except Exception:
            continue

    return productos


async def cargar_pagina_con_retry(page, url, es_primera=False):
    for intento in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            if intento < MAX_RETRIES:
                await asyncio.sleep(0.8 * intento)
                continue
            return False

        espera = WAIT_INICIAL_MS if (es_primera and intento == 1) else int(DELAY * 1000)
        await page.wait_for_timeout(espera)

        html = await page.content()
        if "cuadProd" in html or "listaProds" in html or "Resultados" in html:
            return True

        if intento < MAX_RETRIES:
            await asyncio.sleep(0.8 * intento)

    return False


async def scrapear_categoria(page, cat, ean_dict, es_primera=False):
    nombre = cat["nombre"]
    nl = cat["nl"]
    todos = []
    eans_vistos = set()

    url_base = f"{BASE_URL}/productosnl.asp?nl={nl}&TM=cx&PP={PP_POR_PAGINA}"

    pagina = 1
    while pagina <= MAX_PAGINAS_SEGURIDAD:
        ok = await cargar_pagina_con_retry(
            page, f"{url_base}&pg={pagina}",
            es_primera=(es_primera and pagina == 1)
        )
        if not ok:
            break

        prods = await scrapear_pagina(page, nombre, ean_dict)

        if not prods:
            break

        # Dedupe por EAN
        prods_nuevos = []
        for p in prods:
            ean_p = p.get("ean", "")
            if ean_p and ean_p in eans_vistos:
                continue
            if ean_p:
                eans_vistos.add(ean_p)
            prods_nuevos.append(p)

        # Si no hay productos nuevos (duplicados totales), terminamos
        if pagina > 1 and len(prods_nuevos) == 0:
            break

        todos.extend(prods_nuevos)

        # Si la página está vacía (0 productos), terminamos
        if len(prods) == 0:
            break

        pagina += 1

    # Enriquecer con datos de La Gallega para los que coincidan por EAN
    coincidencias = 0
    for p in todos:
        ean_p = p.get("ean", "")
        if ean_p and ean_p in ean_dict:
            lg_info = ean_dict[ean_p]
            p["lagallega_id"] = lg_info["id"]
            p["lagallega_price"] = lg_info["price_promo"]
            p["lagallega_image"] = lg_info.get("image_url", "")
            coincidencias += 1

    log(f"  {nombre}: {len(todos)} productos ({coincidencias} coincidencias Gallega, páginas: {pagina})")
    return todos


async def worker(browser, cola, resultados, ean_dict, total_cats):
    page = await preparar_pagina(browser)
    es_primera = True

    try:
        while True:
            try:
                idx, cat = cola.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                prods = await scrapear_categoria(page, cat, ean_dict, es_primera=es_primera)
            except Exception as e:
                log(f"[ERROR] {cat['nombre']}: {e}")
                prods = []

            es_primera = False
            resultados.append(prods)
    finally:
        await page.close()


def update_reina_catalog_and_prices(productos, ean_dict=None):
    """Actualiza la tabla products y competitor_prices con los resultados de La Reina"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    c = conn.cursor()

    from import_carrefour import clean_ean, clean_image_url
    from db import extract_brand

    # Obtener fotos de La Gallega por EAN (referencia principal)
    c.execute("""
    SELECT ean, image_url
    FROM products
    WHERE supermarket = 'La Gallega' AND ean != '' AND ean IS NOT NULL AND image_url != '' AND image_url IS NOT NULL
    """)
    lg_img_by_ean = {}
    for r_ean, r_img in c.fetchall():
        c_ean = clean_ean(r_ean)
        c_img = clean_image_url(r_img)
        if c_ean and c_img:
            lg_img_by_ean[c_ean] = c_img

    # Obtener imágenes existentes en DB de La Reina
    c.execute("""
    SELECT id, ean, image_url
    FROM products
    WHERE supermarket = 'La Reina' AND image_url != '' AND image_url IS NOT NULL
    """)
    existing_reina_img_by_id = {}
    existing_reina_img_by_ean = {}
    for p_id, p_ean, p_img in c.fetchall():
        c_img = clean_image_url(p_img)
        if c_img:
            existing_reina_img_by_id[p_id] = c_img
            c_ean = clean_ean(p_ean)
            if c_ean:
                existing_reina_img_by_ean[c_ean] = c_img

    # 1. Catálogo completo en 'products'
    products_inserts = []
    vistos_ean = set()
    for prod in productos:
        ean = clean_ean(prod.get("ean"))
        nombre = (prod.get("nombre") or "").strip()
        if not ean or not nombre or ean in vistos_ean:
            continue
        vistos_ean.add(ean)

        cat = prod.get("categoria") or "General"
        reina_price = float(prod.get("precio") or prod.get("precio_promo") or 0.0)
        if reina_price <= 0:
            continue

        regular_price = float(prod.get("precio_regular") or reina_price)
        has_promo = 1 if prod.get("oferta") else 0
        discount_pct = float(prod.get("discount_pct") or 0.0)
        promo_detail = (prod.get("promo_detail") or "").strip()
        promo_units = int(prod.get("promo_units") or 1)

        custom_id = f"reina_{ean}"
        # Regla de Oro de Imágenes:
        if ean in lg_img_by_ean:
            img = lg_img_by_ean[ean]
        elif custom_id in existing_reina_img_by_id:
            img = existing_reina_img_by_id[custom_id]
        elif ean in existing_reina_img_by_ean:
            img = existing_reina_img_by_ean[ean]
        else:
            img = clean_image_url(prod.get("imagen") or "")

        url = prod.get("url") or f"https://www.lareinaonline.com.ar/busqueda.asp?txt={ean}"
        brand = extract_brand(nombre)

        products_inserts.append((
            custom_id, ean, nombre, cat, "", brand,
            regular_price, reina_price, has_promo, discount_pct,
            img, url, "La Reina", promo_units, promo_detail, 1
        ))

    c.execute("DELETE FROM products WHERE supermarket = 'La Reina'")
    c.executemany("""
    INSERT OR REPLACE INTO products (
        id, ean, name, category, category_id, brand,
        price_regular, price_promo, is_promo, discount_pct,
        image_url, url, supermarket, pack_units, pack_detail, is_available
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, products_inserts)
    conn.commit()

    # 2. Cruce para competitor_prices
    c.execute("DELETE FROM competitor_prices WHERE supermarket = 'La Reina'")
    conn.commit()

    if not ean_dict:
        ean_dict = get_lagallega_eans()

    comp_inserts = []
    pack_adjusted_count = 0
    anomaly_count = 0

    for prod in productos:
        ean = clean_ean(prod.get("ean"))
        raw_reina_price = float(prod.get("precio") or prod.get("precio_promo") or 0.0)
        if not ean or raw_reina_price <= 0 or ean not in ean_dict:
            continue

        lg_info = ean_dict[ean]
        lg_price = float(lg_info.get("price_promo") or 0.0)
        lg_name = lg_info.get("name", "")
        reina_name = prod.get("nombre", "")

        # En La Reina no hay promociones de 2da al 70 ni packs divididos; se usa el precio directo
        reina_price = raw_reina_price
        regular_price = float(prod.get("precio_regular") or reina_price)
        has_promo = 1 if prod.get("oferta") else 0
        promo_units = int(prod.get("promo_units") or 1)
        promo_detail = (prod.get("promo_detail") or "").strip()
        promo_p = reina_price if has_promo else None

        diff_pct = round(((reina_price - lg_price) / lg_price) * 100, 1) if lg_price > 0 else 0.0

        # Omitir anomalías extremas
        if diff_pct < -70 or diff_pct > 200:
            anomaly_count += 1
            continue

        comp_inserts.append((
            lg_info["id"], ean, "La Reina", reina_price, diff_pct,
            regular_price, promo_p, has_promo, promo_units, promo_detail
        ))

    c.executemany("""
    INSERT INTO competitor_prices (
        product_id, ean, supermarket, price, difference_pct,
        regular_price, promo_price, has_promo, promo_units, promo_detail
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, comp_inserts)
    conn.commit()

    # Almacenar histórico de precios de La Reina (Catálogo completo con EAN)
    from datetime import datetime
    today_int = int(datetime.now().strftime("%Y%m%d"))
    from db import SUPERMARKET_CODES
    history_inserts = []
    vistos_reina_hist = set()
    reina_code = SUPERMARKET_CODES['La Reina']
    for prod in productos:
        ean = clean_ean(prod.get("ean"))
        reina_price = float(prod.get("precio") or prod.get("precio_promo") or 0.0)
        if ean and reina_price > 0 and ean not in vistos_reina_hist:
            vistos_reina_hist.add(ean)
            history_inserts.append((ean, today_int, reina_price, reina_code))

    if history_inserts:
        c.executemany("""
        INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
        VALUES (?, ?, ?, ?)
        """, history_inserts)
        conn.commit()
        print(f"Historico de La Reina guardado: {len(history_inserts):,} registros en price_history")

    conn.close()

    print(f"Catálogo completo de La Reina en products: {len(products_inserts)}")
    print(f"Packs de La Reina ajustados equitativamente: {pack_adjusted_count}")
    if anomaly_count > 0:
        print(f"Omitidas {anomaly_count} anomalías extremas en La Reina (>200% o <-70%)")
    print(f"Actualizados {len(comp_inserts)} precios coincidentes de La Reina en competitor_prices")
    return len(products_inserts), len(comp_inserts)


def update_competitor_prices(productos):
    """Compatibilidad hacia atrás si se llama directamente"""
    total_p, total_c = update_reina_catalog_and_prices(productos)
    return total_c


async def main():
    log("="*60)
    log("SCRAPER DE LA REINA ONLINE - Catálogo Completo y Cruce Multitienda")
    log("="*60)

    start_time = time.time()

    # Obtener EAN de La Gallega
    ean_dict = get_lagallega_eans()
    if not ean_dict:
        log.error("No se encontraron EAN de La Gallega")
        return

    todos_productos = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        page_warmup = await browser.new_page(user_agent=USER_AGENT)
        log(f"Config: DELAY={DELAY}s  PARALELO={N_PARALELO}  PP={PP_POR_PAGINA}")
        log("")

        log("Warmup...")
        await warmup(page_warmup)
        log("Warmup OK\n")

        categorias = await descubrir_categorias(page_warmup)
        await page_warmup.close()

        if not categorias:
            log("[ERROR] No se descubrieron categorías.")
            await browser.close()
            return

        cola = asyncio.Queue()
        for i, cat in enumerate(categorias, 1):
            await cola.put((i, cat))

        log(f"Lanzando {N_PARALELO} workers en paralelo\n")

        resultados_compartidos = []
        workers = [
            asyncio.create_task(
                worker(browser, cola, resultados_compartidos,
                       ean_dict, len(categorias))
            )
            for _ in range(N_PARALELO)
        ]
        await asyncio.gather(*workers)
        await browser.close()

    for r in resultados_compartidos:
        todos_productos.extend(r)

    # Deduplicar por EAN
    vistos = set()
    unicos = []
    for p in todos_productos:
        if p["ean"] not in vistos:
            vistos.add(p["ean"])
            unicos.append(p)

    log(f"\nTotal productos únicos La Reina: {len(unicos)}")

    # Actualizar catálogo en products y cruce en competitor_prices
    total_prods, total_comps = update_reina_catalog_and_prices(unicos, ean_dict)

    elapsed = round(time.time() - start_time, 2)
    log(f"\n{'='*60}")
    log(f"PROCESO COMPLETADO")
    log(f"Catálogo completo en DB ('products'): {total_prods}")
    log(f"Coincidencias en DB ('competitor_prices'): {total_comps}")
    log(f"Tiempo total: {elapsed} segundos")
    log(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
