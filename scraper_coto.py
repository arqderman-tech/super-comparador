"""
Scraper de COTO Digital adaptado para SuperComparador
Scrapea solo productos que coinciden con EAN de La Gallega
"""
import json
import time
import logging
import re
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import ssl
import random
import sqlite3
import os

# Importar el motor base de COTO
import sys
from pathlib import Path

# Descargar coto_base.py si no existe
COTO_BASE_URL = "https://raw.githubusercontent.com/arqderman-tech/COTOBOT/main/coto_base.py"
COTO_BASE_PATH = os.path.join(os.path.dirname(__file__), "coto_base.py")

if not os.path.exists(COTO_BASE_PATH):
    print(f"Descargando coto_base.py desde {COTO_BASE_URL}...")
    try:
        with urlopen(COTO_BASE_URL, timeout=30) as response:
            content = response.read()
        with open(COTO_BASE_PATH, 'wb') as f:
            f.write(content)
        print("coto_base.py descargado exitosamente")
    except Exception as e:
        print(f"Error descargando coto_base.py: {e}")
        sys.exit(1)

# Importar funciones del motor base
from coto_base import get_json, _find_results, _parse_precio, extraer_producto

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BASE_BROWSE = "https://www.cotodigital3.com.ar/sitios/cdigi/browse/_"
NRPP = 50
MAX_WORKERS = 10

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
    "Referer": "https://www.cotodigital3.com.ar/",
}

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

