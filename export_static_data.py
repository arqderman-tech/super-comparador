"""
export_static_data.py
---------------------------------------------------------------------
Genera los archivos JSON estáticos en /static/data/ a partir de database.db.
Esto permite que la aplicación funcione al 100% como Single Page Application
en GitHub Pages (gratis, autónoma, en la nube), permitiendo escanear códigos
de barra desde cualquier celular en cualquier supermercado sin depender de una PC.
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

def run_export():
    print("=== Iniciando exportación de datos estáticos para GitHub Pages ===")
    
    # 1. Stats
    print("1. Exportando stats.json...")
    stats = server.api_stats()
    with open(os.path.join(DATA_DIR, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)

    # 2. Categorías
    print("2. Exportando categories.json...")
    categories = server.api_categories()
    with open(os.path.join(DATA_DIR, "categories.json"), "w", encoding="utf-8") as f:
        json.dump(categories, f, ensure_ascii=False)

    # 3. Ofertas destacadas
    print("3. Exportando top_deals.json...")
    deals = server.get_top_deals(limit=100)
    with open(os.path.join(DATA_DIR, "top_deals.json"), "w", encoding="utf-8") as f:
        json.dump(deals, f, ensure_ascii=False)

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

    # 6. Catálogo de productos completo
    print("6. Exportando products.json...")
    conn = server.get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, ean, name, category, category_id, brand, price_regular, 
               price_promo, is_promo, discount_pct, image_url, url, supermarket
        FROM products
    """)
    rows = c.fetchall()
    all_prods = [dict(r) for r in rows]
    with open(os.path.join(DATA_DIR, "products.json"), "w", encoding="utf-8") as f:
        json.dump(all_prods, f, ensure_ascii=False)
    print(f"   -> {len(all_prods)} productos exportados")

    # 7. Diccionario de productos completos por EAN e ID (para escáner en vivo y fichas)
    print("7. Exportando products_by_ean.json (Fichas completas, comparativa multitienda e historial)...")
    ean_dict = {}
    for p in all_prods:
        pid = p['id']
        try:
            detail = server.api_product_detail(pid)
            ean_dict[str(pid)] = detail
            if p.get('ean'):
                clean_ean = str(p['ean']).strip()
                if clean_ean:
                    ean_dict[clean_ean] = detail
                    if len(clean_ean) == 12:
                        ean_dict['0' + clean_ean] = detail
                    elif len(clean_ean) == 13 and clean_ean.startswith('0'):
                        ean_dict[clean_ean.lstrip('0')] = detail
        except Exception as e:
            pass

    with open(os.path.join(DATA_DIR, "products_by_ean.json"), "w", encoding="utf-8") as f:
        json.dump(ean_dict, f, ensure_ascii=False)
    print(f"   -> {len(ean_dict)} claves indexadas por código de barras")

    conn.close()

    total_mb = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR)) / (1024*1024)
    print(f"\n✓ Exportación completada con éxito. Tamaño total: {total_mb:.2f} MB")
    print("✓ El sitio web ahora es 100% autónomo y puede funcionar en GitHub Pages!")

if __name__ == '__main__':
    run_export()
