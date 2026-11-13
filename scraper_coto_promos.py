# scraper_coto_promos.py
"""
Scraper de promociones bancarias de Coto.
- Fuente 1: REST getPromocionesMulticanal (estructura)
- Fuente 2: /terminos-descuentos (exclusiones reales)
- Linkea ambas y persiste en JSON + SQLite.

Uso:
    python scraper_coto_promos.py
"""

import json
import re
import sqlite3
import time
import random
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

REST_URL = (
    "https://www.coto.com.ar/rest/model/atg/actors/cProfileActor/"
    "getPromocionesMulticanal"
)
TC_URL = "https://www.coto.com.ar/terminos-descuentos"
DB_PATH = "database.db"
OUT_JSON = "coto_promos.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json, text/html",
    "Referer": "https://www.coto.com.ar/descuentos",
}

# banco_id / icono → nombre legible
BANCO_POR_ICONO = {
    "logo_icbc_1.png":                "ICBC",
    "logo_tci.png":                   "TCI",
    "logo_galicia.png":               "Galicia",
    "logo_ciudad1.png":               "Banco Ciudad",
    "logo_comafi2.png":               "Comafi",
    "logo_comafi_unico.png":          "Comafi (Segmento Único)",
    "logo_patagonia2.png":            "Patagonia",
    "logo_nacion_d.png":              "Nación",
    "logo_credicoop.png":             "Credicoop / Cabal",
    "logo_macro_bma3.png":            "Macro",
    "logo_columbia_1.png":            "Columbia",
    "logo_supervielle2.png":          "Supervielle",
    "logo_naranjax2.png":             "Naranja X",
    "logo_comunidad.png":             "Comunidad Coto",
    "logo_beneficios_anses.png":      "ANSES",
    "logo_ciudadania_portena.png":    "Ciudadanía Porteña",
    "logo_jubiladosypensionados.png": "Jubilados y Pensionados",
    "logo_mercadopago.png":           "MercadoPago",
    "logo_modo.png":                  "MODO",
    "logo_visa1.png":                 "Visa (NFC débito)",
}

DIAS_NOMBRE = {1: "Domingo", 2: "Lunes", 3: "Martes", 4: "Miércoles",
               5: "Jueves", 6: "Viernes", 7: "Sábado"}

DIAS_INDEX_TC = {
    "LUNES": 2, "MARTES": 3, "MIERCOLES": 4, "MIÉRCOLES": 4,
    "JUEVES": 5, "VIERNES": 6, "SABADO": 7, "SÁBADO": 7, "DOMINGO": 1,
}

# ---------------------------------------------------------------------------
# 1) Fetch de las dos fuentes
# ---------------------------------------------------------------------------

def fetch_promos_rest():
    """Trae promos desde el REST de Coto."""
    url = f"{REST_URL}?enviroment=ag&pushSite=CotoDigital"
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["result"]