# Categorías completas de COTO Digital
CATEGORIAS = [
    # ALMACÉN
    {"n": "8pub5z",   "nombre": "Almacén"},
    {"n": "1y5dh9i",  "nombre": "Golosinas"},
    {"n": "s3bf1a",   "nombre": "Panadería"},
    {"n": "10kzbyj",  "nombre": "Snacks"},
    {"n": "ukd5id",   "nombre": "Cereales"},
    {"n": "1rtbab6",  "nombre": "Endulzantes"},
    {"n": "rv0frc",   "nombre": "Aderezos Y Salsas"},
    {"n": "dw58vw",   "nombre": "Infusiones"},
    {"n": "1t4efca",  "nombre": "Conservas"},
    {"n": "842qrm",   "nombre": "Harinas"},
    {"n": "12rkdi1",  "nombre": "Encurtidos"},
    {"n": "mj4aa8",   "nombre": "Mermeladas Y Dulces"},
    {"n": "os1anu",   "nombre": "Salsas Y Puré De Tomate"},
    {"n": "18r69ct",  "nombre": "Aceites Y Condimentos"},
    {"n": "nnh9fj",   "nombre": "Alimento Bebés Y Niños"},
    {"n": "c0x2yz",   "nombre": "Arroz Y Legumbres"},
    {"n": "1t0tm80",  "nombre": "Especias"},
    {"n": "tvb9c7",   "nombre": "Pasta Seca Y Rellenas"},
    {"n": "a6cxru",   "nombre": "Repostería"},
    {"n": "10vvk4q",  "nombre": "Sopas Y Saborizantes"},
    {"n": "mz3nfh",   "nombre": "Rebozador Y Pan Rallado"},
    {"n": "1yw5bwj",  "nombre": "Leche En Polvo"},
    {"n": "qe3p7f",   "nombre": "Suplementos Dietarios"},
    # BEBIDAS SIN ALCOHOL
    {"n": "17xtqou",  "nombre": "Aguas"},
    {"n": "n4l4r5",   "nombre": "Gaseosas"},
    {"n": "11la5tu",  "nombre": "Jugos"},
    {"n": "1mf4idu",  "nombre": "Amargos"},
    {"n": "1js4wik",  "nombre": "Energizantes"},
    {"n": "pz126",    "nombre": "Bebidas Isotónicas"},
    {"n": "1trypio",  "nombre": "Granadinas"},
    {"n": "wbn3k9",   "nombre": "Sidras Sin Alcohol"},
    {"n": "1u71aca",  "nombre": "Refrescos"},
    # BEBIDAS CON ALCOHOL
    {"n": "1lbig2w",  "nombre": "Aperitivos"},
    {"n": "137sk0z",  "nombre": "Cerveza"},
    {"n": "qxxhmm",   "nombre": "Champagne y Espumantes"},
    {"n": "uqiqtm",   "nombre": "Vinos"},
    {"n": "jigb2z",   "nombre": "Bebidas Blancas y Destiladas"},
    {"n": "t0seih",   "nombre": "Whiskys"},
    {"n": "1lnekxo",  "nombre": "Licores"},
    # FRESCOS
    {"n": "1d443r9",  "nombre": "Lácteos"},
    {"n": "1j6o93y",  "nombre": "Fiambres"},
    {"n": "1d0721n",  "nombre": "Quesos"},
    {"n": "176whnp",  "nombre": "Carnicería"},
    {"n": "6drhk5",   "nombre": "Aves"},
    {"n": "1e4im7l",  "nombre": "Pastas Frescas Y Tapas"},
    {"n": "l535ea",   "nombre": "Comidas Elaboradas"},
    {"n": "zxw18u",   "nombre": "Frutas Y Verduras"},
    {"n": "yxu4b7",   "nombre": "Pescadería"},
    {"n": "mtdtw6",   "nombre": "Huevos"},
    # CONGELADOS
    {"n": "wgo47s",   "nombre": "Pescadería Congelada"},
    {"n": "uh4qr",    "nombre": "Nuggets Y Bocaditos"},
    {"n": "15wfcx5",  "nombre": "Hamburguesas Y Milanesas"},
    {"n": "1vcscvz",  "nombre": "Papas Congeladas"},
    {"n": "cor40m",   "nombre": "Helados Y Postres"},
    {"n": "m17c6b",   "nombre": "Comidas Congeladas"},
    {"n": "8efwh3",   "nombre": "Vegetales Congelados"},
    {"n": "14w51iy",  "nombre": "Frutas Congeladas"},
    # LIMPIEZA
    {"n": "t2y8zd",   "nombre": "Lavado"},
    {"n": "ff27jr",   "nombre": "Papeles"},
    {"n": "6bv3h3",   "nombre": "Insecticidas"},
    {"n": "1ap445v",  "nombre": "Calzado"},
    {"n": "ohywgy",   "nombre": "Accesorios de Limpieza"},
    {"n": "1annh67",  "nombre": "Desodorantes de Ambiente"},
    {"n": "pz78zm",   "nombre": "Limpieza de Baño"},
    {"n": "1lonain",  "nombre": "Limpieza de Cocina"},
    {"n": "bf8h0x",   "nombre": "Limpieza de Pisos y Superficies"},
    {"n": "1ogrrlx",  "nombre": "Lavandinas"},
    # PERFUMERÍA Y CUIDADO PERSONAL
    {"n": "1w8xczk",  "nombre": "Cuidado del Cabello"},
    {"n": "721a4h",   "nombre": "Higiene Personal"},
    {"n": "2f9qa1",   "nombre": "Desodorantes y Antitranspirantes"},
    {"n": "1a8xcmp",  "nombre": "Pañales e Incontinencia"},
    {"n": "sstxyh",   "nombre": "Cuidado Personal"},
    {"n": "iak9sv",   "nombre": "Cuidado Bucal"},
    {"n": "1csoql4",  "nombre": "Protección Femenina"},
    {"n": "jpga0a",   "nombre": "Cremas de Belleza"},
    {"n": "7d4hhu",   "nombre": "Cuidado de la Piel"},
    {"n": "14mninw",  "nombre": "Accesorios Perfumería"},
    {"n": "1c9xpsw",  "nombre": "Cuidado del Bebé"},
    {"n": "hooak",    "nombre": "Farmacia"},
    {"n": "6hnf6f",   "nombre": "Colonias y Perfumes"},
    {"n": "85l8um",   "nombre": "Cosméticos"},
    # MASCOTAS
    {"n": "i0b09i",   "nombre": "Mascotas"},
]


