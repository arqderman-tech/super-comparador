"""
export_seed.py
---------------------------------------------------------------------
Genera una base de datos ligera y funcional (database.db) de ~15 MB
a partir de la base de datos completa local.

Incluye:
  - Top 800+ productos con precios en supermercados competidores
  - Productos de prueba para escáner de códigos de barra
  - Precios de competidores asociados (Coto, Carrefour, Jumbo, La Reina, La Anónima)
  - Serie histórica diaria de precios (7 meses) para gráficos de evolución
  - Índice de búsqueda de texto completo (FTS5)

Ideal para mantener el repositorio de GitHub por debajo de las restricciones
de tamaño (límite duro de 100 MB y advertencia de 50 MB de GitHub), permitiendo
que el proyecto sea 100% funcional inmediatamente al clonarse.
---------------------------------------------------------------------
"""

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_DB = os.path.join(BASE_DIR, "database.db")
SOURCE_DB = os.path.join(BASE_DIR, "database_full.db")

# Si no existe database_full.db, buscar en la carpeta de desarrollo hermana
if not os.path.exists(SOURCE_DB):
    sibling_dev = os.path.normpath(os.path.join(BASE_DIR, "..", "super-comparador-dev", "database.db"))
    if os.path.exists(sibling_dev):
        SOURCE_DB = sibling_dev

def export_seed(limit_products=800):
    if not os.path.exists(SOURCE_DB):
        print(f"Error: No se encontró la base de datos de origen en: {SOURCE_DB}")
        return

    print(f"Leyendo origen: {SOURCE_DB} ({os.path.getsize(SOURCE_DB) / (1024*1024):.2f} MB)")
    temp_target = TARGET_DB + ".tmp"
    if os.path.exists(temp_target):
        os.remove(temp_target)

    conn_src = sqlite3.connect(SOURCE_DB)
    c_src = conn_src.cursor()

    conn_dst = sqlite3.connect(temp_target)
    c_dst = conn_dst.cursor()

    # 1. Esquema de tablas regulares
    c_src.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
    for name, sql in c_src.fetchall():
        if name.startswith('sqlite_') or '_fts' in name:
            continue
        try:
            c_dst.execute(sql)
        except Exception as e:
            print(f"Error creando tabla {name}: {e}")

    # 2. Productos
    print(f"Extrayendo top {limit_products} productos con comparativa...")
    c_src.execute(f"""
        SELECT DISTINCT p.* FROM products p
        JOIN competitor_prices cp ON p.id = cp.product_id
        LIMIT {limit_products}
    """)
    products = c_src.fetchall()

    test_eans = ['7790310985830', '7790360971135', '7613287149145', '7791293032467', '7791728246629']
    q_test = ','.join(['?'] * len(test_eans))
    c_src.execute(f"SELECT * FROM products WHERE ean IN ({q_test})", test_eans)
    extra_prods = c_src.fetchall()

    prod_dict = {p[0]: p for p in products}
    for ep in extra_prods:
        prod_dict[ep[0]] = ep
    final_products = list(prod_dict.values())

    placeholders = ','.join(['?'] * len(final_products[0]))
    c_dst.executemany(f"INSERT OR IGNORE INTO products VALUES ({placeholders})", final_products)
    print(f"-> {len(final_products)} productos insertados")

    # 3. Precios de competidores
    prod_ids = [p[0] for p in final_products]
    comps = []
    chunk_size = 500
    for i in range(0, len(prod_ids), chunk_size):
        chunk = prod_ids[i:i+chunk_size]
        qm = ','.join(['?'] * len(chunk))
        c_src.execute(f"SELECT * FROM competitor_prices WHERE product_id IN ({qm})", chunk)
        comps.extend(c_src.fetchall())

    if comps:
        placeholders_comp = ','.join(['?'] * len(comps[0]))
        c_dst.executemany(f"INSERT OR IGNORE INTO competitor_prices VALUES ({placeholders_comp})", comps)
        print(f"-> {len(comps)} precios competidores insertados")

    # 4. Historial de precios
    eans = list(set([p[1] for p in final_products if p[1]]))
    hist = []
    for i in range(0, len(eans), chunk_size):
        chunk = eans[i:i+chunk_size]
        qm = ','.join(['?'] * len(chunk))
        c_src.execute(f"SELECT * FROM price_history WHERE ean IN ({qm})", chunk)
        hist.extend(c_src.fetchall())

    if hist:
        placeholders_hist = ','.join(['?'] * len(hist[0]))
        c_dst.executemany(f"INSERT OR IGNORE INTO price_history VALUES ({placeholders_hist})", hist)
        print(f"-> {len(hist)} registros de historial diario insertados")

    # 5. Índice FTS5
    c_src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='products_fts'")
    fts_row = c_src.fetchone()
    if fts_row and fts_row[0]:
        try:
            c_dst.execute(fts_row[0])
            c_dst.execute("INSERT INTO products_fts(rowid, name, category, brand, ean) SELECT rowid, name, category, brand, ean FROM products")
            print("-> FTS5 de búsqueda rápida creado")
        except Exception as e:
            print("Aviso FTS5:", e)

    # 6. Índices secundarios
    c_src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")
    for row in c_src.fetchall():
        try:
            c_dst.execute(row[0])
        except Exception:
            pass

    conn_dst.commit()
    conn_dst.execute("VACUUM")
    conn_dst.close()
    conn_src.close()

    if os.path.exists(TARGET_DB):
        os.remove(TARGET_DB)
    os.rename(temp_target, TARGET_DB)

    sz = os.path.getsize(TARGET_DB)
    print(f"¡Base seed generada con éxito en {TARGET_DB}!")
    print(f"Tamaño final: {sz / (1024*1024):.2f} MB (Apta para subir a GitHub sin restricciones)")

if __name__ == '__main__':
    export_seed()