def fetch_terminos_html():
    req = Request(TC_URL, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# 2) Parseo del REST
# ---------------------------------------------------------------------------

def parse_porcentaje(texto):
    m = re.search(r"(\d{1,2})\s*%\s*(?:DE\s*)?DESCUENTO", texto or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_tope_rest(obs):
    t = (obs or "").upper()
    if "SIN TOPE" in t or "SIN LÍMITE" in t or "SIN LIMITE" in t:
        return None
    m = re.search(r"\$\s*([\d\.]+)", obs or "")
    return int(m.group(1).replace(".", "")) if m else None


def parse_tarjetas(desc):
    d = (desc or "").upper()
    tipos, marcas = [], []
    if "CRÉDITO" in d: tipos.append("Crédito")
    if "DÉBITO" in d: tipos.append("Débito")
    if "PREPAGA" in d: tipos.append("Prepaga")
    for m in ("VISA", "MASTERCARD", "CABAL", "AMERICAN EXPRESS",
              "MAESTRO", "NARANJA X", "MERCADO PAGO", "MODO", "NFC", "QR"):
        if m in d:
            marcas.append(m.title())
    return {"tipos": tipos or ["No especificado"],
            "marcas": marcas or ["No especificado"]}


def parse_dias_rest(promo):
    dias = promo.get("dias") or []
    ids = sorted({d["id"] for d in dias})
    return {
        "ids": ids,
        "nombres": [DIAS_NOMBRE[i] for i in ids],
        "texto": promo.get("diasVigencia") or "",
    }


def es_descuento_porcentual(promo):
    txt = (promo.get("textoDescuento") or "").upper()
    if "CUOTAS" in txt or "SIN INTERÉS" in txt:
        return False
    return parse_porcentaje(promo.get("textoDescuento")) is not None


def procesar_promo_rest(promo, canal):
    if not es_descuento_porcentual(promo):
        return None
    pct = parse_porcentaje(promo["textoDescuento"])
    tope = parse_tope_rest(promo.get("observacion"))
    tarjetas = parse_tarjetas(promo.get("descripcion"))
    dias = parse_dias_rest(promo)
    banco = BANCO_POR_ICONO.get(promo.get("icono") or "", "Desconocido")
    return {
        "id": promo.get("id"),
        "canal": canal,
        "banco": banco,
        "porcentaje": pct,
        "descripcion": (promo.get("descripcion") or "").strip(),
        "observacion": (promo.get("observacion") or "").strip(),
        "tope_num": tope,
        "dias_ids": dias["ids"],
        "dias_nombres": dias["nombres"],
        "dias_texto": dias["texto"],
        "tarjetas": tarjetas,
        "vigencia_desde": promo.get("vigenciaDesde"),
        "vigencia_hasta": promo.get("vigenciaHasta"),
        "icono": promo.get("icono"),
        "forma_pago": promo.get("formaPago"),
        "is_digital": promo.get("isDigital"),
        "exclusiones": None,  # se completa en el linkeo
    }


# ---------------------------------------------------------------------------
# 3) Parseo de términos y condiciones
# ---------------------------------------------------------------------------

def split_bloques_terminos(html):
    texto = re.sub(r"[ \t]+", " ", html).replace("\r", "")
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]

    patron_fin = re.compile(
        r"–\s*(EXCLUSIVO COMPRAS PRESENCIALES|EXCLUSIVO COMPRAS ONLINE|"
        r"PRESENCIAL Y ONLINE|PRESENCIAL|ONLINE)",
        re.IGNORECASE,
    )
    bloques, titulo, cuerpo = [], None, []
    for ln in lineas:
        upper = ln[:40].upper()
        es_header = bool(patron_fin.search(ln)) and (
            upper.startswith("BANCO") or upper.startswith("BENEFICIO")
            or upper.startswith("SUPERAPP") or re.match(r"^\d{1,2}%", upper)
        )
        if es_header:
            if titulo:
                bloques.append((titulo, " ".join(cuerpo)))
            titulo, cuerpo = ln, []
        elif titulo:
            cuerpo.append(ln)
    if titulo:
        bloques.append((titulo, " ".join(cuerpo)))
    return bloques


def parse_bloque_terminos(titulo, cuerpo):
    t = (titulo + " " + cuerpo).upper()
    m = re.search(r"(\d{1,2})\s*%\s*(?:DE\s*)?DESCUENTO", t)
    if not m:
        return None
    pct = int(m.group(1))

    tit_u = titulo.upper()
    if "PRESENCIAL Y ONLINE" in tit_u:
        canal = "Ambos"
    elif "ONLINE" in tit_u:
        canal = "Online"
    elif "PRESENCIAL" in tit_u:
        canal = "Presencial"
    else:
        canal = "No especificado"

    banco = None
    m = re.match(
        r"BANCO\s+([A-ZÁÉÍÓÚÑ0-9 ]+?)(?:\s+(?:MODO|PLAN SUELDO|LUNES|MARTES|"
        r"MI[ÉE]RCOLES|JUEVES|VIERNES|S[ÁA]BADO|DOMINGO|\d|–|-|$))",
        titulo.upper(),
    )
    if m:
        banco = "Banco " + m.group(1).strip().title()
    elif tit_u.startswith("BENEFICIO ANSES"):
        banco = "ANSES"
    elif tit_u.startswith("SUPERAPP"):
        banco = "SuperApp Coto"
    elif "SUPERVIELLE" in tit_u and "MODO" in tit_u:
        banco = "Supervielle"
    elif "MODO" in tit_u:
        banco = "MODO"

    dias = set()
    for nombre, idx in DIAS_INDEX_TC.items():
        if re.search(rf"\b{nombre}S?\b", t):
            dias.add(idx)
    if re.search(r"S[ÁA]BADO\s+\d+\s+(?:DE\s+\w+\s+)?HASTA\s+DOMINGO", t):
        dias.update({7, 1})

    tope_num = None
    if "SIN TOPE" in t or "SIN LÍMITE" in t or "SIN LIMITE" in t:
        tope_num = None
    else:
        for pat in (
            r"TOPE DE REINTEGRO\s*(?:DE)?\s*\$\s*([\d\.]+)",
            r"REEMBOLSO M[ÁA]XIMO\s*:?\s*\$\s*([\d\.]+)",
            r"TOPE[^$]{0,40}\$\s*([\d\.]+)",
        ):
            mm = re.search(pat, t)
            if mm:
                tope_num = int(mm.group(1).replace(".", ""))
                break

    m = re.search(r"EXCLUSIONES\s*:", cuerpo, re.IGNORECASE)
    excl_texto = cuerpo[m.end():].strip() if m else cuerpo
    t_ex = excl_texto.upper()

    rubros = []
    for clave, etiqueta in [
        ("ELECTRODOMÉSTICOS", "Electrodomésticos"),
        ("NEUMÁTICOS", "Neumáticos"),
        ("RODADOS", "Rodados"),
        ("CORTES DE CARNES", "Carnes y embutidos"),
        ("POLLO ENTERO", "Pollo"),
        ("LECHES INFANTILES", "Leches infantiles 1 y 2"),
        ("HARINAS EN TODAS", "Harinas"),
        ("AZÚCAR EN TODAS", "Azúcar"),
        ("ACEITE DE GIRASOL", "Aceite de girasol"),
        ("PATIOS DE COMIDAS", "Patios de comidas / Coto Expresso / Coto Bar"),
    ]:
        if clave in t_ex:
            rubros.append(etiqueta)

    marcas = []
    m = re.search(
        r"NO INCLUYE TODAS LAS PRESENTACIONES DE LAS SIGUIENTES MARCAS:([^\.]+)",
        t_ex,
    )
    if m:
        marcas = [x.strip().title() for x in m.group(1).split(",") if x.strip()]

    return {
        "titulo": titulo,
        "banco_terminos": banco,
        "porcentaje": pct,
        "canal": canal,
        "dias": sorted(dias),
        "tope_num": tope_num,
        "no_acumulable": "NO ACUMULA" in t or "NO ACUMULABLE" in t,
        "excluye_online": "DESCUENTOS NO VÁLIDOS PARA COMPRAS ONLINE" in t_ex,
        "excluye_nfc": "NFC" in t_ex,
        "excluye_corporativas": "CORPORATIVAS" in t_ex,
        "rubros_excluidos": rubros,
        "marcas_excluidas": marcas,
        "texto_crudo": excl_texto,
    }


def cargar_terminos():
    html = fetch_terminos_html()
    bloques = split_bloques_terminos(html)
    terminos = []
    for titulo, cuerpo in bloques:
        p = parse_bloque_terminos(titulo, cuerpo)
        if p:
            terminos.append(p)
    return terminos


# ---------------------------------------------------------------------------
# 4) Linkeo REST ↔ Términos
# ---------------------------------------------------------------------------

def _normalizar_banco(s):
    if not s:
        return ""
    s = s.upper()
    s = re.sub(r"\bBANCO\b", "", s).strip()
    s = re.sub(r"\s*\(.*?\)", "", s).strip()
    return s


def _similitud_banco(a, b):
    a, b = _normalizar_banco(a), _normalizar_banco(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _similitud_tope(a, b):
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.5
    return 1.0 if a == b else 0.3


def _score(promo_rest, termino):
    if promo_rest["porcentaje"] != termino["porcentaje"]:
        return 0.0
    canal_r, canal_t = promo_rest["canal"], termino["canal"]
    if canal_t == "Ambos":
        canal_score = 1.0
    elif canal_r == canal_t:
        canal_score = 1.0
    else:
        return 0.0

    banco_score = _similitud_banco(promo_rest.get("banco", ""), termino["banco_terminos"])
    dias_r = set(promo_rest.get("dias_ids", []))
    dias_t = set(termino["dias"])
    if dias_r and dias_t:
        dias_score = len(dias_r & dias_t) / max(len(dias_r), len(dias_t))
    else:
        dias_score = 0.3
    tope_score = _similitud_tope(promo_rest.get("tope_num"), termino["tope_num"])
    return 0.4 * banco_score + 0.3 * dias_score + 0.2 * tope_score + 0.1 * canal_score


EXCLUSIONES_FALLBACK = {
    "match_score": 0.0,
    "match_titulo": None,
    "no_acumulable": True,
    "excluye_online": False,
    "excluye_nfc": True,
    "excluye_corporativas": True,
    "rubros_excluidos": [
        "Electrodomésticos", "Neumáticos", "Rodados",
        "Carnes y embutidos", "Pollo", "Leches infantiles 1 y 2",
        "Harinas", "Azúcar", "Aceite de girasol",
    ],
    "marcas_excluidas": [
        "Coca Cola", "Fanta", "Sprite", "Schweppes", "Aquarius",
        "Cepita", "Benedictino", "Powerade", "Monster",
    ],
    "texto_crudo": "Aplican exclusiones generales de Coto (ver /terminos-descuentos).",
}


def link_promos(promos_rest, terminos, umbral=0.45):
    for promo in promos_rest:
        mejores = sorted(
            ((_score(promo, t), t) for t in terminos),
            key=lambda x: x[0], reverse=True,
        )
        score, match = mejores[0] if mejores else (0.0, None)
        if match and score >= umbral:
            promo["exclusiones"] = {
                "match_score": round(score, 3),
                "match_titulo": match["titulo"],
                "no_acumulable": match["no_acumulable"],
                "excluye_online": match["excluye_online"],
                "excluye_nfc": match["excluye_nfc"],
                "excluye_corporativas": match["excluye_corporativas"],
                "rubros_excluidos": match["rubros_excluidos"],
                "marcas_excluidas": match["marcas_excluidas"],
                "texto_crudo": match["texto_crudo"],
            }
        else:
            fb = dict(EXCLUSIONES_FALLBACK)
            fb["match_score"] = round(score, 3)
            fb["excluye_online"] = promo["canal"] == "Presencial"
            promo["exclusiones"] = fb
    return promos_rest


# ---------------------------------------------------------------------------
# 5) Persistencia
# ---------------------------------------------------------------------------

def crear_tabla(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS promos (
        id TEXT PRIMARY KEY,
        supermarket TEXT NOT NULL,
        canal TEXT NOT NULL,
        banco TEXT,
        porcentaje INTEGER,
        descripcion TEXT,
        observacion TEXT,
        tope_num INTEGER,
        tope_texto TEXT,
        dias_ids TEXT,
        dias_nombres TEXT,
        dias_texto TEXT,
        tarjetas_tipos TEXT,
        tarjetas_marcas TEXT,
        vigencia_desde TEXT,
        vigencia_hasta TEXT,
        icono TEXT,
        forma_pago INTEGER,
        is_digital INTEGER,
        exclusiones_json TEXT,
        scraped_at TEXT
    )
    """)


def guardar(promos, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    crear_tabla(conn)
    conn.execute("DELETE FROM promos WHERE supermarket = 'Coto'")
    for p in promos:
        conn.execute("""
        INSERT OR REPLACE INTO promos (
            id, supermarket, canal, banco, porcentaje, descripcion, observacion,
            tope_num, tope_texto, dias_ids, dias_nombres, dias_texto,
            tarjetas_tipos, tarjetas_marcas, vigencia_desde, vigencia_hasta,
            icono, forma_pago, is_digital, exclusiones_json, scraped_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p["id"], "Coto", p["canal"], p["banco"], p["porcentaje"],
            p["descripcion"], p["observacion"],
            p["tope_num"],
            f"${p['tope_num']:,}".replace(",", ".") if p["tope_num"] else "Sin tope",
            json.dumps(p["dias_ids"]),
            json.dumps(p["dias_nombres"], ensure_ascii=False),
            p["dias_texto"],
            json.dumps(p["tarjetas"]["tipos"], ensure_ascii=False),
            json.dumps(p["tarjetas"]["marcas"], ensure_ascii=False),
            p["vigencia_desde"], p["vigencia_hasta"],
            p["icono"], p["forma_pago"], int(bool(p["is_digital"])),
            json.dumps(p["exclusiones"], ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 6) Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()
    print("[1/4] Trayendo promos del REST...")
    result = fetch_promos_rest()
    promos = []
    for raw in result.get("promocionesDigitales", []):
        p = procesar_promo_rest(raw, canal="Online")
        if p: promos.append(p)
    for raw in result.get("promocionesSucursalesFisicas", []):
        p = procesar_promo_rest(raw, canal="Presencial")
        if p: promos.append(p)
    print(f"      {len(promos)} promos de descuento %")

    print("[2/4] Trayendo términos y condiciones...")
    terminos = cargar_terminos()
    print(f"      {len(terminos)} bloques de términos parseados")

    print("[3/4] Linkeando promos con exclusiones...")
    promos = link_promos(promos, terminos)

    print("[4/4] Guardando...")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(promos, f, ensure_ascii=False, indent=2)
    guardar(promos)

    # Resumen de calidad
    print("\nResumen de linkeo:")
    for p in promos:
        score = p["exclusiones"]["match_score"]
        marca = "OK " if score >= 0.7 else ("~  " if score >= 0.45 else "MAL")
        tope = f"${p['tope_num']:,}".replace(",", ".") if p["tope_num"] else "sin tope"
        print(f"  [{marca}] {score:.2f}  {p['porcentaje']:>2}%  "
              f"{p['banco']:<20} {p['canal']:<10} "
              f"{','.join(p['dias_nombres']) or 'todos'}  {tope}")

    elapsed = round(time.time() - t0, 2)
    print(f"\nListo en {elapsed}s.")
    print(f"  JSON: {OUT_JSON}")
    print(f"  DB:   tabla 'promos' en {DB_PATH}")


if __name__ == "__main__":
    main()