def get_lagallega_eans():
    """Obtiene todos los EAN de La Gallega desde la base de datos"""
    from import_carrefour import clean_ean
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000;")
    c = conn.cursor()

    c.execute("SELECT id, ean, name, price_promo FROM products WHERE ean != '' AND ean IS NOT NULL AND supermarket = 'La Gallega'")
    rows = c.fetchall()

    conn.close()

    # Crear diccionario: ean -> (id, name, price_promo)
    ean_dict = {}
    for row in rows:
        c_ean = clean_ean(row[1])
        if c_ean:
            ean_dict[c_ean] = {"id": row[0], "name": row[2] or "", "price_promo": row[3]}

    log.info(f"EAN de La Gallega cargados: {len(ean_dict)} productos")
    return ean_dict


def _fetch_page(args):
    """Worker: descarga una página y devuelve (n_code, offset, records, total)."""
    n_code, offset, cat_nombre = args
    url = f"{BASE_BROWSE}/N-{n_code}?Nrpp={NRPP}&No={offset}&format=json"
    data = get_json(url)
    if not data:
        log.warning(f"  WARNING {cat_nombre}: sin respuesta en offset {offset}")
        return n_code, offset, [], 0

    main = _find_results(data)
    if main is None:
        return n_code, offset, [], 0

    records = main.get("records", [])
    total = int(main.get("totalNumRecs", 0))

    return n_code, offset, records, total


def scrape_categoria(n_code, cat_nombre):
    """Scrapea todos los productos de una categoría de COTO Digital"""
    log.info(f"-> {cat_nombre} (N-{n_code})")

    # Página 0: obtener total
    _, _, records_p0, total = _fetch_page((n_code, 0, cat_nombre))
    if not records_p0:
        return []

    productos = []
    for rec in records_p0:
        prod = extraer_producto(rec, cat_nombre)
        if prod.get("nombre"):
            productos.append(prod)

    log.info(f"  {cat_nombre}: pág 0 ({len(productos)} prods) | Total en web: {total}")

    if total <= NRPP:
        return productos

    # Páginas restantes en paralelo
    offsets = list(range(NRPP, total, NRPP))
    page_args = [(n_code, off, cat_nombre) for off in offsets]

    paginas = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_page, args): args[1] for args in page_args}
        for future in as_completed(futures):
            _, offset, records, _ = future.result()
            paginas[offset] = records

    # Procesar páginas restantes en orden
    for off in offsets:
        for rec in paginas.get(off, []):
            prod = extraer_producto(rec, cat_nombre)
            if prod.get("nombre"):
                productos.append(prod)

    return productos


