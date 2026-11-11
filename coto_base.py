"""
coto_base.py – Motor genérico de scraping para Coto Digital
Requiere: requests, selectolax
"""

import json, csv, time, logging, re
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import ssl
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BASE_BROWSE  = "https://www.cotodigital3.com.ar/sitios/cdigi/browse/_"
NRPP         = 50
MAX_WORKERS  = 20   # workers paralelos

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
    "Referer": "https://www.cotodigital3.com.ar/",
}


def get_json(url, retries=5, espera=3):
    ultimo_error = None
    for i in range(retries):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, context=SSL_CTX, timeout=20) as r:
                contenido = r.read()
            if not contenido:
                raise ValueError("Respuesta vacía del servidor")
            return json.loads(contenido)
        except (HTTPError, URLError, ValueError, json.JSONDecodeError) as e:
            ultimo_error = e
            log.warning(f"  intento {i+1}/{retries}: {e}  url={url[:80]}")
            time.sleep(espera * (i + 1))  # backoff: 3s, 6s, 9s, 12s, 15s
    log.error(f"  FALLO DEFINITIVO tras {retries} intentos: {url[:80]}")
    return None


def _find_results(data):
    """
    Busca recursivamente el dict con 'totalNumRecs' y 'records'
    dentro de data["contents"][0].
    """
    if not data:
        return None

    def _search(obj):
        if isinstance(obj, dict):
            if "totalNumRecs" in obj and "records" in obj:
                return obj
            for v in obj.values():
                r = _search(v)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = _search(item)
                if r is not None:
                    return r
        return None

    try:
        root = data["contents"][0]
    except (KeyError, IndexError):
        return None

    return _search(root)


