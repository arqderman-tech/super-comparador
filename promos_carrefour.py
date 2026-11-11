# promos_carrefour.py
"""
Scraper de promociones bancarias de Carrefour Argentina.
- Fuente 1: GraphQL persistido GetBanks        (catálogo de bancos)
- Fuente 2: GraphQL persistido GetCards        (catálogo de tarjetas)
- Fuente 3: GraphQL persistido GetPromotions   (promociones reales)
- Linkea las tres y persiste en JSON + SQLite.

Escribe en la tabla `promos_carrefour` para NO tocar la tabla `promos`
que usa el scraper de Coto en la misma database.db.

Uso:
    python promos_carrefour.py
"""

import base64
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

GRAPHQL_BASE = (
    "https://www.carrefour.com.ar/_v/public/graphql/v1"
    "?workspace=master&maxAge=short&appsEtag=remove&domain=store&locale=es-AR"
)

HASHES = {
    "GetBanks":      "a17d0a4ae5248a8007075eb0c871b327be760c90f8ef994193758e4914e68c33",
    "GetCards":      "b0268b02cfc0021bcd0d0373f54e590bb71111cbcef4dcc62bf381a3a0abfa15",
    "GetPromotions": "e3aa1d96402d80dbca5c2c9dbcb7ff859970db0ccfdb64e583fb8a9b1bbff49e",
}

SENDER = "valtech.carrefourar-bank-promotions@0.x"
PROVIDER = "vtex.store-graphql@2.x"

DB_PATH = "database.db"
OUT_JSON = "carrefour_promos.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
    "Referer": "https://www.carrefour.com.ar/descuentos-bancarios?filtro=entidad",
}

DIAS_NOMBRE = {1: "Domingo", 2: "Lunes", 3: "Martes", 4: "Miércoles",
               5: "Jueves", 6: "Viernes", 7: "Sábado"}

NORMALIZAR_BANCO = {
    "BanCo":              "BanCo",
    "Banco Galicia":      "Banco Galicia",
    "Banco_Ciudad":       "Banco Ciudad",
    "Banco_Columbia":     "Banco Columbia",
    "Banco_Credicoop":    "Banco Credicoop",
    "Banco_Entre_Rios":   "Banco Entre Ríos",
    "Banco_Formosa":      "Banco Formosa",
    "Banco_Hipotecario":  "Banco Hipotecario",
    "Banco_Nacion":       "Banco Nación",
    "Banco_Provincia":    "Banco Provincia",
    "Banco_San_Juan":     "Banco San Juan",
    "Bancor":             "Bancor",
    "BBVA":               "BBVA",
    "BPN":                "BPN",
    "Citi":               "Citi",
    "Club La Nación":     "Club La Nación",
    "Cuenta Digital":     "Cuenta Digital Carrefour Banco",
    "Cuenta Dni":         "Cuenta DNI",
    "Frances":            "Banco Francés",
    "Galicia":            "Galicia",
    "HSBC":               "HSBC",
    "ICBC":               "ICBC",
    "ITAU":               "Itaú",
    "Macro":              "Macro",
    "Mercado Pago":       "Mercado Pago",
    "Mi Carrefour":       "Mi Carrefour",
    "Otros_Bancos":       "Otros Bancos",
    "Patagonia":          "Patagonia",
    "Santander_Rio":      "Santander Río",
    "Superville":         "Supervielle",
    "Tarjeta Naranja":    "Naranja X",
}

# ---------------------------------------------------------------------------
# 1) Fetch GraphQL persistido
# ---------------------------------------------------------------------------

def _graphql_request(operation, variables):
    """Hace una request a VTEX GraphQL usando persistedQuery."""
    variables_b64 = quote(json.dumps(variables, separators=(",", ":")))
    ext = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": HASHES[operation],
            "sender": SENDER,
            "provider": PROVIDER,
        },
        "variables": base64.b64encode(
            json.dumps(variables).encode()
        ).decode(),
    }
    ext_b64 = quote(json.dumps(ext, separators=(",", ":")))

    url = (
        f"{GRAPHQL_BASE}&operationName={operation}"
        f"&variables={variables_b64}&extensions={ext_b64}"
    )
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))

    if "errors" in data:
        raise RuntimeError(f"{operation} devolvió errores: {data['errors']}")
    return data["data"]["documents"]


