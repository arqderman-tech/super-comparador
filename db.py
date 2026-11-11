import sqlite3
import re
import os
import unicodedata
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

# Códigos de supermercado (0=La Gallega, 1=Coto, 2=Carrefour, 3=La Reina)
SUPERMARKET_CODES = {
    'La Gallega': 0,
    'Coto': 1,
    'Carrefour': 2,
    'La Reina': 3,
    'Jumbo': 4,
    'La Anónima': 5
}

SUPERMARKET_NAMES = {v: k for k, v in SUPERMARKET_CODES.items()}

def normalize_text(text: str) -> str:
    """Normaliza texto eliminando acentos y convirtiendo a minúsculas"""
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('ASCII').lower()

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # Products table
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        ean TEXT,
        name TEXT NOT NULL,
        category TEXT,
        category_id TEXT,
        brand TEXT,
        price_regular REAL,
        price_promo REAL,
        is_promo INTEGER DEFAULT 0,
        discount_pct REAL DEFAULT 0,
        image_url TEXT,
        url TEXT,
        supermarket TEXT DEFAULT 'La Gallega',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        pack_units INTEGER DEFAULT 1,
        pack_detail TEXT,
        is_available INTEGER DEFAULT 1
    );
    """)

    # Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_promo ON products(is_promo);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price_promo);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_disc ON products(discount_pct);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_available ON products(is_available);")

    # Competitor benchmark prices
    c.execute("""
    CREATE TABLE IF NOT EXISTS competitor_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT,
        ean TEXT,
        supermarket TEXT NOT NULL,
        price REAL NOT NULL,
        difference_pct REAL,
        regular_price REAL,
        promo_price REAL,
        has_promo INTEGER DEFAULT 0,
        promo_units INTEGER DEFAULT 1,
        promo_detail TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_ean ON competitor_prices(ean);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_super ON competitor_prices(supermarket);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_prod_super ON competitor_prices(product_id, supermarket);")

    # Migration for existing competitor_prices table to add promo columns if missing
    c.execute("PRAGMA table_info(competitor_prices);")
    existing_cols = [r[1] for r in c.fetchall()]
    new_cols = [
        ("regular_price", "REAL"),
        ("promo_price", "REAL"),
        ("has_promo", "INTEGER DEFAULT 0"),
        ("promo_units", "INTEGER DEFAULT 1"),
        ("promo_detail", "TEXT")
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            try:
                c.execute(f"ALTER TABLE competitor_prices ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

    # Migration for products table to add pack and availability columns if missing
    c.execute("PRAGMA table_info(products);")
    existing_cols = [r[1] for r in c.fetchall()]
    prod_cols = [
        ("pack_units", "INTEGER DEFAULT 1"),
        ("pack_detail", "TEXT"),
        ("is_available", "INTEGER DEFAULT 1")
    ]
    for col_name, col_type in prod_cols:
        if col_name not in existing_cols:
            try:
                c.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

    # Scrape run log
    c.execute("""
    CREATE TABLE IF NOT EXISTS scrape_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT,
        total_items INTEGER,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Performance indexes for multi-supermarket catalog & fast EAN cross-matching
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_products_supermarket ON products(supermarket);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_products_super_cat ON products(supermarket, category);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_products_super_ean ON products(supermarket, ean);")
    except Exception as e:
        print(f"Warning: Index creation note: {e}")

    # FTS5 Full-Text Search
    try:
        c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            id,
            ean,
            name,
            category,
            brand,
            content='products',
            content_rowid='rowid'
        );
        """)

        # Triggers to keep FTS5 synchronized
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
            INSERT INTO products_fts(rowid, id, ean, name, category, brand) 
            VALUES (new.rowid, new.id, new.ean, new.name, new.category, new.brand);
        END;
        """)

        c.execute("""
        CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, id, ean, name, category, brand) 
            VALUES('delete', old.rowid, old.id, old.ean, old.name, old.category, old.brand);
        END;
        """)

        c.execute("""
        CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, id, ean, name, category, brand) 
            VALUES('delete', old.rowid, old.id, old.ean, old.name, old.category, old.brand);
            INSERT INTO products_fts(rowid, id, ean, name, category, brand) 
            VALUES (new.rowid, new.id, new.ean, new.name, new.category, new.brand);
        END;
        """)
    except Exception as e:
        print(f"Warning: FTS5 initialization note: {e}")

    conn.commit()
    conn.close()

def extract_brand(name: str) -> str:
    """Heuristic extractor for popular brand names in Argentina"""
    common_brands = [
        "La Serenisima", "La Serenísima", "Sancor", "Ilolay", "Tregar", "Veronica", "Verónica",
        "Arcor", "Bagley", "Terrabusi", "Oreo", "Milka", "Marilaca", "Aguila", "Águila",
        "Coca Cola", "Coca-Cola", "Pepsi", "Sprite", "Fanta", "Manaos", "Villa del Sur", "Villavicencio",
        "Quilmes", "Brahma", "Heineken", "Stella Artois", "Schneider", "Imperial", "Corona",
        "Cocinero", "Natura", "Cañuelas", "Morixe", "Pureza", "Favorita", "Lucchetti", "Matarazzo",
        "Knorr", "Hellmanns", "Hellmann's", "Dánica", "Fanacoa", "Savora",
        "Gallo", "Marolio", "Molto", "Noel", "La Campagnola",
        "Ala", "Ariel", "Skip", "Drive", "Magistral", "Cif", "Ayudin", "Ayudín", "Procénex", "Vivere", "Comfort",
        "Colgate", "Oral-B", "Sedal", "Dove", "Pantene", "Rexona", "Axe", "Nivea", "Gillette", "Pampers", "Huggies",
        "Whiskas", "Pedigree", "Dog Chow", "Cat Chow", "Purina", "Raza"
    ]
    name_lower = name.lower()
    for b in common_brands:
        pattern = r'\b' + re.escape(b.lower()) + r'\b'
        if re.search(pattern, name_lower):
            return b
    # Fallback: first 1 or 2 capitalized words or 'General'
    return ""

def bulk_upsert_products(items: List[Dict[str, Any]]):
    if not items:
        return 0
    conn = get_connection()
    c = conn.cursor()
    
    upsert_query = """
    INSERT INTO products (
        id, ean, name, category, category_id, brand,
        price_regular, price_promo, is_promo, discount_pct,
        image_url, url, supermarket, updated_at
    ) VALUES (
        :id, :ean, :name, :category, :category_id, :brand,
        :price_regular, :price_promo, :is_promo, :discount_pct,
        :image_url, :url, :supermarket, CURRENT_TIMESTAMP
    )
    ON CONFLICT(id) DO UPDATE SET
        ean = COALESCE(excluded.ean, products.ean),
        name = excluded.name,
        category = COALESCE(excluded.category, products.category),
        category_id = COALESCE(excluded.category_id, products.category_id),
        brand = CASE WHEN excluded.brand != '' THEN excluded.brand ELSE products.brand END,
        price_regular = excluded.price_regular,
        price_promo = excluded.price_promo,
        is_promo = excluded.is_promo,
        discount_pct = excluded.discount_pct,
        image_url = excluded.image_url,
        url = excluded.url,
        updated_at = CURRENT_TIMESTAMP;
    """
    
    # Process brands and discount percentages
    cleaned_items = []
    for item in items:
        p_reg = item.get('price_regular') or item.get('price_promo') or 0.0
        p_pro = item.get('price_promo') or p_reg
        # Usar exactamente el valor is_promo del scraper, no recalcular
        is_p = 1 if item.get('is_promo') else 0
        disc = 0.0
        if is_p and p_reg > 0 and p_reg > p_pro:
            disc = round(((p_reg - p_pro) / p_reg) * 100, 1)
        
        brand = item.get('brand') or extract_brand(item.get('name', ''))
        
        cleaned_items.append({
            'id': str(item['id']),
            'ean': item.get('ean') or '',
            'name': item.get('name', '').strip(),
            'category': item.get('category') or 'General',
            'category_id': item.get('category_id') or '',
            'brand': brand,
            'price_regular': p_reg,
            'price_promo': p_pro,
            'is_promo': is_p,
            'discount_pct': disc,
            'image_url': item.get('image') or item.get('image_url') or '',
            'url': item.get('url') or f"https://www.lagallega.com.ar/productosdet.asp?Pr={item['id']}",
            'supermarket': 'La Gallega'
        })
        
    c.executemany(upsert_query, cleaned_items)
    conn.commit()

    # Almacenar histórico de precios de La Gallega
    from datetime import datetime
    today_int = int(datetime.now().strftime("%Y%m%d"))
    history_inserts = []
    for item in cleaned_items:
        ean = item.get('ean')
        price = item.get('price_promo')
        if ean and price > 0:
            history_inserts.append((ean, today_int, price, SUPERMARKET_CODES['La Gallega']))

    if history_inserts:
        c.executemany("""
        INSERT OR REPLACE INTO price_history (ean, fecha, precio, supermarket_id)
        VALUES (?, ?, ?, ?)
        """, history_inserts)
        conn.commit()

    conn.close()
    return len(cleaned_items)

def clear_simulated_competitors():
    """
    Elimina todos los precios simulados de competidores.
    Solo se mantienen los precios reales de COTO (obtenidos via scraper_coto.py)
    """
    conn = get_connection()
    c = conn.cursor()

    # Eliminar solo supermercados simulados (Día)
    c.execute("DELETE FROM competitor_prices WHERE supermarket IN ('Día', 'Dia')")
    deleted = c.rowcount

    conn.commit()
    conn.close()
    return deleted

def get_supermarket_updates(conn=None) -> Dict[str, Any]:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    c = conn.cursor()

    c.execute("SELECT supermarket, MAX(updated_at) FROM products WHERE updated_at IS NOT NULL GROUP BY supermarket")
    prod_updates = {row[0]: row[1] for row in c.fetchall() if row[0]}

    c.execute("SELECT supermarket, MAX(updated_at) FROM competitor_prices WHERE updated_at IS NOT NULL GROUP BY supermarket")
    comp_updates = {row[0]: row[1] for row in c.fetchall() if row[0]}

    c.execute("SELECT MAX(created_at) FROM scrape_logs")
    last_scrape_log = c.fetchone()[0]

    c.execute("SELECT MAX(fecha) FROM price_history WHERE supermarket_id = 0")
    gallega_hist = c.fetchone()[0]
    gallega_hist_dt = None
    if gallega_hist:
        try:
            s_gh = str(gallega_hist)
            if len(s_gh) == 8:
                gallega_hist_dt = f"{s_gh[:4]}-{s_gh[4:6]}-{s_gh[6:]} 00:00:00"
        except Exception:
            pass

    supermarkets = ['La Gallega', 'Coto', 'Carrefour', 'La Reina', 'Jumbo', 'La Anónima']
    updates = {}
    for sm in supermarkets:
        candidates = []
        if sm in prod_updates and prod_updates[sm]:
            candidates.append(str(prod_updates[sm]))
        if sm in comp_updates and comp_updates[sm]:
            candidates.append(str(comp_updates[sm]))
        if sm == 'La Gallega':
            if last_scrape_log:
                candidates.append(str(last_scrape_log))
            if gallega_hist_dt:
                candidates.append(gallega_hist_dt)
        updates[sm] = max(candidates) if candidates else None

    if close_after:
        conn.close()

    return updates

def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*), COUNT(CASE WHEN is_promo = 1 THEN 1 END), AVG(price_promo), MAX(discount_pct) FROM products")
    row = c.fetchone()
    total_prods, promo_count, avg_price, max_disc = row[0], row[1], row[2], row[3]
    
    c.execute("SELECT COUNT(DISTINCT category) FROM products")
    total_cats = c.fetchone()[0]
    
    c.execute("""
    SELECT category, COUNT(*) as count, AVG(price_promo) as avg_price, 
           SUM(CASE WHEN is_promo = 1 THEN 1 ELSE 0 END) as promo_count
    FROM products
    GROUP BY category
    ORDER BY count DESC
    """)
    cat_distribution = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT MAX(created_at) FROM scrape_logs")
    last_scrape = c.fetchone()[0]

    # Competitor & Product stats per supermarket
    c.execute("SELECT supermarket, COUNT(*) FROM products WHERE (is_available = 1 OR is_available IS NULL) GROUP BY supermarket")
    p_counts = {row[0]: row[1] for row in c.fetchall()}

    c.execute("SELECT supermarket, COUNT(*) FROM competitor_prices GROUP BY supermarket")
    comp_stats = {row[0]: row[1] for row in c.fetchall()}

    supermarket_counts = {
        'La Gallega': p_counts.get('La Gallega', total_prods or 0),
        'Carrefour': p_counts.get('Carrefour') or comp_stats.get('Carrefour', 0),
        'Coto': p_counts.get('Coto') or comp_stats.get('Coto', 0),
        'La Reina': p_counts.get('La Reina') or comp_stats.get('La Reina', 0),
        'Jumbo': p_counts.get('Jumbo') or comp_stats.get('Jumbo', 0),
        'La Anónima': p_counts.get('La Anónima') or comp_stats.get('La Anónima', 0)
    }

    supermarket_updates = get_supermarket_updates(conn)

    conn.close()
    return {
        'total_products': total_prods or 0,
        'promo_count': promo_count or 0,
        'promo_pct': round((promo_count / total_prods * 100), 1) if total_prods else 0,
        'avg_price': round(avg_price or 0, 2),
        'max_discount': max_disc or 0,
        'total_categories': total_cats or 0,
        'categories': cat_distribution,
        'last_scrape': last_scrape,
        'competitor_coverage': comp_stats,
        'supermarket_counts': supermarket_counts,
        'supermarket_updates': supermarket_updates
    }

def search_products(query: str = "", category: str = "", promo_only: bool = False,
                    sort_by: str = "relevance", min_price: float = 0, max_price: float = 0,
                    limit: int = 40, offset: int = 0, supermarket: str = "La Gallega") -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()

    norm_super = "La Gallega"
    if supermarket and supermarket.lower() in ("coto", "cotodigital"):
        norm_super = "Coto"
    elif supermarket and supermarket.lower() in ("carrefour", "carre"):
        norm_super = "Carrefour"
    elif supermarket and supermarket.lower() in ("la reina", "lareina", "reina"):
        norm_super = "La Reina"
    elif supermarket and supermarket.lower() in ("jumbo", "jumbo argentina", "jumbo online"):
        norm_super = "Jumbo"
    elif supermarket and supermarket.lower() in ("la anonima", "laanonima", "anonima", "la anónima"):
        norm_super = "La Anónima"

    clean_q = query.strip()
    words = [w for w in re.split(r'\s+', clean_q) if len(w) > 1] if clean_q else []

    # Check if we have native catalog in products table for this supermarket
    c.execute("SELECT COUNT(*) FROM products WHERE supermarket = ?", (norm_super,))
    has_native_catalog = (c.fetchone()[0] > 0)

    if has_native_catalog:
        params = [norm_super]
        where_clauses = ["supermarket = ?", "(is_available = 1 OR is_available IS NULL)"]

        if category:
            where_clauses.append("category = ?")
            params.append(category)

        if promo_only:
            where_clauses.append("(is_promo = 1 OR discount_pct > 0)")

        if min_price > 0:
            where_clauses.append("price_promo >= ?")
            params.append(min_price)

        if max_price > 0:
            where_clauses.append("price_promo <= ?")
            params.append(max_price)

        if clean_q:
            # Normalizar query para ignorar acentos
            norm_q = normalize_text(clean_q)
            words = [w for w in re.split(r'\s+', norm_q) if len(w) > 1] if norm_q else []

            if clean_q.isdigit():
                where_clauses.append("(ean LIKE ? OR id = ?)")
                params.extend([f"%{clean_q}%", clean_q])
            else:
                if words:
                    for w in words:
                        where_clauses.append("(LOWER(name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(category) LIKE ?)")
                        params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])
                else:
                    where_clauses.append("(LOWER(name) LIKE ? OR LOWER(brand) LIKE ?)")
                    params.extend([f"%{norm_q}%", f"%{norm_q}%"])

        where_sql = " AND ".join(where_clauses)

        order_sql = "discount_pct DESC, id ASC"
        if sort_by == "price_asc":
            order_sql = "price_promo ASC"
        elif sort_by == "price_desc":
            order_sql = "price_promo DESC"
        elif sort_by == "discount":
            order_sql = "discount_pct DESC, price_promo ASC"
        elif sort_by == "name":
            order_sql = "name ASC"

        c.execute(f"SELECT COUNT(*) FROM products WHERE {where_sql}", params)
        total = c.fetchone()[0]

        fetch_sql = f"""
        SELECT id, ean, name, category, brand, price_regular, price_promo, 
               is_promo, discount_pct, image_url, url, supermarket, updated_at,
               pack_units, pack_detail
        FROM products
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """
        c.execute(fetch_sql, params + [limit, offset])
        items = [dict(r) for r in c.fetchall()]

        if items:
            # Multi-supermarket comparison via EAN
            eans = [i['ean'] for i in items if i.get('ean')]
            comp_map = {}
            if eans:
                placeholders = ','.join(['?'] * len(eans))
                c.execute(f"""
                SELECT ean, supermarket, price_promo, price_regular, is_promo, discount_pct, pack_units, pack_detail, url
                FROM products
                WHERE ean IN ({placeholders}) AND supermarket != ? AND price_promo > 0 AND (is_available = 1 OR is_available IS NULL)
                """, eans + [norm_super])
                for row in c.fetchall():
                    e = row['ean']
                    if e not in comp_map:
                        comp_map[e] = []
                    comp_map[e].append(dict(row))

                # Cross-check competitor_prices for promotional metadata and fallback
                c.execute(f"""
                SELECT cp.ean, cp.supermarket, cp.price as price_promo, COALESCE(cp.regular_price, cp.price) as price_regular,
                       cp.has_promo as is_promo, 0 as discount_pct, cp.promo_units as pack_units, cp.promo_detail as pack_detail,
                       cp.promo_price
                FROM competitor_prices cp
                WHERE cp.ean IN ({placeholders}) AND cp.supermarket != ? AND cp.price > 0
                """, eans + [norm_super])
                cp_lookup = {}
                for row in c.fetchall():
                    e = row['ean']
                    s = row['supermarket']
                    cp_lookup[(e, s)] = dict(row)
                    if e not in comp_map:
                        comp_map[e] = []
                    # Only add if not already in comp_map for this supermarket
                    if not any(x['supermarket'] == s for x in comp_map[e]):
                        comp_map[e].append(dict(row))

            for item in items:
                cur_price = item['price_promo']
                e = item.get('ean')
                comps = []
                if e and e in comp_map:
                    for m in comp_map[e]:
                        s = m['supermarket']
                        other_p = m['price_promo']
                        diff_pct = round(((other_p - cur_price) / cur_price) * 100, 1) if cur_price else 0.0
                        cp_info = cp_lookup.get((e, s), {})
                        reg_p = cp_info.get('price_regular') or m.get('price_regular') or other_p
                        p_price = cp_info.get('promo_price')
                        if not p_price and m.get('is_promo'):
                            p_price = other_p if (reg_p and other_p < reg_p) else None
                        has_p = bool(cp_info.get('is_promo') or m.get('is_promo') or (reg_p and p_price and p_price < reg_p))
                        p_units = cp_info.get('pack_units') or m.get('pack_units') or 1
                        p_detail = cp_info.get('pack_detail') or m.get('pack_detail') or ''

                        comps.append({
                            'supermarket': s,
                            'price': other_p,
                            'difference_pct': diff_pct,
                            'regular_price': reg_p,
                            'promo_price': p_price,
                            'has_promo': has_p,
                            'promo_units': p_units,
                            'promo_detail': p_detail
                        })
                comps.sort(key=lambda x: x['price'])
                item['competitors'] = comps

                # Format store URL if missing
                if not item.get('url'):
                    if norm_super == 'Coto':
                        item['url'] = f"https://www.cotodigital3.com.ar/sitios/cdigi/browse?Ntt={e}" if e else "https://www.cotodigital3.com.ar/"
                    elif norm_super == 'Carrefour':
                        item['url'] = f"https://www.carrefour.com.ar/{e}?_q={e}&map=ft" if e else "https://www.carrefour.com.ar/"
                    elif norm_super == 'La Reina':
                        item['url'] = f"https://www.lareinaonline.com.ar/busqueda.asp?txt={e}" if e else "https://www.lareinaonline.com.ar/"
                    elif norm_super == 'Jumbo':
                        item['url'] = f"https://www.jumbo.com.ar/{e}?_q={e}&map=ft" if e else "https://www.jumbo.com.ar/"
                    elif norm_super == 'La Anónima':
                        item['url'] = f"https://www.laanonima.com.ar/buscar/{e}" if e else "https://www.laanonima.com.ar/"

    else:
        # Fallback query for competitor table when products table not yet populated
        params = [norm_super]
        where_clauses = ["cp.supermarket = ?"]

        if category:
            where_clauses.append("p.category = ?")
            params.append(category)

        if promo_only:
            where_clauses.append("cp.has_promo = 1")

        if min_price > 0:
            where_clauses.append("cp.price >= ?")
            params.append(min_price)

        if max_price > 0:
            where_clauses.append("cp.price <= ?")
            params.append(max_price)

        if clean_q:
            if clean_q.isdigit():
                where_clauses.append("(p.ean LIKE ? OR p.id = ?)")
                params.extend([f"%{clean_q}%", clean_q])
            else:
                if words:
                    for w in words:
                        where_clauses.append("(p.name LIKE ? OR p.brand LIKE ? OR p.category LIKE ?)")
                        params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])
                else:
                    where_clauses.append("(p.name LIKE ? OR p.brand LIKE ?)")
                    params.extend([f"%{clean_q}%", f"%{clean_q}%"])

        where_sql = " AND ".join(where_clauses)

        order_sql = "cp.has_promo DESC, cp.price ASC"
        if sort_by == "price_asc":
            order_sql = "cp.price ASC"
        elif sort_by == "price_desc":
            order_sql = "cp.price DESC"
        elif sort_by == "discount":
            order_sql = "discount_pct DESC, cp.price ASC"
        elif sort_by == "name":
            order_sql = "p.name ASC"

        c.execute(f"""
        SELECT COUNT(*)
        FROM competitor_prices cp
        JOIN products p ON cp.product_id = p.id
        WHERE {where_sql}
        """, params)
        total = c.fetchone()[0]

        fetch_sql = f"""
        SELECT cp.id as comp_id, p.id, p.ean, p.name, p.category, p.brand,
               COALESCE(cp.regular_price, cp.price) as price_regular,
               cp.price as price_promo,
               cp.has_promo as is_promo,
               CASE WHEN cp.regular_price > cp.price 
                    THEN ROUND(((cp.regular_price - cp.price) / cp.regular_price) * 100, 1) 
                    ELSE 0.0 END as discount_pct,
               p.image_url,
               p.url as lg_url,
               cp.supermarket,
               cp.promo_units,
               cp.promo_detail,
               p.price_promo as lg_price
        FROM competitor_prices cp
        JOIN products p ON cp.product_id = p.id
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """
        c.execute(fetch_sql, params + [limit, offset])
        raw_items = [dict(r) for r in c.fetchall()]

        items = []
        if raw_items:
            pids = [i['id'] for i in raw_items]
            placeholders = ','.join(['?'] * len(pids))
            c.execute(f"""
            SELECT product_id, supermarket, price, difference_pct, regular_price, promo_price, has_promo, promo_units, promo_detail
            FROM competitor_prices
            WHERE product_id IN ({placeholders}) AND supermarket != ?
            """, pids + [norm_super])
            other_comps = {}
            for r in c.fetchall():
                pid = r['product_id']
                if pid not in other_comps:
                    other_comps[pid] = []
                other_comps[pid].append(dict(r))

            for item in raw_items:
                cur_price = item['price_promo']
                comps = []
                if item.get('lg_price') and item['lg_price'] > 0:
                    lg_diff = round(((item['lg_price'] - cur_price) / cur_price) * 100, 1) if cur_price else 0
                    comps.append({
                        'supermarket': 'La Gallega',
                        'price': item['lg_price'],
                        'difference_pct': lg_diff,
                        'regular_price': item['lg_price'],
                        'promo_price': None,
                        'has_promo': False,
                        'promo_units': 1,
                        'promo_detail': ''
                    })
                for oc in other_comps.get(item['id'], []):
                    oc_diff = round(((oc['price'] - cur_price) / cur_price) * 100, 1) if cur_price else 0
                    comps.append({
                        'supermarket': oc['supermarket'],
                        'price': oc['price'],
                        'difference_pct': oc_diff,
                        'regular_price': oc['regular_price'] if 'regular_price' in oc.keys() else oc['price'],
                        'promo_price': oc['promo_price'] if 'promo_price' in oc.keys() else None,
                        'has_promo': bool(oc['has_promo']) if 'has_promo' in oc.keys() and oc['has_promo'] else False,
                        'promo_units': oc['promo_units'] if 'promo_units' in oc.keys() and oc['promo_units'] else 1,
                        'promo_detail': oc['promo_detail'] if 'promo_detail' in oc.keys() and oc['promo_detail'] else ''
                    })

                ean_val = item.get('ean') or ''
                if norm_super == 'Coto':
                    store_url = f"https://www.cotodigital3.com.ar/sitios/cdigi/browse?Ntt={ean_val}" if ean_val else "https://www.cotodigital3.com.ar/"
                elif norm_super == 'Carrefour':
                    store_url = f"https://www.carrefour.com.ar/{ean_val}?_q={ean_val}&map=ft" if ean_val else "https://www.carrefour.com.ar/"
                elif norm_super == 'La Reina':
                    store_url = f"https://www.lareinaonline.com.ar/busqueda.asp?txt={ean_val}" if ean_val else "https://www.lareinaonline.com.ar/"
                elif norm_super == 'Jumbo':
                    store_url = f"https://www.jumbo.com.ar/{ean_val}?_q={ean_val}&map=ft" if ean_val else "https://www.jumbo.com.ar/"
                elif norm_super == 'La Anónima':
                    store_url = f"https://www.laanonima.com.ar/buscar/{ean_val}" if ean_val else "https://www.laanonima.com.ar/"
                else:
                    store_url = item.get('lg_url') or ""

                items.append({
                    'id': item['id'],
                    'ean': item['ean'],
                    'name': item['name'],
                    'category': item['category'],
                    'brand': item['brand'],
                    'price_regular': item['price_regular'],
                    'price_promo': item['price_promo'],
                    'is_promo': item['is_promo'],
                    'discount_pct': item['discount_pct'],
                    'image_url': item['image_url'],
                    'url': store_url,
                    'supermarket': norm_super,
                    'competitors': comps,
                    'promo_detail': item.get('promo_detail') or ''
                })

    conn.close()
    return {
        'total': total,
        'limit': limit,
        'offset': offset,
        'items': items,
        'supermarket': norm_super
    }

def get_top_deals(limit: int = 12, supermarket: str = "La Gallega") -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()

    norm_super = "La Gallega"
    if supermarket and supermarket.lower() in ("coto", "cotodigital"):
        norm_super = "Coto"
    elif supermarket and supermarket.lower() in ("carrefour", "carre"):
        norm_super = "Carrefour"
    elif supermarket and supermarket.lower() in ("la reina", "lareina", "reina"):
        norm_super = "La Reina"
    elif supermarket and supermarket.lower() in ("jumbo", "jumbo argentina", "jumbo online"):
        norm_super = "Jumbo"
    elif supermarket and supermarket.lower() in ("la anonima", "laanonima", "anonima", "la anónima"):
        norm_super = "La Anónima"

    c.execute("SELECT COUNT(*) FROM products WHERE supermarket = ?", (norm_super,))
    has_native = (c.fetchone()[0] > 0)

    if has_native:
        c.execute("""
        SELECT id, ean, name, category, brand, price_regular, price_promo, 
               is_promo, discount_pct, image_url, url, supermarket
        FROM products
        WHERE supermarket = ? AND (is_promo = 1 OR discount_pct > 0 OR price_regular > price_promo) AND (is_available = 1 OR is_available IS NULL)
        ORDER BY discount_pct DESC, price_promo ASC
        LIMIT ?
        """, (norm_super, limit))
        rows = [dict(r) for r in c.fetchall()]
    else:
        c.execute("""
        SELECT cp.id as comp_id, p.id, p.ean, p.name, p.category, p.brand,
               COALESCE(cp.regular_price, cp.price) as price_regular,
               cp.price as price_promo,
               cp.has_promo as is_promo,
               CASE WHEN cp.regular_price > cp.price 
                    THEN ROUND(((cp.regular_price - cp.price) / cp.regular_price) * 100, 1) 
                    ELSE 0.0 END as discount_pct,
               p.image_url,
               cp.supermarket
        FROM competitor_prices cp
        JOIN products p ON cp.product_id = p.id
        WHERE cp.supermarket = ? AND (cp.has_promo = 1 OR cp.regular_price > cp.price)
        ORDER BY discount_pct DESC, cp.price ASC
        LIMIT ?
        """, (norm_super, limit))
        rows = [dict(r) for r in c.fetchall()]

    conn.close()
    return rows

def compare_basket(product_ids: List[str]) -> Dict[str, Any]:
    """
    Calculate the total cost of a shopping basket across all 4 supermarkets.
    Supports products added from any supermarket tab.
    """
    if not product_ids:
        return {'supermarkets': [], 'total_items': 0, 'items': [], 'comparison': []}
        
    conn = get_connection()
    c = conn.cursor()
    placeholders = ','.join(['?'] * len(product_ids))
    
    c.execute(f"""
    SELECT id, ean, name, price_promo, image_url, category, supermarket
    FROM products
    WHERE id IN ({placeholders})
    """, product_ids)
    basket_items = [dict(r) for r in c.fetchall()]
    total_items = len(basket_items)
    
    if not basket_items:
        conn.close()
        return {'supermarkets': [], 'total_items': 0, 'items': [], 'comparison': []}

    eans = [i['ean'] for i in basket_items if i.get('ean')]
    all_supers = ['La Gallega', 'Carrefour', 'Coto', 'La Reina', 'Jumbo', 'La Anónima']

    # Cross-supermarket price mapping via EAN
    ean_price_map = {}
    if eans:
        e_placeholders = ','.join(['?'] * len(eans))
        # 1. From products table
        c.execute(f"""
        SELECT ean, supermarket, price_promo
        FROM products
        WHERE ean IN ({e_placeholders}) AND price_promo > 0
        """, eans)
        for r in c.fetchall():
            k = (r['ean'], r['supermarket'])
            if k not in ean_price_map or r['price_promo'] < ean_price_map[k]:
                ean_price_map[k] = r['price_promo']

        # 2. From competitor_prices table
        c.execute(f"""
        SELECT ean, supermarket, price as price_promo
        FROM competitor_prices
        WHERE ean IN ({e_placeholders}) AND price > 0
        """, eans)
        for r in c.fetchall():
            k = (r['ean'], r['supermarket'])
            if k not in ean_price_map:
                ean_price_map[k] = r['price_promo']

    # Calculate totals per supermarket
    store_totals = {s: 0.0 for s in all_supers}
    store_matched_lg = {s: 0.0 for s in all_supers}
    store_counts = {s: 0 for s in all_supers}

    # Reference benchmark is La Gallega price (or item price)
    for item in basket_items:
        item_s = item.get('supermarket') or 'La Gallega'
        item_p = item.get('price_promo') or 0.0
        ean = item.get('ean')

        # Find La Gallega price for this item as standard comparison base
        lg_price = item_p if item_s == 'La Gallega' else (ean_price_map.get((ean, 'La Gallega')) or item_p)

        for s in all_supers:
            if s == item_s:
                store_totals[s] += item_p
                store_matched_lg[s] += lg_price
                store_counts[s] += 1
            elif ean and (ean, s) in ean_price_map:
                store_totals[s] += ean_price_map[(ean, s)]
                store_matched_lg[s] += lg_price
                store_counts[s] += 1

    comp_totals = []
    lg_total = round(store_totals['La Gallega'], 2)
    comp_totals.append({
        'supermarket': 'La Gallega',
        'total': lg_total,
        'difference_amount': 0.0,
        'difference_pct': 0.0,
        'is_cheapest': False,
        'matched_count': store_counts['La Gallega'],
        'is_complete': (store_counts['La Gallega'] == total_items)
    })

    for s in ['Carrefour', 'Coto', 'La Reina', 'Jumbo', 'La Anónima']:
        count = store_counts[s]
        total = round(store_totals[s], 2)
        lg_matched = store_matched_lg[s]
        is_complete = (count == total_items)
        diff_amt = round(total - lg_matched, 2)
        diff_pct = round(((total - lg_matched) / lg_matched) * 100, 1) if lg_matched else 0.0

        comp_totals.append({
            'supermarket': s,
            'total': total,
            'difference_amount': diff_amt,
            'difference_pct': diff_pct,
            'is_cheapest': False,
            'matched_count': count,
            'is_complete': is_complete
        })

    # Determine cheapest
    complete_totals = [ct for ct in comp_totals if ct['is_complete']]
    if complete_totals:
        cheapest = min(complete_totals, key=lambda x: x['total'])
        cheapest['is_cheapest'] = True
    elif comp_totals:
        cheapest = min(comp_totals, key=lambda x: x['total'])
        cheapest['is_cheapest'] = True

    conn.close()
    return {
        'total_items': total_items,
        'items': basket_items,
        'comparison': sorted(comp_totals, key=lambda x: (-x['is_cheapest'], x['total']))
    }

def get_product_history(ean: str, supermarket: str = None) -> Dict[str, Any]:
    if not ean:
        return {'history': [], 'stats': None, 'supermarket': supermarket or 'La Gallega'}

    conn = get_connection()
    c = conn.cursor()

    # Normalizar nombre de supermercado
    norm_super = 'La Gallega'
    if supermarket and isinstance(supermarket, str):
        s_low = supermarket.lower()
        if 'coto' in s_low:
            norm_super = 'Coto'
        elif 'carrefour' in s_low or 'carre' in s_low:
            norm_super = 'Carrefour'
        elif 'reina' in s_low:
            norm_super = 'La Reina'
        elif 'jumbo' in s_low:
            norm_super = 'Jumbo'
        elif 'anonima' in s_low or 'anónima' in s_low:
            norm_super = 'La Anónima'

    supermarket_id = SUPERMARKET_CODES.get(norm_super, 0)

    # 1. Obtener precio actual del producto en ESTE supermercado
    # Primero buscamos en products
    c.execute("""
    SELECT price_promo, price_regular, updated_at
    FROM products
    WHERE ean = ? AND supermarket = ?
    LIMIT 1
    """, (ean, norm_super))
    current_row = c.fetchone()

    # Si no está en products, buscamos en competitor_prices
    if not current_row:
        c.execute("""
        SELECT price as price_promo, regular_price as price_regular, updated_at
        FROM competitor_prices
        WHERE ean = ? AND supermarket = ?
        LIMIT 1
        """, (ean, norm_super))
        current_row = c.fetchone()

    current_price = float(current_row['price_promo']) if current_row and current_row['price_promo'] else None
    current_date = current_row['updated_at'] if current_row else None

    # 2. Obtener histórico de price_history filtrado EXCLUSIVAMENTE por este supermercado
    c.execute("""
    SELECT fecha, precio
    FROM price_history
    WHERE ean = ? AND supermarket_id = ?
    ORDER BY fecha ASC
    """, (ean, supermarket_id))
    rows = c.fetchall()
    conn.close()

    if not rows and not current_price:
        return {'history': [], 'stats': None, 'supermarket': norm_super}

    # Format date: 20260220 (integer) -> 2026-02-20
    history = []
    prices = []
    for r in rows:
        f = r['fecha']
        p = float(r['precio'])
        f_str = str(f)
        f_fmt = f"{f_str[:4]}-{f_str[4:6]}-{f_str[6:]}" if len(f_str) == 8 else f_str
        history.append({'date': f_fmt, 'price': p})
        prices.append(p)

    # Si hay precio actual y no hay historial o el precio actual difiere del último
    if current_price:
        today = current_date.split()[0] if current_date and ' ' in str(current_date) else (str(current_date) if current_date else datetime.now().strftime('%Y-%m-%d'))
        if not prices:
            history.append({'date': today, 'price': current_price})
            prices.append(current_price)
        elif abs(current_price - prices[-1]) > 0.01:
            if not history or history[-1]['date'] != today:
                history.append({'date': today, 'price': current_price})
                prices.append(current_price)

    if not prices:
        return {'history': [], 'stats': None, 'supermarket': norm_super}

    first_price = prices[0]
    last_price = prices[-1]
    min_price = min(prices)
    max_price = max(prices)
    avg_price = round(sum(prices) / len(prices), 2)
    inflation_pct = round(((last_price - first_price) / first_price) * 100, 1) if first_price > 0 else 0

    # Calculate pre-deal baseline (average of first 90% of history or last 30 days)
    recent_prices = prices[-30:] if len(prices) >= 30 else prices
    recent_baseline = round(sum(recent_prices) / len(recent_prices), 2)

    return {
        'supermarket': norm_super,
        'history': history,
        'stats': {
            'first_date': history[0]['date'],
            'last_date': history[-1]['date'],
            'total_points': len(history),
            'first_price': first_price,
            'current_price': last_price,
            'min_price': min_price,
            'max_price': max_price,
            'avg_price': avg_price,
            'recent_baseline': recent_baseline,
            'inflation_pct': inflation_pct
        }
    }

def audit_product_deal(product: Dict[str, Any], hist_stats: Optional[Dict[str, Any]], history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Audit whether an advertised promo is REAL or INFLATED.
    Compares against the last higher price before the current drop, not the average.
    """
    if not product.get('is_promo') or not hist_stats:
        return {'status': 'NO_PROMO', 'label': 'Precio Regular', 'color': 'slate', 'badge': ''}

    price_promo = product.get('price_promo', 0)
    price_reg = product.get('price_regular', 0)
    total_points = hist_stats.get('total_points', 0)

    # Si hay menos de 5 puntos históricos, no hay suficiente historial para dictar una auditoría de oferta
    if total_points < 5:
        return {'status': 'UNKNOWN', 'label': 'Historial en formación', 'color': 'slate', 'badge': ''}

    # Encontrar el último precio más alto anterior a la rebaja
    baseline = None
    if history and len(history) > 0:
        # Recorrer el historial de atrás hacia adelante (excluyendo el precio actual)
        for i in range(len(history) - 2, -1, -1):
            prev_price = history[i].get('price', 0)
            if prev_price > price_promo:
                baseline = prev_price
                break

    # Si no encontramos un precio más alto, usar el máximo histórico
    if baseline is None:
        baseline = hist_stats.get('max_price', 0)

    if baseline <= 0:
        return {'status': 'UNKNOWN', 'label': 'Historial insuficiente', 'color': 'slate', 'badge': ''}

    # Difference vs pre-offer baseline (último precio más alto)
    savings_vs_history_pct = round(((baseline - price_promo) / baseline) * 100, 1)

    # Check if regular price was inflated artificially above historical max
    hist_max = hist_stats.get('max_price', 0)
    inflated_reg = price_reg > (hist_max * 1.15) and price_promo >= baseline

    if savings_vs_history_pct >= 10:
        return {
            'status': 'VERIFIED_DEAL',
            'label': 'Oferta Real Verificada',
            'detail': f'Ahorro real del {savings_vs_history_pct}% vs precio anterior (${baseline:,.0f})',
            'color': 'emerald',
            'badge': 'bg-emerald-100 text-emerald-800 border-emerald-300',
            'savings_pct': savings_vs_history_pct
        }
    elif savings_vs_history_pct <= -5 or inflated_reg:
        return {
            'status': 'INFLATED_DEAL',
            'label': 'Alerta: Oferta Cuestionable',
            'detail': f'El precio de oferta (${price_promo:,.0f}) supera o iguala el precio anterior (${baseline:,.0f})',
            'color': 'rose',
            'badge': 'bg-rose-100 text-rose-800 border-rose-300',
            'savings_pct': savings_vs_history_pct
        }
    else:
        return {
            'status': 'MODERATE_DEAL',
            'label': 'Oferta Leve / Mantiene Precio',
            'detail': f'Precio similar al histórico (${baseline:,.0f})',
            'color': 'amber',
            'badge': 'bg-amber-100 text-amber-800 border-amber-300',
            'savings_pct': savings_vs_history_pct
        }

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at", DB_PATH)
