import requests
import pandas as pd
import time
from datetime import datetime
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).resolve().parent
BASE_URL        = "https://www.carrefour.com.ar"
PAGE_SIZE       = 50
MAX_PRODS       = 2500
MAX_WORKERS_CAT = 8    # categorías en paralelo (no más para no banear)
MAX_WORKERS_PAG = 6    # páginas en paralelo dentro de cada categoría
OUTPUT_DIR      = BASE_DIR / "output_carrefour"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
    "Referer": "https://www.carrefour.com.ar/",
    "Connection": "keep-alive",
}

# ── TODAS LAS CATEGORÍAS (86) ─────────────────────────────────────────────────
CATEGORIAS = [
    ("Almacén", "Aceites y vinagres", 161, 162), ("Almacén", "Pastas secas", 161, 168),
    ("Almacén", "Arroz y legumbres", 161, 172), ("Almacén", "Harinas", 161, 176),
    ("Almacén", "Enlatados y Conservas", 161, 183), ("Almacén", "Sal aderezos y saborizadores", 161, 190),
    ("Almacén", "Caldos sopas y puré", 161, 195), ("Almacén", "Repostería y postres", 161, 199),
    ("Almacén", "Snacks", 161, 214), ("Almacén", "Comidas instantáneas", 161, 658),
    ("Almacén", "Golosinas y chocolates", 222, 208), ("Almacén", "Chocolates", 208, 209),
    ("Almacén", "Alfajores", 208, 210), ("Almacén", "Caramelos, gomitas y chupetines", 208, 211),
    ("Almacén", "Chicles", 208, 212), ("Almacén", "Bocaditos, confites y turrones", 208, 213),
    ("Almacén", "Galletitas y tostadas", 222, 223), ("Almacén", "Galletitas dulces", 223, 224),
    ("Almacén", "Bizcochitos", 223, 225), ("Almacén", "Galletitas de agua", 223, 226),
    ("Almacén", "Galletas de arroz", 223, 227), ("Almacén", "Tostadas, grisines y marineras", 223, 228),
    ("Almacén", "Budines y magdalenas", 222, 229), ("Almacén", "Yerba", 222, 232),
    ("Almacén", "Café", 222, 233), ("Almacén", "Café molido y en grano", 233, 234),
    ("Almacén", "Café instantáneo", 233, 235), ("Almacén", "Cápsulas de café", 233, 236),
    ("Almacén", "Filtros de café", 233, 237), ("Almacén", "Infusiones", 222, 238),
    ("Almacén", "Azúcar y endulzantes", 222, 242), ("Almacén", "Azúcar", 242, 243),
    ("Almacén", "Edulcorantes y endulzantes", 242, 244), ("Almacén", "Mermeladas y dulces", 222, 246),
    ("Almacén", "Miel", 246, 245), ("Almacén", "Dulce de leche", 246, 247),
    ("Almacén", "Mermeladas, dulces y jaleas", 246, 248), ("Almacén", "Pasta de maní y crema de avellanas", 246, 249),
    ("Almacén", "Cereales y barritas", 222, 250), ("Almacén", "Cereales", 250, 251),
    ("Almacén", "Barritas de cereales", 250, 252), ("Almacén", "Granola", 250, 253), ("Bebidas Con Alcohol", "Cervezas", 255, 256),
    ("Bebidas Con Alcohol", "Vinos", 255, 257), ("Bebidas Con Alcohol", "Fernet y aperitivos", 255, 262),
    ("Bebidas Con Alcohol", "Bebidas blancas", 255, 266), ("Bebidas Con Alcohol", "Espumantes y sidras", 255, 273),
    ("Bebidas Sin Alcohol", "Gaseosas", 255, 277), ("Bebidas Sin Alcohol", "Aguas", 255, 283),
    ("Bebidas Sin Alcohol", "Jugos", 255, 286), ("Bebidas Sin Alcohol", "Bebidas energizantes", 255, 290),
    ("Bebidas Sin Alcohol", "Bebidas isotónicas", 255, 291), ("Frescos", "Leches", 292, 293),
    ("Frescos", "Yogures", 292, 299), ("Frescos", "Mantecas y margarinas", 292, 302),
    ("Frescos", "Cremas de leche", 292, 304), ("Frescos", "Postres", 292, 305),
    ("Frescos", "Huevos", 292, 306), ("Frescos", "Tapas y pastas frescas", 292, 307),
    ("Frescos", "Quesos", 292, 310), ("Frescos", "Fiambres", 292, 318),
    ("Frescos", "Carne vacuna", 321, 322), ("Frescos", "Pollo y granja", 321, 323),
    ("Frescos", "Carne de cerdo", 321, 324), ("Frescos", "Pescados y mariscos", 321, 327),
    ("Frescos", "Frutas", 330, 331), ("Frescos", "Verduras", 330, 332),
    ("Congelados", "Hamburguesas y medallones", 347, 348), ("Congelados", "Nuggets y rebozados", 347, 349),
    ("Congelados", "Papas congeladas", 347, 350), ("Congelados", "Frutas y vegetales cong.", 347, 352),
    ("Congelados", "Comidas congeladas", 347, 356), ("Congelados", "Pescados cong.", 347, 357),
    ("Congelados", "Helados y postres", 347, 358), ("Congelados", "Pollo congelado", 347, 703),
    ("Limpieza", "Limpieza de la ropa", 359, 360), ("Limpieza", "Limpieza pisos y muebles", 359, 367),
    ("Limpieza", "Insecticidas", 359, 376), ("Limpieza", "Limpieza de cocina", 359, 377),
    ("Limpieza", "Lavandinas", 359, 384), ("Limpieza", "Rollos y servilletas", 359, 385),
    ("Limpieza", "Papeles higiénicos", 359, 386), ("Limpieza", "Limpieza de baño", 359, 387),
    ("Limpieza", "Desodorantes de ambiente", 359, 390), ("Limpieza", "Artículos de limpieza", 359, 394),
    ("Cuidado Personal", "Cuidado del cabello", 402, 403), ("Cuidado Personal", "Shampoos", 403, 404),
    ("Cuidado Personal", "Acondicionadores", 403, 405), ("Cuidado Personal", "Tratamientos capilares", 403, 406),
    ("Cuidado Personal", "Cremas para peinar", 403, 407), ("Cuidado Personal", "Coloración", 403, 408),
    ("Cuidado Personal", "Gel y fijadores", 403, 409), ("Cuidado Personal", "Piojicidas", 403, 410),
    ("Cuidado Personal", "Accesorios para el cabello", 403, 411), ("Cuidado Personal", "Cuidado dental", 402, 412),
    ("Cuidado Personal", "Pasta dental", 412, 413), ("Cuidado Personal", "Enjuagues bucales", 412, 414),
    ("Cuidado Personal", "Cepillos de dientes", 412, 415), ("Cuidado Personal", "Hilos dentales", 412, 416),
    ("Cuidado Personal", "Para prótesis dentales", 412, 417), ("Cuidado Personal", "Jabones", 402, 418),
    ("Cuidado Personal", "Protección femenina", 402, 422), ("Cuidado Personal", "Toallitas femeninas", 422, 423),
    ("Cuidado Personal", "Cuidado de la piel", 402, 427), ("Cuidado Personal", "Cremas faciales", 427, 428),
    ("Cuidado Personal", "Limpieza facial y exfoliantes", 427, 429), ("Cuidado Personal", "Desmaquillantes", 427, 430),
    ("Cuidado Personal", "Cremas corporales", 427, 431), ("Cuidado Personal", "Cremas para manos", 427, 432),
    ("Cuidado Personal", "Cuidado labial", 427, 433), ("Cuidado Personal", "Protección solar", 427, 434),
    ("Cuidado Personal", "Antitranspirantes", 402, 435), ("Cuidado Personal", "Cuidado corporal", 402, 438),
    ("Cuidado Personal", "Depilación", 438, 439), ("Cuidado Personal", "Afeitado", 438, 440),
    ("Cuidado Personal", "Cuidado de los pies", 438, 441), ("Cuidado Personal", "Repelentes", 402, 443),
    ("Cuidado Personal", "Algodones e hisopos", 402, 444), ("Cuidado Personal", "Pañales", 451, 452),
    ("Cuidado Personal", "Toallitas húmedas", 451, 453), ("Cuidado Personal", "Higiene para bebés", 451, 458),
    ("Cuidado Personal", "Aceites, cremas y lociones", 458, 461), ("Cuidado Personal", "Shampoos y acondicionadores", 458, 663),
    ("Cuidado Personal", "Leches infantiles", 462, 463), ("Cuidado Personal", "Cereales y papillas", 462, 464),
    ("Cuidado Personal", "Cuidado maternal", 451, 466),    ("Panadería", "Bizcochuelos y piononos", 336, 206), ("Panadería", "Elaboración Carrefour", 336, 337),
    ("Panadería", "Especialidades dulces", 336, 338), ("Panadería", "Especialidades saladas", 336, 339),
    ("Panadería", "Panificados", 336, 340), ("Panadería", "Panes lacteados y de mesa", 336, 341),
    ("Panadería", "Pan para hamburguesas y panchos", 336, 342), ("Panadería", "Tortillas", 336, 343),
    ("Panadería", "Pizzas y prepizzas", 336, 345), ("Panadería", "Sandwiches, Empanadas y Tartas", 336, 346),
    ("Hogar", "Cocina y comedor", 71, 148), ("Hogar", "Papeles y bolsas de cocina", 71, 156),
]