def fetch_banks():
    docs = _graphql_request("GetBanks", {})
    return _parse_catalog(docs, NORMALIZAR_BANCO)


def fetch_cards():
    docs = _graphql_request("GetCards", {})
    return _parse_catalog(docs, {})


def _parse_catalog(documents, nombre_map):
    """Convierte [{fields:[{key,value}]}] en {id: {name, image}}."""
    out = {}
    for doc in documents:
        f = {x["key"]: x["value"] for x in doc["fields"]}
        raw_name = f.get("name", "")
        out[f["id"]] = {
            "name": nombre_map.get(raw_name, raw_name.replace("_", " ")),
            "image": f.get("image"),
        }
    return out


def fetch_promotions():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    where = f"active=true AND ((active_from < {now}) AND (active_to > {now}))"
    docs = _graphql_request("GetPromotions", {"where": where, "account": "carrefourar"})
    return [_parse_promo(d) for d in docs]


# ---------------------------------------------------------------------------
# 2) Parseo de promociones
# ---------------------------------------------------------------------------

def _parse_promo(doc):
    f = {x["key"]: x["value"] for x in doc["fields"]}

    # --- porcentaje ---
    pct_raw = f.get("discount_percentage")
    pct = int(pct_raw) if pct_raw and pct_raw.isdigit() else None

    # --- cuotas sin interés ---
    cuotas_raw = f.get("discounts_amount_installments")
    cuotas = int(cuotas_raw) if cuotas_raw and cuotas_raw.isdigit() else None
    cuotas_txt = f.get("discounts_text_installments")  # "sin interés" / None

    # --- tope (desde sub_title o legal) ---
    tope = _parse_tope(f.get("sub_title", "") + " " + f.get("legal", ""))

    # --- días ---
    dias_map = [("monday", 2), ("tuesday", 3), ("wednesday", 4),
                ("thursday", 5), ("friday", 6), ("saturday", 7), ("sunday", 1)]
    dias_ids = [idx for key, idx in dias_map if f.get(key) == "true"]
    dias_nombres = [DIAS_NOMBRE[i] for i in dias_ids]

    # --- canal (flags hyper/market/express/maxi/ecommerce) ---
    canales = []
    if f.get("ecommerce") == "true":
        canales.append("Online")
    if any(f.get(k) == "true" for k in ("hyper", "market", "express", "maxi")):
        canales.append("Presencial")
    canal = "Ambos" if len(canales) == 2 else (canales[0] if canales else "No especificado")

    # --- exclusiones desde el texto legal ---
    legal = (f.get("legal") or "").upper()
    rubros = []
    for clave, etiqueta in [
        ("ELECTRODOMÉSTICOS", "Electrodomésticos"),
        ("TECNOLOGÍA", "Tecnología"),
        ("CARNICERÍA", "Carnicería"),
        ("HUEVOS DE GALLINA", "Huevos"),
        ("FRUTAS", "Frutas y verduras"),
        ("VERDURAS", "Frutas y verduras"),
        ("LECHES INFANTILES", "Leches infantiles 1 y 2"),
        ("BAZAR", "Bazar"),
        ("TEXTIL", "Textil"),
        ("OFERTÓN POR BULTO", "Ofertón por bulto"),
        ("PACK FAMILIAR", "Pack familiar"),
        ("PRECIO SÚPER BAJO", "Precio Súper Bajo"),
        ("MARKETPLACE", "Productos de Marketplace"),
    ]:
        if clave in legal and etiqueta not in rubros:
            rubros.append(etiqueta)

    marcas = []
    m = re.search(r"BODEGAS?[^:]*:([^\.]+)", legal) or \
        re.search(r"LAS SIGUIENTES BODEGAS Y MARCAS:([^\.]+)", legal)
    if m:
        marcas = [x.strip().title() for x in m.group(1).split(",") if x.strip()]

    return {
        "id": f.get("id"),
        "titulo": f.get("title"),
        "subtitulo": f.get("sub_title"),
        "porcentaje": pct,
        "cuotas": cuotas,
        "cuotas_texto": cuotas_txt,
        "tope_num": tope,
        "tope_texto": f"${tope:,}".replace(",", ".") if tope else "Sin tope",
        "dias_ids": dias_ids,
        "dias_nombres": dias_nombres,
        "dias_texto": f.get("validText") or "",
        "canal": canal,
        "hiper": f.get("hyper") == "true",
        "market": f.get("market") == "true",
        "express": f.get("express") == "true",
        "maxi": f.get("maxi") == "true",
        "ecommerce": f.get("ecommerce") == "true",
        "id_banco": f.get("idBank"),
        "id_tarjeta": f.get("idCard"),
        "img_card": f.get("img_card"),
        "img_card_2": f.get("img_card_2"),
        "vigencia_desde": f.get("active_from"),
        "vigencia_hasta": f.get("active_to"),
        "activa": f.get("active") == "true",
        "order": f.get("order"),
        "legal": f.get("legal"),
        "rubros_excluidos": rubros,
        "marcas_excluidas": marcas,
        "no_acumulable": "NO ACUMULABLE" in legal or "NO ACUMULABLES" in legal,
    }


