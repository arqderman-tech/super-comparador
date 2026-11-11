"""
Importador de precios de Carrefour a la base de datos de SuperComparador.
Lee el archivo CSV generado por carrefour_scraper.py, cruza por EAN con La Gallega,
detecta y normaliza diferencias de pack vs unitario, y carga los registros en competitor_prices.
"""
import os
import glob
import re
import logging
import sqlite3
import pandas as pd
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("import_carrefour")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output_carrefour")

def clean_ean(val) -> str:
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    # Manejar notación científica (ej: 7.792798e+12 -> 7792798001316)
    if 'e+' in s.lower():
        try:
            s = str(int(float(s)))
        except:
            pass
    s = re.sub(r'\.0$', '', s)
    return s.strip()

def clean_image_url(val) -> str:
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    if s.lower() == "nan" or not s.startswith("http"):
        return ""
    return s

def extract_units(name: str) -> int:
    """Extrae la cantidad de unidades en un pack si está explícita en el nombre del producto."""
    if not name:
        return 1
    name_clean = name.lower()
    # Descartar dimensiones (ej. 34 x 38 cm, 30x40 cm)
    name_clean = re.sub(r'\b\d+\s*x\s*\d+\s*(?:cm|mts|m|mm)?\b', ' ', name_clean)
    # Descartar número de hojas en afeitadoras (ej. 3 hojas) y marcas (Mach 3)
    name_clean = re.sub(r'\b\d+\s*hojas?\b', ' ', name_clean)
    name_clean = re.sub(r'\bmach\s*\d+\b', ' ', name_clean)
    
    # 1. Unidades explícitas (ej: "30 unidades", "x 6 uni", "4 uds", "20 unid", "x 2")
    m_unit = re.search(r'\b(?:x\s*)?(\d+)\s*(?:unid|unidades|uni|und|uds|un|u)\b', name_clean)
    if m_unit:
        val = int(m_unit.group(1))
        if 2 <= val <= 150:
            return val

    # 2. Pack explícito (ej: "pack x 6", "pack de 4", "pack 6")
    m_pack = re.search(r'\bpack\s*(?:x|de)?\s*(\d+)\b', name_clean)
    if m_pack:
        val = int(m_pack.group(1))
        if 2 <= val <= 50:
            return val

    # 3. Multiplicador de peso (ej: "3 x 90 gr", "6 x 25 gm")
    m_mult = re.search(r'\b(\d+)\s*x\s*\d+\s*(?:g|gr|gm|ml|cc)\b', name_clean)
    if m_mult:
        val = int(m_mult.group(1))
        if 2 <= val <= 36:
            return val

    return 1

def check_pack_mismatch(lg_name: str, comp_name: str, lg_price: float, comp_price: float) -> Tuple[bool, float, str]:
    """
    Detecta si un supermercado vende por pack y el otro por unidad para un mismo EAN.
    Ajusta el precio proporcionalmente para hacer la comparativa equitativa.
    """
    if lg_price <= 0 or comp_price <= 0:
        return False, comp_price, ""
        
    lg_u = extract_units(lg_name)
    comp_u = extract_units(comp_name)
    
    # Caso 1: La Gallega es pack (N > 1) y Carrefour vende unitario
    if lg_u > 1 and comp_u == 1:
        ratio = lg_price / comp_price
        if abs(ratio - lg_u) / lg_u <= 0.35:
            adjusted = comp_price * lg_u
            return True, adjusted, f"Ajustado por pack: Carrefour vende unitario a ${comp_price:,.0f} (equiv. x{lg_u}: ${adjusted:,.0f})"
            
    # Caso 2: Carrefour es pack (N > 1) y La Gallega vende unitario
    elif comp_u > 1 and lg_u == 1:
        ratio = comp_price / lg_price
        if abs(ratio - comp_u) / comp_u <= 0.35:
            adjusted = comp_price / comp_u
            return True, adjusted, f"Ajustado por pack: Carrefour vende pack x{comp_u} a ${comp_price:,.0f} (equiv. 1u: ${adjusted:,.0f})"
            
    # Caso 3: Ambos indican pack pero con distinta cantidad de unidades (ej. x12 vs x10)
    elif lg_u > 1 and comp_u > 1 and lg_u != comp_u:
        expected_mult = lg_u / comp_u
        ratio = lg_price / comp_price
        if abs(ratio - expected_mult) / expected_mult <= 0.35:
            adjusted = comp_price * (lg_u / comp_u)
            return True, adjusted, f"Ajustado por pack: Carrefour vende x{comp_u} a ${comp_price:,.0f} (equiv. x{lg_u}: ${adjusted:,.0f})"
            
    return False, comp_price, ""