def crear_sesion():
    """Cada thread necesita su propia sesión para evitar conflictos."""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,          # esperas: 2s, 4s, 8s, 16s, 32s
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    return session


def fetch_page(session, fq, from_idx, cat_nombre, cat_principal):
    """Descarga una página y devuelve los SKUs extraídos."""
    url    = f"{BASE_URL}/api/catalog_system/pub/products/search"
    to_idx = from_idx + PAGE_SIZE - 1

    try:
        r = session.get(url, params={"fq": fq, "_from": from_idx, "_to": to_idx},
                        headers=HEADERS, timeout=30)
        r.raise_for_status()

        total_vtex = 0
        res = r.headers.get("resources", "")
        if "/" in res:
            total_vtex = int(res.split("/")[-1])

        skus = []
        for p in r.json():
            for sku in p.get("items", []):
                sellers = sku.get("sellers", [])
                if not sellers:
                    continue
                offer = sellers[0].get("commertialOffer", {})
                avail = offer.get("AvailableQuantity", 0)
                price = offer.get("Price", 0)
                list_price = offer.get("ListPrice", 0)
                
                # Omitir únicamente productos con precio inválido o nulo.
                # Se registran todos los SKUs con su stock ('disponible') para monitorear la rotación diaria.
                if not price or price <= 0:
                    continue
                
                # Detectar si es promo con restricciones (Exclusivo online, etc.)
                is_restricted = False
                promo_type = ""
                
                # Buscar si la API indica explícitamente que es exclusivo online
                # Puede estar en campos como 'IsExclusive', 'Exclusive', etc.
                if sellers[0].get('IsExclusive') or sellers[0].get('exclusive') or sellers[0].get('isExclusive'):
                    is_restricted = True
                    promo_type = "Exclusivo online"
                elif offer.get('IsExclusive') or offer.get('exclusive') or offer.get('isExclusive'):
                    is_restricted = True
                    promo_type = "Exclusivo online"

                images = sku.get("images", [])
                image_url = ""
                if images and isinstance(images, list):
                    image_url = images[0].get("imageUrl", "") if isinstance(images[0], dict) else ""
                if not image_url:
                    for alt_item in p.get("items", []):
                        alt_imgs = alt_item.get("images", [])
                        if alt_imgs and isinstance(alt_imgs, list):
                            image_url = alt_imgs[0].get("imageUrl", "") if isinstance(alt_imgs[0], dict) else ""
                            if image_url:
                                break
                if image_url and image_url.startswith("//"):
                    image_url = "https:" + image_url

                skus.append({
                    "fecha":          datetime.now().strftime("%Y-%m-%d"),
                    "product_id":     p.get("productId", ""),
                    "sku_id":         sku.get("itemId", ""),
                    "ean":            sku.get("ean", ""),
                    "nombre":         sku.get("nameComplete") or p.get("productName", ""),
                    "marca":          p.get("brand", ""),
                    "categoria":      cat_nombre,
                    "precio_actual":  price,
                    "precio_regular": list_price,
                    "disponible":     avail,
                    "is_restricted":  is_restricted,
                    "promo_type":     promo_type,
                    "link":           p.get("link", ""),
                    "image_url":      image_url,
                    "cat_principal":  cat_principal,
                })
        return skus, total_vtex, None

    except Exception as e:
        return [], 0, str(e)