def _parse_tope(texto):
    """Extrae el primer tope en $ que aparezca en el texto."""
    if not texto:
        return None
    t = texto.upper()
    if "SIN TOPE" in t:
        return None
    m = re.search(r"\$\s*([\d\.]+)", texto)
    if m:
        return int(m.group(1).replace(".", ""))
    return None


# ---------------------------------------------------------------------------
# 3) Linkeo promo → banco/tarjeta
# ---------------------------------------------------------------------------

def link_promos(promos, bancos, tarjetas):
    for p in promos:
        banco = None
        tarjeta = None

        if p["id_banco"] and p["id_banco"] in bancos:
            banco = bancos[p["id_banco"]]["name"]
        if p["id_tarjeta"] and p["id_tarjeta"] in tarjetas:
            tarjeta = tarjetas[p["id_tarjeta"]]["name"]

        if not banco:
            banco = _inferir_banco(p)
        if not tarjeta:
            tarjeta = _inferir_tarjeta(p)

        p["banco"] = banco or "Desconocido"
        p["tarjeta"] = tarjeta or "No especificada"
    return promos


def _inferir_banco(p):
    txt = f"{p.get('titulo','')} {p.get('subtitulo','')} {p.get('legal','')}".upper()
    img = (p.get("img_card") or "").lower()

    if "carrefour banco" in txt or "cuenta digital" in txt or "carrefour-credito" in img:
        return "Carrefour Banco"
    if "mercado pago" in txt or "mercado-pago" in img:
        return "Mercado Pago"
    if "patagonia" in txt or "patagonia" in img:
        return "Patagonia"
    if "cuenta dni" in txt or "cuenta_dni" in img:
        return "Cuenta DNI"
    if "club la nación" in txt or "club_lanacion" in img:
        return "Club La Nación"
    if "mi carrefour" in txt or "logo-micrf" in img:
        return "Mi Carrefour"
    if "naranja" in txt or "naranjax" in img:
        return "Naranja X"
    if "anses" in txt or "anses" in img:
        return "ANSES"
    return None


def _inferir_tarjeta(p):
    txt = f"{p.get('titulo','')} {p.get('legal','')}".upper()
    if "CUENTA DIGITAL" in txt:
        return "Cuenta Digital"
    if "CUENTA DNI" in txt:
        return "Cuenta DNI"
    if "TARJETA DE CRÉDITO" in txt and "CARREFOUR" in txt:
        return "Tarjeta de Crédito Carrefour"
    if "TARJETA DE DÉBITO" in txt:
        return "Tarjeta de Débito"
    if "VISA" in txt:
        return "Visa"
    if "MASTERCARD" in txt:
        return "Mastercard"
    if "AMERICAN EXPRESS" in txt or "AMEX" in txt:
        return "American Express"
    return None


# ---------------------------------------------------------------------------
# 4) Persistencia  (tabla propia: promos_carrefour)
# ---------------------------------------------------------------------------