def _parse_precio(texto):
    """Extrae float de strings como '$1.310,05' -> 1310.05"""
    if not texto:
        return None
    clean = re.sub(r"[^\d,.]", "", str(texto))
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def extraer_producto(rec_outer, cat_nombre):
    rec   = rec_outer.get("records", [{}])[0]
    attrs = rec.get("attributes", {})

    def get1(key, default=""):
        v = attrs.get(key, [default])
        return v[0] if isinstance(v, list) else v

    plu = get1("product.repositoryId").replace("prod", "")
    ean = get1("product.eanPrincipal")
    nombre = get1("product.displayName")
    marca  = (get1("product.MARCA") or get1("product.brand")).title()

    cat_parts = [c for c in attrs.get("allAncestors.displayName", [])
                 if c not in ("CotoDigital", "")]
    cat_full  = " > ".join(reversed(cat_parts)) if cat_parts else cat_nombre

    record_id = get1("record.id")
    url_prod  = (f"https://www.cotodigital3.com.ar/sitios/cdigi/producto/_/R-{record_id}"
                 if record_id else "")
    imagen    = get1("product.largeImage.url") or get1("product.mediumImage.url")

    desc_unidad = get1("product.unidades.descUnidad")
    c_formato   = get1("product.cFormato", "").strip()
    es_pesable  = get1("product.unidades.esPesable") == "1"

    try:
        precio_regular = float(get1("sku.activePrice") or 0) or None
    except Exception:
        precio_regular = None

    try:
        dp = json.loads(get1("sku.dtoPrice") or "{}")
    except Exception:
        dp = {}
    precio_sin_imp = dp.get("precioSinImp")

    try:
        precio_x_unidad = float(get1("sku.referencePrice") or 0) or None
    except Exception:
        precio_x_unidad = None

    if precio_x_unidad:
        if desc_unidad in ("KGS", "GRM"):
            unidad_label = "por 100 Gramos"
        elif desc_unidad in ("LTS", "LIT"):
            unidad_label = "por Lt"
        else:
            unidad_label = f"por {c_formato or desc_unidad or 'unidad'}"
    else:
        unidad_label = ""

    try:
        descuentos = json.loads(get1("product.dtoDescuentos", "[]"))
    except Exception:
        descuentos = []

    has_promo = False
    promo_units = 1
    promo_price = None
    promo_nombre = ""
    promo_texto = ""
    promo_regular = ""
    promo_detail = ""

    if descuentos and isinstance(descuentos, list) and len(descuentos) > 0:
        d = descuentos[0]
        texto_desc = (d.get("textoDescuento") or "").strip()
        texto_llev = (d.get("textoLlevando") or "").strip()
        promo_regular = (d.get("textoPrecioRegular") or "").strip()
        precio_desc_str = d.get("precioDescuento") or d.get("precioDesc")
        precio_dto = _parse_precio(precio_desc_str)
        comentarios = (d.get("comentarios") or "").strip()

        if not precio_regular and promo_regular:
            precio_regular = _parse_precio(promo_regular)

        if precio_dto:
            has_promo = True
            promo_price = precio_dto

            if texto_llev and texto_desc:
                promo_nombre = f"{texto_llev} ({texto_desc})"
            elif texto_llev:
                promo_nombre = texto_llev
            elif texto_desc:
                promo_nombre = texto_desc
            else:
                promo_nombre = "Oferta"

            promo_texto = promo_nombre

            # Determinar unidades necesarias
            m_llev = re.search(r'llevando\s*(\d+)', texto_llev, re.IGNORECASE)
            m_x = re.search(r'(\d+)\s*[xX]\s*(\d+)', texto_desc)
            m_2da = re.search(r'\b(2da|segunda)\b', texto_desc, re.IGNORECASE)
            m_3ra = re.search(r'\b(3ra|tercera)\b', texto_desc, re.IGNORECASE)
            m_4ta = re.search(r'\b(4ta|cuarta)\b', texto_desc, re.IGNORECASE)
            m_llev_desc = re.search(r'llevando\s*(\d+)', texto_desc, re.IGNORECASE)

            if m_llev:
                promo_units = int(m_llev.group(1))
            elif m_x:
                promo_units = int(m_x.group(1))
            elif m_2da:
                promo_units = 2
            elif m_3ra:
                promo_units = 3
            elif m_4ta:
                promo_units = 4
            elif m_llev_desc:
                promo_units = int(m_llev_desc.group(1))
            else:
                promo_units = 1

            p_reg_val = precio_regular or precio_dto
            reg_fmt = f"${p_reg_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            dto_fmt = f"${precio_dto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            if promo_units > 1:
                promo_detail = f"¡Hay promo en COTO! {promo_nombre}: tenés que comprar {promo_units} unidades y te queda a {dto_fmt} c/u (Precio regular: {reg_fmt})"
            else:
                promo_detail = f"¡Oferta directa en COTO! {promo_nombre}: comprando 1 unidad te queda a {dto_fmt} (Precio regular: {reg_fmt})"

            if comentarios and "acumulable" in comentarios.lower():
                promo_detail += f" - {comentarios}"

    # REGLA DE COMPARATIVA:
    # Si la promo exige comprar más de 1 unidad (ej. Llevando 2, 70% 2da, 3x2),
    # NO se puede usar el precio con descuento para comparar por unidad (se usa el regular).
    # Si no hay condición de cantidad (ej. 25%Dto directo), el precio con descuento SÍ es válido para comparar.
    if has_promo and promo_price:
        if promo_units > 1:
            precio_actual = precio_regular if precio_regular else promo_price
        else:
            precio_actual = promo_price
    else:
        precio_actual = precio_regular if precio_regular else 0.0

    return {
        "supermercado":    "coto",
        "plu":             plu,
        "ean":             ean,
        "nombre":          nombre,
        "marca":           marca,
        "categoria":       cat_full,
        "precio_actual":   precio_actual,
        "precio_regular":  precio_regular,
        "has_promo":       has_promo,
        "promo_price":     promo_price,
        "promo_units":     promo_units,
        "promo_detail":    promo_detail,
        "precio_sin_imp":  precio_sin_imp,
        "precio_x_unidad": precio_x_unidad,
        "unidad_label":    unidad_label,
        "unidad":          desc_unidad,
        "es_pesable":      es_pesable,
        "promo_texto":     promo_texto,
        "promo_regular":   promo_regular,
        "imagen":          imagen,
        "url":             url_prod,
        "fecha":           datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _fetch_page(args):
    """Worker: descarga una página y devuelve (n_code, offset, records, total)."""
    n_code, offset, cat_nombre = args
    url  = f"{BASE_BROWSE}/N-{n_code}?Nrpp={NRPP}&No={offset}&format=json"
    data = get_json(url)
    if not data:
        log.warning(f"  WARNING {cat_nombre}: sin respuesta en offset {offset}")
        return n_code, offset, [], 0

    main = _find_results(data)
    if main is None:
        try:
            claves = list(data["contents"][0].keys())
        except Exception:
            claves = list(data.keys())
        log.warning(f"  WARNING {cat_nombre}: no se encontro bloque de resultados. Claves raiz: {claves}")
        return n_code, offset, [], 0

    records = main.get("records", [])
    total   = int(main.get("totalNumRecs", 0))

    if not records:
        log.warning(f"  WARNING {cat_nombre}: 0 registros en offset {offset} (totalNumRecs={total})")

    return n_code, offset, records, total


def scrape_categoria(n_code, cat_nombre):
    """Scrapea todas las páginas de una categoría usando N-code Endeca."""
    log.info(f"-> {cat_nombre} (N-{n_code})")

    # ── Página 0: obtener total ───────────────────────────────────────────────
    _, _, records_p0, total = _fetch_page((n_code, 0, cat_nombre))
    if not records_p0:
        return []

    todos = [extraer_producto(r, cat_nombre) for r in records_p0]
    log.info(f"  offset 0 | {len(todos)}/{total}")

    if total <= NRPP:
        return todos

    # ── Páginas restantes en paralelo ─────────────────────────────────────────
    offsets = list(range(NRPP, total, NRPP))
    page_args = [(n_code, off, cat_nombre) for off in offsets]

    # Guardar resultados indexados por offset para mantener orden
    paginas = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_page, args): args[1] for args in page_args}
        for future in as_completed(futures):
            _, offset, records, _ = future.result()
            paginas[offset] = records
            log.info(f"  offset {offset} | {len(todos) + sum(len(v) for v in paginas.values())}/{total}")

    # Agregar en orden de offset
    for off in offsets:
        for rec in paginas.get(off, []):
            todos.append(extraer_producto(rec, cat_nombre))

    return todos


CAMPOS = [
    "supermercado", "plu", "ean", "nombre", "marca", "categoria",
    "precio_actual", "precio_regular", "precio_sin_imp",
    "precio_x_unidad", "unidad_label", "unidad", "es_pesable",
    "promo_texto", "promo_regular", "imagen", "url", "fecha",
]


def guardar(todos, output_dir: Path, nombre_archivo: str):
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    ruta_csv  = output_dir / f"{nombre_archivo}_{ts}.csv"
    ruta_json = output_dir / f"{nombre_archivo}_{ts}.json"

    with open(ruta_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS)
        writer.writeheader()
        writer.writerows(todos)

    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

    log.info(f"OK CSV  -> {ruta_csv}  ({len(todos)} prods)")
    log.info(f"OK JSON -> {ruta_json}")
    return ruta_csv
    