def get_productos_categoria(cat_principal, cat_nombre, parent_id, child_id):
    """Scrapea una categoría completa. Crea su propia sesión (thread-safe)."""
    session = crear_sesion()
    fq      = f"C:/{parent_id}/{child_id}/"

    # Página 0: obtener total
    skus_p0, total_vtex, err = fetch_page(session, fq, 0, cat_nombre, cat_principal)
    if err:
        print(f"  [Error inicial en {cat_nombre}]: {err}")
        return cat_nombre, [], 0
    if not skus_p0:
        return cat_nombre, [], total_vtex

    limit   = min(total_vtex, MAX_PRODS)
    offsets = list(range(PAGE_SIZE, limit, PAGE_SIZE))

    if not offsets:
        return cat_nombre, skus_p0, total_vtex

    # Páginas restantes en paralelo (dentro de la categoría)
    all_skus = list(skus_p0)
    page_futures = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_PAG) as ex:
        for off in offsets:
            f = ex.submit(fetch_page, session, fq, off, cat_nombre, cat_principal)
            page_futures[f] = off
        for future in as_completed(page_futures):
            skus, _, err = future.result()
            if err:
                print(f"  [Error offset {page_futures[future]} en {cat_nombre}]: {err}")
            else:
                all_skus.extend(skus)

    return cat_nombre, all_skus, total_vtex