def update_coto_catalog_and_prices(productos, ean_dict):
    """
    Inserta el catálogo completo de COTO en la tabla 'products'
    y los cruces coincidentes con La Gallega en 'competitor_prices'.
    """
    from db import init_db, extract_brand
    init_db()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    c = conn.cursor()

    from import_carrefour import check_pack_mismatch, clean_ean, clean_image_url

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

    # Obtener imágenes existentes en DB de Coto (para no perderlas si el scraper no trae foto)
    c.execute("""
    SELECT id, ean, image_url
    FROM products
    WHERE supermarket = 'Coto' AND image_url != '' AND image_url IS NOT NULL
    """)
    existing_coto_img_by_id = {}
    existing_coto_img_by_ean = {}
    for p_id, p_ean, p_img in c.fetchall():
        c_img = clean_image_url(p_img)
        if c_img:
            existing_coto_img_by_id[p_id] = c_img
            c_ean = clean_ean(p_ean)
            if c_ean:
                existing_coto_img_by_ean[c_ean] = c_img

    # 1. Catálogo completo en 'products'
    products_inserts = []
    vistos_id = set()

    for p in productos:
        plu = str(p.get("plu") or "").strip()
        ean = clean_ean(p.get("ean"))
        prod_id = f"coto_{plu}" if plu else f"coto_{ean}"
        if not prod_id or prod_id in vistos_id:
            continue
        vistos_id.add(prod_id)

        nombre = (p.get("nombre") or "").strip()
        if not nombre:
            continue

        cat_full = p.get("categoria") or "General"
        cat_simple = cat_full.split(" > ")[-1] if " > " in cat_full else cat_full

        marca = (p.get("marca") or "").strip()
        if not marca or marca.lower() in ("", "coto"):
            marca = extract_brand(nombre) or "COTO"

        p_act = float(p.get("precio_actual") or 0.0)
        p_reg = float(p.get("precio_regular") or p_act)
        if p_reg <= 0:
            p_reg = p_act
        if p_act <= 0:
            p_act = p_reg

        # Usar solo el campo has_promo de la API de COTO, no recalcular
        has_p = 1 if p.get("has_promo") else 0
        disc = round(((p_reg - p_act) / p_reg) * 100, 1) if (p_reg > p_act > 0) else 0.0

        # Regla de Oro de Imágenes:
        if ean and ean in lg_img_by_ean:
            img = lg_img_by_ean[ean]
        elif prod_id in existing_coto_img_by_id:
            img = existing_coto_img_by_id[prod_id]
        elif ean and ean in existing_coto_img_by_ean:
            img = existing_coto_img_by_ean[ean]
        else:
            img = clean_image_url(p.get("imagen") or "")

        url = p.get("url") or ""
        p_units = int(p.get("promo_units") or 1)
        p_det = p.get("promo_detail") or ""

        products_inserts.append((
            prod_id, ean, nombre, cat_simple, cat_full, marca,
            p_reg, p_act, has_p, disc, img, url, 'Coto', p_units, p_det
        ))

    log.info(f"Guardando catálogo completo de COTO en 'products': {len(products_inserts):,} productos...")
    c.execute("DELETE FROM products WHERE supermarket = 'Coto'")
    c.executemany("""
    INSERT OR REPLACE INTO products (
        id, ean, name, category, category_id, brand,
        price_regular, price_promo, is_promo, discount_pct,
        image_url, url, supermarket, pack_units, pack_detail
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, products_inserts)
    conn.commit()

    # 2. Cruce con La Gallega para 'competitor_prices'
    c.execute("DELETE FROM competitor_prices WHERE supermarket = 'Coto'")
    conn.commit()

    comp_inserts = []
    coincidencias = {}  # Guardar coincidencias para el histórico
    pack_adjusted_count = 0
    anomaly_count = 0

    for prod in productos:
        ean = clean_ean(prod.get("ean"))
        if not ean or ean not in ean_dict:
            continue

        raw_coto_price = float(prod.get("precio_actual") or 0.0)
        if raw_coto_price <= 0:
            continue

        lg_info = ean_dict[ean]
        lg_price = float(lg_info.get("price_promo") or 0.0)
        lg_name = lg_info.get("name", "")
        coto_name = prod.get("nombre", "")

        is_pack_adj, norm_price, pack_detail = check_pack_mismatch(
            lg_name, coto_name, lg_price, raw_coto_price
        )

        if is_pack_adj:
            coto_price = norm_price
            has_promo = 1
            promo_units = 1
            promo_detail = pack_detail
            pack_adjusted_count += 1
        else:
            coto_price = raw_coto_price
            has_promo = 1 if prod.get("has_promo") else 0
            promo_units = prod.get("promo_units", 1)
            promo_detail = prod.get("promo_detail", "")

        if lg_price > 0:
            diff_pct = round(((coto_price - lg_price) / lg_price) * 100, 1)
        else:
            diff_pct = 0.0

        if diff_pct < -70 or diff_pct > 200:
            anomaly_count += 1
            continue

        reg_p = float(prod.get("precio_regular") or raw_coto_price)
        promo_p = prod.get("promo_price")
        if (not promo_p or promo_p == reg_p) and has_promo and promo_detail:
            m_promo = re.search(r'te queda a \$?([\d\.]+,\d{2})', promo_detail)
            if m_promo:
                try:
                    promo_p = float(m_promo.group(1).replace('.', '').replace(',', '.'))
                except ValueError:
                    pass

        comp_inserts.append((
            lg_info["id"],
            ean,
            "Coto",
            coto_price,
            diff_pct,
            reg_p,
            promo_p,
            has_promo,
            promo_units,
            promo_detail
        ))

        # Guardar coincidencia para el histórico
        coincidencias[ean] = (ean, coto_price)

    c.executemany("""
    INSERT INTO competitor_prices (
        product_id, ean, supermarket, price, difference_pct,
        regular_price, promo_price, has_promo, promo_units, promo_detail
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, comp_inserts)
    conn.commit()

    # Almacenar histórico de precios de COTO
    from datetime import datetime
    today_int = int(datetime.now().strftime("%Y%m%d"))
    from db import SUPERMARKET_CODES
    history_inserts = []
    for ean, coto_price in coincidencias.values():
        history_inserts.append((ean, today_int, coto_price, SUPERMARKET_CODES['Coto']))

    if history_inserts:
        c.executemany("""
        INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
        VALUES (?, ?, ?, ?)
        """, history_inserts)
        conn.commit()
        log.info(f"Historico de COTO guardado: {len(history_inserts):,} registros")

    conn.close()

    log.info(f"Packs de COTO ajustados equitativamente: {pack_adjusted_count}")
    if anomaly_count > 0:
        log.info(f"Omitidas {anomaly_count} anomalías extremas en COTO (>200% o <-70%)")
    log.info(f"Actualizados {len(comp_inserts):,} precios de COTO coincidentes en competitor_prices")
    return len(products_inserts), len(comp_inserts)


def main():
    log.info("="*60)
    log.info("SCRAPER DE COTO DIGITAL - CATÁLOGO COMPLETO")
    log.info("="*60)

    start_time = time.time()

    # Obtener EAN de La Gallega para el cruce comparativo
    ean_dict = get_lagallega_eans()
    log.info(f"Cargados {len(ean_dict):,} EANs de La Gallega para cruce multitienda")

    # Excluir la raíz "Almacén" para no duplicar subcategorías
    cats_sin_raiz = [c for c in CATEGORIAS if c["n"] != "8pub5z"]

    todos_productos = []

    def scrape_cat(cat):
        prods = scrape_categoria(cat["n"], cat["nombre"])
        return cat["n"], prods

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scrape_cat, cat): cat for cat in cats_sin_raiz}
        for future in futures:
            n_code, prods = future.result()
            todos_productos.extend(prods)
            log.info(f"  Acumulado total COTO: {len(todos_productos):,} productos")

    # Deduplicar por PLU y EAN
    vistos = set()
    unicos = []
    for p in todos_productos:
        key = str(p.get("plu") or "") + "_" + str(p.get("ean") or "")
        if key not in vistos:
            vistos.add(key)
            unicos.append(p)

    log.info(f"\nTotal único COTO: {len(unicos):,} productos descargados")

    # Actualizar catálogo completo en 'products' y cruce en 'competitor_prices'
    total_prods, total_comps = update_coto_catalog_and_prices(unicos, ean_dict)

    elapsed = round(time.time() - start_time, 2)
    log.info(f"\n{'='*60}")
    log.info(f"PROCESO DE COTO DIGITAL COMPLETADO")
    log.info(f"Catálogo completo en DB ('products'): {total_prods:,}")
    log.info(f"Coincidencias cruzadas ('competitor_prices'): {total_comps:,}")
    log.info(f"Tiempo total: {elapsed} segundos")
    log.info(f"{'='*60}")
    log.info(f"PROCESO COMPLETADO")
    log.info(f"Productos de COTO encontrados: {len(unicos)}")
    log.info(f"Catálogo completo en DB: {total_prods:,}")
    log.info(f"Precios actualizados en DB: {total_comps:,}")
    log.info(f"Tiempo total: {elapsed} segundos")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