def crear_tabla(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS promos_carrefour (
        id TEXT PRIMARY KEY,
        canal TEXT NOT NULL,
        banco TEXT,
        tarjeta TEXT,
        porcentaje INTEGER,
        cuotas INTEGER,
        cuotas_texto TEXT,
        titulo TEXT,
        subtitulo TEXT,
        tope_num INTEGER,
        tope_texto TEXT,
        dias_ids TEXT,
        dias_nombres TEXT,
        dias_texto TEXT,
        hiper INTEGER,
        market INTEGER,
        express INTEGER,
        maxi INTEGER,
        ecommerce INTEGER,
        vigencia_desde TEXT,
        vigencia_hasta TEXT,
        img_card TEXT,
        img_card_2 TEXT,
        order_num TEXT,
        activa INTEGER,
        rubros_excluidos TEXT,
        marcas_excluidas TEXT,
        no_acumulable INTEGER,
        legal TEXT,
        scraped_at TEXT
    )
    """)


def guardar(promos, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    crear_tabla(conn)
    conn.execute("DELETE FROM promos_carrefour")

    for p in promos:
        conn.execute("""
        INSERT OR REPLACE INTO promos_carrefour (
            id, canal, banco, tarjeta, porcentaje, cuotas, cuotas_texto,
            titulo, subtitulo, tope_num, tope_texto, dias_ids, dias_nombres, dias_texto,
            hiper, market, express, maxi, ecommerce,
            vigencia_desde, vigencia_hasta, img_card, img_card_2, order_num, activa,
            rubros_excluidos, marcas_excluidas, no_acumulable, legal, scraped_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p["id"], p["canal"], p["banco"], p["tarjeta"],
            p["porcentaje"], p["cuotas"], p["cuotas_texto"],
            p["titulo"], p["subtitulo"], p["tope_num"], p["tope_texto"],
            json.dumps(p["dias_ids"]),
            json.dumps(p["dias_nombres"], ensure_ascii=False),
            p["dias_texto"],
            int(p["hiper"]), int(p["market"]), int(p["express"]),
            int(p["maxi"]), int(p["ecommerce"]),
            p["vigencia_desde"], p["vigencia_hasta"],
            p["img_card"], p["img_card_2"], p["order"],
            int(p["activa"]),
            json.dumps(p["rubros_excluidos"], ensure_ascii=False),
            json.dumps(p["marcas_excluidas"], ensure_ascii=False),
            int(p["no_acumulable"]),
            p["legal"],
            datetime.now().isoformat(timespec="seconds"),
        ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 5) Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print("[1/4] Trayendo catálogo de bancos (GetBanks)...")
    bancos = fetch_banks()
    print(f"      {len(bancos)} bancos")

    print("[2/4] Trayendo catálogo de tarjetas (GetCards)...")
    tarjetas = fetch_cards()
    print(f"      {len(tarjetas)} tarjetas")

    print("[3/4] Trayendo promociones (GetPromotions)...")
    promos = fetch_promotions()
    print(f"      {len(promos)} promociones")

    print("[4/4] Linkeando y guardando...")
    promos = link_promos(promos, bancos, tarjetas)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(promos, f, ensure_ascii=False, indent=2)
    guardar(promos)

    print("\nResumen:")
    for p in promos:
        tipo = "descuento" if p["porcentaje"] else (
            "cuotas" if p["cuotas"] else "otro")
        valor = f"{p['porcentaje']}%" if p["porcentaje"] else (
            f"{p['cuotas']} cuotas" if p["cuotas"] else "—")
        print(f"  {p['banco']:<22} {p['tarjeta']:<28} "
              f"{tipo:<10} {valor:<12} {p['canal']:<10} "
              f"{','.join(p['dias_nombres']) or 'todos':<15} {p['tope_texto']}")

    elapsed = round(time.time() - t0, 2)
    print(f"\nListo en {elapsed}s.")
    print(f"  JSON: {OUT_JSON}")
    print(f"  DB:   tabla 'promos_carrefour' en {DB_PATH}")


if __name__ == "__main__":
    main()