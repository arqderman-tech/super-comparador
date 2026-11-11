"""
init_db.py
---------------------------------------------------------------------
Inicializa una base de datos SQLite limpia con el esquema completo
de tablas, índices y motor de búsqueda de texto completo (FTS5).
---------------------------------------------------------------------
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1. Tabla products
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        ean TEXT,
        name TEXT NOT NULL,
        category TEXT,
        category_id TEXT,
        brand TEXT,
        price_regular REAL DEFAULT 0,
        price_promo REAL DEFAULT 0,
        is_promo INTEGER DEFAULT 0,
        discount_pct REAL DEFAULT 0,
        image_url TEXT,
        url TEXT,
        supermarket TEXT DEFAULT 'La Gallega',
        pack_units INTEGER DEFAULT 1,
        pack_detail TEXT,
        is_available INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 2. Tabla competitor_prices
    c.execute("""
    CREATE TABLE IF NOT EXISTS competitor_prices (
        product_id TEXT,
        ean TEXT,
        supermarket TEXT,
        price REAL,
        difference_pct REAL,
        regular_price REAL,
        promo_price REAL,
        has_promo INTEGER DEFAULT 0,
        promo_units INTEGER DEFAULT 1,
        promo_detail TEXT,
        PRIMARY KEY (product_id, supermarket)
    )
    """)

    # 3. Tabla price_history
    c.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        ean TEXT,
        fecha INTEGER,
        precio REAL,
        supermarket_id INTEGER DEFAULT 0,
        PRIMARY KEY (ean, fecha, supermarket_id)
    )
    """)

    # 4. Tabla scrape_logs
    c.execute("""
    CREATE TABLE IF NOT EXISTS scrape_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        supermarket TEXT,
        status TEXT,
        total_items INTEGER,
        message TEXT
    )
    """)

    # 5. Índices de optimización
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_supermarket ON products(supermarket)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_ean ON competitor_prices(ean)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_supermarket ON competitor_prices(supermarket)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hist_ean_fecha_supermarket ON price_history(ean, fecha, supermarket_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hist_fecha ON price_history(fecha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hist_ean_fecha ON price_history(ean, fecha)")

    # 6. Tabla FTS5 para búsqueda rápida instantánea
    try:
        c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            name, category, brand, ean,
            content='products',
            content_rowid='rowid'
        )
        """)
    except Exception as e:
        print(f"Aviso FTS5: {e}")

    conn.commit()
    conn.close()
    print(f"Base de datos inicializada en: {DB_PATH}")

if __name__ == '__main__':
    init_db()