def main():
    if not OUTPUT_DIR.exists():
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Nota directorio output: {e}")
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = OUTPUT_DIR / f"carrefour_{timestamp}.csv"
    csv_lock     = Lock()
    total_cats   = len(CATEGORIAS)

    # Contador compartido para el progreso
    completadas    = [0]
    acumulado_skus = [0]
    lock_contador  = Lock()

    print(f"{'='*60}")
    print(f" CARREBOT - SCRAPER COMPLETO ({total_cats} CATEGORÍAS EN PARALELO)")
    print(f" Inicia: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    # ── TODAS LAS CATEGORÍAS EN PARALELO ─────────────────────────────────────
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_CAT) as ex:
        futures = {
            ex.submit(get_productos_categoria, cp, cn, pid, cid): (cp, cn)
            for cp, cn, pid, cid in CATEGORIAS
        }

        for future in as_completed(futures):
            cat_nombre, prods_cat, total_web = future.result()
            cat_principal = futures[future][0]

            with lock_contador:
                completadas[0] += 1
                idx = completadas[0]

            if prods_cat:
                df_temp = pd.DataFrame(prods_cat)
                with csv_lock:
                    header_necesario = not csv_filename.exists()
                    df_temp.to_csv(csv_filename, mode="a", index=False,
                                   header=header_necesario, encoding="utf-8-sig")
                with lock_contador:
                    acumulado_skus[0] += len(prods_cat)
                    total_hasta_ahora  = acumulado_skus[0]

                print(f"[{idx:02d}/{total_cats}] {cat_nombre.ljust(30)} -> {len(prods_cat)} SKUs (Total: {total_hasta_ahora})")
            else:
                print(f"[{idx:02d}/{total_cats}] {cat_nombre.ljust(30)} -> SIN DATOS/ERROR")

    print(f"\n{'='*60}")
    print(f" PROCESO TERMINADO: {datetime.now().strftime('%H:%M:%S')}")
    print(f" Total SKUs guardados: {acumulado_skus[0]}")
    print(f" Archivo: {csv_filename}")
    print(f"{'='*60}")

    # Importar automáticamente a la base de datos
    try:
        from import_carrefour import import_latest_carrefour
        print("\nSincronizando precios con la base de datos...")
        inserted = import_latest_carrefour(str(csv_filename))
        print(f"Precios de Carrefour sincronizados en DB: {inserted} productos.")
    except Exception as e:
        print(f"Error al sincronizar con la base de datos: {e}")


if __name__ == "__main__":
    main()

