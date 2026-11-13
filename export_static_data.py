"""
export_static_data.py
---------------------------------------------------------------------
Genera los archivos JSON estáticos en /static/data/ a partir de database.db.
Esto permite que la aplicación funcione al 100% como Single Page Application
en GitHub Pages (gratis, autónoma, en la nube), con catálogo completo por
supermercado, ofertas filtradas, comparador multitienda y escáner de códigos de barra.
---------------------------------------------------------------------
"""

import os
import sys
import json
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

sys.path.insert(0, BASE_DIR)
import server
import db

SUPERMARKETS = ['La Gallega', 'Coto', 'Carrefour', 'La Reina', 'Jumbo', 'La Anónima']

def get_super_slug(name):
    norm = name.lower().replace(" ", "").replace("ó", "o").replace("á", "a").replace("é", "e").replace("í", "i")
    if "gallega" in norm: return "lagallega"
    if "coto" in norm: return "coto"
    if "carrefour" in norm: return "carrefour"
    if "reina" in norm: return "lareina"
    if "jumbo" in norm: return "jumbo"
    if "anonima" in norm: return "laanonima"
    return norm

def run_export():
    print("=== Iniciando exportación de datos estáticos para GitHub Pages ===")
    
    # 1. Stats globales y por tienda
    print("1. Exportando stats.json...")
    stats = server.api_stats()
    with open(os.path.join(DATA_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)

    # 2. Categorías por supermercado
    print("2. Exportando categories.json (multitienda)...")
    categories_by_super = {}
    for s in SUPERMARKETS:
        categories_by_super[s] = server.api_categories(supermarket=s)
    with open(os.path.join(DATA_DIR, "categories.json"), "w", encoding="utf-8") as f:
        json.dump(categories_by_super, f, ensure_ascii=False)

    # 3. Ofertas destacadas por supermercado (top 50 por tienda)
    print("3. Exportando top_deals.json (multitienda)...")
    deals_by_super = {}
    for s in SUPERMARKETS:
        deals_by_super[s] = db.get_top_deals(limit=50, supermarket=s)
    with open(os.path.join(DATA_DIR, "top_deals.json"), "w", encoding="utf-8") as f:
        json.dump(deals_by_super, f, ensure_ascii=False)

    # 4. Auditoría de precios
    print("4. Exportando audit.json...")
    audit = server.api_history_audit(limit=100)
    with open(os.path.join(DATA_DIR, "audit.json"), "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False)

    # 5. Inflación
    print("5. Exportando inflation.json...")
    inflation = server.api_history_inflation(limit=100)
    with open(os.path.join(DATA_DIR, "inflation.json"), "w", encoding="utf-8") as f:
        json.dump(inflation, f, ensure_ascii=False)

    # 6. Catálogo de productos particionado por supermercado
    print("6. Exportando catálogos por supermercado...")
    conn = server.get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, ean, name, category, category_id, brand, price_regular, 
               price_promo, is_promo, discount_pct, image_url, url, supermarket
        FROM products
    """)
    rows = c.fetchall()
    all_prods = [dict(r) for r in rows]
    
    # Agrupar por supermercado
    by_super = {s: [] for s in SUPERMARKETS}
    for p in all_prods:
        s_name = p.get('supermarket', 'La Gallega')
        matched = False
        for s in SUPERMARKETS:
            if get_super_slug(s) == get_super_slug(s_name):
                by_super[s].append(p)
                matched = True
                break
        if not matched:
            by_super['La Gallega'].append(p)

    for s in SUPERMARKETS:
        slug = get_super_slug(s)
        p_list = by_super[s]
        filename = f"products_{slug}.json"
        with open(os.path.join(DATA_DIR, filename), "w", encoding="utf-8") as f:
            json.dump(p_list, f, ensure_ascii=False, separators=(',', ':'))
        print(f"   -> {s} ({filename}): {len(p_list)} productos ({os.path.getsize(os.path.join(DATA_DIR, filename))/(1024*1024):.1f} MB)")

    # Mantener products.json como catálogo general
    with open(os.path.join(DATA_DIR, "products.json"), "w", encoding="utf-8") as f:
        json.dump(all_prods, f, ensure_ascii=False, separators=(',', ':'))
    print(f"   -> products.json (completo): {len(all_prods)} productos")

    # 7. Comparador multitienda
    print("7. Exportando comparator.json...")
    try:
        comp_res = server.api_comparator_products(limit=10000)
        with open(os.path.join(DATA_DIR, "comparator.json"), "w", encoding="utf-8") as f:
            json.dump(comp_res.get('items', []), f, ensure_ascii=False, separators=(',', ':'))
        print(f"   -> comparator.json: {len(comp_res.get('items', []))} productos con comparativa")
    except Exception as e:
        print(f"   Aviso en comparator.json: {e}")

    # 8. Diccionario de productos completos por EAN e ID (para escáner en vivo y fichas)
    print("8. Exportando products_by_ean.json (Fichas completas, comparativa multitienda e historial)...")
    c.execute("SELECT DISTINCT product_id FROM competitor_prices")
    target_ids = set(r[0] for r in c.fetchall())
    for s_deals in deals_by_super.values():
        for d in s_deals:
            if isinstance(d, dict) and 'id' in d:
                target_ids.add(d['id'])
    for a in audit:
        if isinstance(a, dict) and 'id' in a:
            target_ids.add(a['id'])

    print(f"   -> {len(target_ids)} productos con comparativa multitienda completa")
    ean_dict = {}
    
    for pid in target_ids:
        try:
            detail = server.api_product_detail(pid)
            ean_dict[str(pid)] = detail
            if detail.get('ean'):
                clean_ean = str(detail['ean']).strip()
                if clean_ean:
                    ean_dict[clean_ean] = detail
                    if len(clean_ean) == 12:
                        ean_dict['0' + clean_ean] = detail
                    elif len(clean_ean) == 13 and clean_ean.startswith('0'):
                        ean_dict[clean_ean.lstrip('0')] = detail
        except Exception as e:
            pass

    with open(os.path.join(DATA_DIR, "products_by_ean.json"), "w", encoding="utf-8") as f:
        json.dump(ean_dict, f, ensure_ascii=False, separators=(',', ':'))
    print(f"   -> {len(ean_dict)} claves indexadas en products_by_ean.json")

    conn.close()

    total_mb = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR)) / (1024*1024)
    print(f"\n✓ Exportación completada con éxito. Tamaño total: {total_mb:.2f} MB")
    print("✓ El sitio web ahora es 100% autónomo y rápido en GitHub Pages!")

if __name__ == '__main__':
    run_export()