def import_latest_carrefour(csv_path: Optional[str] = None) -> int:
    if not csv_path:
        csv_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "carrefour_*.csv")))
        if not csv_files:
            log.error("No se encontró ningún archivo CSV en output_carrefour/")
            return 0
        csv_path = csv_files[-1]

    log.info(f"Leyendo archivo Carrefour: {csv_path}")
    if not os.path.exists(csv_path):
        log.error(f"El archivo no existe: {csv_path}")
        return 0

    df = pd.read_csv(csv_path, dtype={'ean': str})
    df = df[df['ean'].notna() & (df['ean'] != '')]
    df['clean_ean'] = df['ean'].apply(clean_ean)
    df = df[df['clean_ean'] != '']
    df = df[df['precio_actual'] > 0]

    # Ordenar priorizando items con stock para la deduplicación por EAN
    if 'disponible' in df.columns:
        df = df.sort_values(by=['disponible', 'precio_actual'], ascending=[False, True])
    
    # Deduplicar por EAN (quedarse con el registro con stock o el más relevante)
    carrefour_by_ean = df.drop_duplicates(subset=['clean_ean'])
    total_carr_unique = len(carrefour_by_ean)
    log.info(f"Productos únicos con EAN y precio en Carrefour: {total_carr_unique:,}")

    # Cargar imágenes y productos de La Gallega (Referencia principal por EAN)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    c = conn.cursor()

    c.execute("""
    SELECT id, ean, name, price_promo, image_url
    FROM products
    WHERE ean != '' AND ean IS NOT NULL AND supermarket = 'La Gallega'
    """)
    lg_map = {}
    lg_img_by_ean = {}
    for pid, ean_val, p_name, p_promo, p_img in c.fetchall():
        c_ean = clean_ean(ean_val)
        if c_ean:
            c_img = clean_image_url(p_img)
            lg_map[c_ean] = {
                'id': pid,
                'name': p_name or '',
                'price_promo': float(p_promo or 0),
                'image_url': c_img
            }
            if c_img:
                lg_img_by_ean[c_ean] = c_img

    log.info(f"Productos con EAN en La Gallega: {len(lg_map):,} ({len(lg_img_by_ean):,} con foto)")

    # Obtener imágenes ya guardadas previamente en la DB para Carrefour (para no cambiarlas todos los días)
    c.execute("""
    SELECT id, ean, image_url
    FROM products
    WHERE supermarket = 'Carrefour' AND image_url != '' AND image_url IS NOT NULL
    """)
    existing_carr_img_by_id = {}
    existing_carr_img_by_ean = {}
    for p_id, p_ean, p_img in c.fetchall():
        c_img = clean_image_url(p_img)
        if c_img:
            existing_carr_img_by_id[p_id] = c_img
            c_ean = clean_ean(p_ean)
            if c_ean:
                existing_carr_img_by_ean[c_ean] = c_img

    # 1. Preparar TODOS los productos de Carrefour para insertar en la tabla products (Catálogo Completo)
    products_inserts = []
    seen_ids = set()
    for _, row in df.iterrows():
        sku_id = str(row.get('sku_id') or '').strip()
        prod_id = str(row.get('product_id') or '').strip()
        custom_id = f"carr_{sku_id}" if sku_id else f"carr_{prod_id}"
        if not custom_id or custom_id in seen_ids:
            continue
        seen_ids.add(custom_id)

        clean_e = row.get('clean_ean', '')
        nombre = str(row.get('nombre') or '').strip()
        categoria = str(row.get('categoria') or 'General').strip()
        cat_principal = str(row.get('cat_principal') or categoria).strip()
        marca = str(row.get('marca') or '').strip()
        p_actual = float(row.get('precio_actual') or 0.0)
        p_reg = float(row.get('precio_regular') or p_actual)
        if p_reg < p_actual:
            p_reg = p_actual

        # Detectar si es promo con restricciones (Exclusivo online, etc.)
        is_restricted = bool(row.get('is_restricted', False))
        promo_type = str(row.get('promo_type') or '').strip()
        
        # Si es promo con restricciones, NO usar el precio actual en la comparativa
        # Usar el precio regular y marcar como oferta especial
        if is_restricted:
            is_promo = 1
            discount_pct = 0.0  # No mostrar descuento porcentual
            # En la comparativa usaremos el precio regular
            price_for_comparison = p_reg
        else:
            is_promo = 1 if (p_reg > p_actual) else 0
            discount_pct = round(((p_reg - p_actual) / p_reg) * 100, 1) if (p_reg > p_actual and p_reg > 0) else 0.0
            price_for_comparison = p_actual

        # Regla de Oro de Imágenes:
        # 1. Si está en La Gallega para ese EAN -> usar la foto de La Gallega (no volver a bajar para otro super)
        # 2. Si ya estaba guardada en la DB -> conservar la existente (no bajar todos los días)
        # 3. Solo si es producto nuevo sin imagen previa -> tomar imagen del scraper de Carrefour
        if clean_e and clean_e in lg_img_by_ean:
            img_url = lg_img_by_ean[clean_e]
        elif custom_id in existing_carr_img_by_id:
            img_url = existing_carr_img_by_id[custom_id]
        elif clean_e and clean_e in existing_carr_img_by_ean:
            img_url = existing_carr_img_by_ean[clean_e]
        else:
            img_raw = row.get('image_url', '') if 'image_url' in row else ''
            img_url = clean_image_url(img_raw)

        link_url = str(row.get('link') or '')
        if not link_url and clean_e:
            link_url = f"https://www.carrefour.com.ar/{clean_e}?_q={clean_e}&map=ft"

        is_available = 1 if float(row.get('disponible', 0) or 0) > 0 else 0

        products_inserts.append((
            custom_id,
            clean_e,
            nombre,
            categoria,
            cat_principal,
            marca,
            p_reg,
            price_for_comparison,
            is_promo,
            discount_pct,
            img_url,
            link_url,
            'Carrefour',
            1,
            promo_type if is_restricted else '',
            is_available
        ))

    log.info(f"Actualizando catálogo completo de Carrefour en 'products': {len(products_inserts):,} productos...")
    c.executemany("""
    INSERT INTO products (
        id, ean, name, category, category_id, brand,
        price_regular, price_promo, is_promo, discount_pct,
        image_url, url, supermarket, pack_units, pack_detail, is_available
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        ean = excluded.ean,
        name = excluded.name,
        category = excluded.category,
        category_id = excluded.category_id,
        brand = excluded.brand,
        price_regular = excluded.price_regular,
        price_promo = excluded.price_promo,
        is_promo = excluded.is_promo,
        discount_pct = excluded.discount_pct,
        image_url = CASE 
            WHEN excluded.image_url != '' THEN excluded.image_url 
            ELSE products.image_url 
        END,
        url = excluded.url,
        pack_units = excluded.pack_units,
        pack_detail = excluded.pack_detail,
        is_available = excluded.is_available,
        updated_at = CURRENT_TIMESTAMP
    """, products_inserts)
    conn.commit()

    # 2. Cruzar datos con La Gallega para la tabla competitor_prices (Comparativa Multitienda)
    inserts = []
    carr_cheaper = 0
    lg_cheaper = 0
    same_price = 0
    pack_adjusted_count = 0
    anomaly_omitted_count = 0

    for _, row in carrefour_by_ean.iterrows():
        ean = row['clean_ean']
        if ean in lg_map:
            lg_item = lg_map[ean]
            pid = lg_item['id']
            lg_name = lg_item['name']
            lg_price = lg_item['price_promo']
            carr_name = str(row.get('nombre') or '')
            raw_carr_price = float(row['precio_actual'])
            reg_price = float(row.get('precio_regular') or raw_carr_price)
            is_restricted = bool(row.get('is_restricted', False))
            promo_type = str(row.get('promo_type') or '').strip()

            # Si es promo con restricciones, usar el precio regular en la comparativa
            if is_restricted:
                carr_price = reg_price
                has_promo = 1
                promo_units = 1
                promo_detail = f"Oferta especial: {promo_type}"
            else:
                # Detectar y corregir disparidad de pack vs unitario
                is_pack_adj, norm_price, pack_detail = check_pack_mismatch(
                    lg_name, carr_name, lg_price, raw_carr_price
                )

                if is_pack_adj:
                    carr_price = norm_price
                    has_promo = 1
                    promo_units = 1
                    promo_detail = pack_detail
                    pack_adjusted_count += 1
                else:
                    carr_price = raw_carr_price
                    has_promo = 0
                    promo_units = 1
                    promo_detail = ""

            if is_pack_adj:
                carr_price = norm_price
                has_promo = 1
                promo_units = 1
                promo_detail = pack_detail
                pack_adjusted_count += 1
            else:
                carr_price = raw_carr_price
                has_promo = 0
                promo_units = 1
                promo_detail = ""

            if lg_price > 0:
                diff_pct = round(((carr_price - lg_price) / lg_price) * 100, 1)
            else:
                diff_pct = 0.0

            # FILTRO DE SEGURIDAD CONTRA ANOMALÍAS O PRECIOS HISTÓRICOS DESACTUALIZADOS:
            is_in_stock = float(row.get('disponible', 0) or 0) > 0
            if not is_in_stock:
                # Omitir productos sin stock en Carrefour: tienen precios históricos obsoletos
                anomaly_omitted_count += 1
                continue
            elif diff_pct < -70 or diff_pct > 200:
                anomaly_omitted_count += 1
                continue

            if carr_price < lg_price:
                carr_cheaper += 1
            elif lg_price < carr_price:
                lg_cheaper += 1
            else:
                same_price += 1

            inserts.append((
                pid, ean, 'Carrefour', carr_price, diff_pct,
                reg_price, None, has_promo, promo_units, promo_detail
            ))

    log.info(f"Packs ajustados equitativamente: {pack_adjusted_count}")
    if anomaly_omitted_count > 0:
        log.info(f"Omitidas {anomaly_omitted_count} anomalías extremas no equiparables (>200% o <-70%)")
    log.info(f"Total productos coincidentes válidos para competitor_prices: {len(inserts):,}")

    # Guardar en competitor_prices
    c.execute("DELETE FROM competitor_prices WHERE supermarket = 'Carrefour'")
    c.executemany("""
    INSERT INTO competitor_prices (
        product_id, ean, supermarket, price, difference_pct,
        regular_price, promo_price, has_promo, promo_units, promo_detail
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, inserts)

    conn.commit()

    # Almacenar histórico de precios de Carrefour
    from datetime import datetime
    today_int = int(datetime.now().strftime("%Y%m%d"))
    from db import SUPERMARKET_CODES
    history_inserts = []
    for item in inserts:
        ean = item[1]  # ean está en posición 1
        price = item[3]  # price está en posición 3
        history_inserts.append((ean, today_int, price, SUPERMARKET_CODES['Carrefour']))

    if history_inserts:
        c.executemany("""
        INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
        VALUES (?, ?, ?, ?)
        """, history_inserts)
        conn.commit()
        log.info(f"Historico de Carrefour guardado: {len(history_inserts):,} registros")

    conn.close()

    log.info("Importación completada con éxito:")
    if inserts:
        log.info(f"  - Carrefour más barato:  {carr_cheaper:,} ({(carr_cheaper/len(inserts)*100):.1f}%)")
        log.info(f"  - La Gallega más barato: {lg_cheaper:,} ({(lg_cheaper/len(inserts)*100):.1f}%)")
        log.info(f"  - Mismo precio:          {same_price:,} ({(same_price/len(inserts)*100):.1f}%)")
        log.info(f"  - Insertados en competitor_prices: {len(inserts):,}")

    return len(inserts)

if __name__ == "__main__":
    import_latest_carrefour()
