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

    # EANs objetivo:
    # 1. Todos los EANs compartidos por 2 o más supermercados (comparativas cruzadas multitienda)
    # 2. Todos los EANs con evolución histórica de precios (múltiples fechas o catálogo con seguimiento)
    # 3. Todos los EANs en competitor_prices
    c.execute("""
        SELECT DISTINCT ean FROM (
            SELECT ean FROM products WHERE ean != '' AND ean IS NOT NULL GROUP BY ean HAVING COUNT(DISTINCT supermarket) >= 2
            UNION
            SELECT ean FROM price_history WHERE ean != '' GROUP BY ean HAVING COUNT(DISTINCT fecha) >= 2
            UNION
            SELECT ean FROM competitor_prices WHERE ean != ''
        )
    """)
    target_eans = [r[0] for r in c.fetchall()]
    print(f"   -> Pre-cargando histórico multitienda para {len(target_eans)} códigos EAN...")

    c.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_eans (ean TEXT PRIMARY KEY)")
    c.execute("DELETE FROM tmp_eans")
    c.executemany("INSERT OR IGNORE INTO tmp_eans VALUES (?)", [(e,) for e in target_eans])

    c.execute("""
        SELECT ph.ean, ph.supermarket_id, ph.fecha, ph.precio
        FROM price_history ph
        JOIN tmp_eans t ON ph.ean = t.ean
        ORDER BY ph.fecha ASC
    """)
    bulk_history = {}
    sid_to_super = {0: "La Gallega", 1: "Coto", 2: "Carrefour", 3: "La Reina", 4: "Jumbo", 5: "La Anónima"}
    for ean_val, sid, fecha, precio in c.fetchall():
        s_name = sid_to_super.get(sid, "La Gallega")
        f_str = str(fecha)
        f_fmt = f"{f_str[:4]}-{f_str[4:6]}-{f_str[6:]}" if len(f_str) == 8 else f_str
        bulk_history.setdefault(ean_val, {}).setdefault(s_name, []).append({
            'date': f_fmt,
            'price': float(precio)
        })

    # Asegurar que todos los productos con historial o comparativa estén representados en target_ids
    c.execute("""
        SELECT p.id, p.ean, p.supermarket
        FROM products p
        JOIN tmp_eans t ON p.ean = t.ean
        ORDER BY CASE 
            WHEN p.supermarket = 'La Gallega' THEN 0 
            WHEN p.supermarket = 'Coto' THEN 1
            WHEN p.supermarket = 'Carrefour' THEN 2
            WHEN p.supermarket = 'Jumbo' THEN 3
            ELSE 4 END
    """)
    seen_hist_eans = set()
    for pid_val, ean_val, super_val in c.fetchall():
        if ean_val not in seen_hist_eans:
            seen_hist_eans.add(ean_val)
            target_ids.add(pid_val)

    ean_dict = {}
    
    for pid in target_ids:
        try:
            detail = server.api_product_detail(pid, fetch_history=False)
            ean_val = detail.get('ean')
            superm = detail.get('supermarket') or 'La Gallega'

            # Adjuntar historias por supermercado (con muestreo de 30 puntos para mantener archivo ~35MB)
            histories = {}
            if ean_val and ean_val in bulk_history:
                for s_name, pts in bulk_history[ean_val].items():
                    if pts:
                        prices = [p['price'] for p in pts]
                        sampled_pts = pts
                        if len(pts) > 30:
                            step = len(pts) / 28.0
                            sampled_pts = [pts[0]] + [pts[int(i * step)] for i in range(1, 28)] + [pts[-1]]
                        histories[s_name] = {
                            'supermarket': s_name,
                            'history': sampled_pts,
                            'stats': {
                                'first_date': pts[0]['date'],
                                'last_date': pts[-1]['date'],
                                'total_points': len(pts),
                                'first_price': prices[0],
                                'current_price': prices[-1],
                                'min_price': min(prices),
                                'max_price': max(prices),
                                'avg_price': round(sum(prices) / len(prices), 2),
                                'inflation_pct': round(((prices[-1] - prices[0]) / prices[0]) * 100, 1) if prices[0] > 0 else 0
                            }
                        }

            if superm in histories:
                detail['history'] = histories[superm]['history']
                detail['history_stats'] = histories[superm]['stats']
                detail['history_supermarket'] = superm
                detail['audit'] = db.audit_product_deal(detail, detail['history_stats'], detail['history'])
            elif "La Gallega" in histories:
                detail['history'] = histories["La Gallega"]["history"]
                detail['history_stats'] = histories["La Gallega"]["stats"]
                detail['history_supermarket'] = "La Gallega"
                detail['audit'] = db.audit_product_deal(detail, detail['history_stats'], detail['history'])

            detail['histories'] = histories

            clean_ean = str(ean_val).strip() if ean_val else ''
            if clean_ean:
                if clean_ean in ean_dict and ean_dict[clean_ean].get('supermarket') == 'La Gallega' and superm != 'La Gallega':
                    pass
                else:
                    ean_dict[clean_ean] = detail
            else:
                ean_dict[str(pid)] = detail
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
