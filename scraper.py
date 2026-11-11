import requests
from bs4 import BeautifulSoup
import re
import time
import json
import csv
import os
import sys
from typing import List, Dict, Any, Callable, Optional

from db import init_db, bulk_upsert_products, get_connection

BASE_URL = "https://www.lagallega.com.ar"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'es-AR,es;q=0.9,en;q=0.8'
}

CATEGORIES = [
    ('03', 'Almacén'),
    ('07', 'Bebidas'),
    ('05', 'Hogar y Jardín'),
    ('13', 'Carnicería'),
    ('15', 'Congelados'),
    ('06', 'Desayuno y Merienda'),
    ('04', 'Electro'),
    ('19', 'Frutas y Verduras'),
    ('23', 'Indumentaria y Calzado'),
    ('08', 'Lácteos y Frescos'),
    ('16', 'Limpieza'),
    ('18', 'Mascotas'),
    ('17', 'Mundo Bebé'),
    ('14', 'Panificados'),
    ('20', 'Perfumería')
]

current_status = {
    "is_running": False,
    "current_category": "",
    "current_page": 0,
    "total_pages": 0,
    "total_items_scraped": 0,
    "message": "Inactivo",
    "error": None
}

def parse_price(txt: Optional[str]) -> Optional[float]:
    if not txt:
        return None
    m = re.search(r'[\d\.\,]+', txt)
    if not m:
        return None
    val_str = m.group(0).replace('.', '').replace(',', '.')
    try:
        return float(val_str)
    except:
        return None


def detectar_pack_y_precio_unitario(nombre: str, precio: float) -> tuple:
    """
    Detecta si el producto es un pack basado en el nombre y devuelve:
    - precio_unitario: precio por unidad equivalente
    - unidades: cantidad de unidades en el pack
    """
    if not nombre or not precio:
        return precio, 1
    
    nombre_lower = nombre.lower()
    
    # Patrones de detección de packs
    patrones = [
        (r'x\s*(\d+)\s*unidades?', 1),
        (r'x\s*(\d+)\s*uni\.', 1),
        (r'x\s*(\d+)\s*u\.?', 1),
        (r'pack\s*x\s*(\d+)', 1),
        (r'pack\s*de\s*(\d+)', 1),
        (r'(\d+)\s*unidades', 1),
        (r'(\d+)\s*uni\.', 1),
        (r'(\d+)\s*u\.?', 1),
    ]
    
    for patron, factor in patrones:
        match = re.search(patron, nombre_lower)
        if match:
            unidades = int(match.group(1))
            if unidades > 1:
                precio_unitario = precio / unidades
                return precio_unitario, unidades
    
    return precio, 1

def parse_product_card(item, category_name: str, category_id: str) -> Optional[Dict[str, Any]]:
    try:
        desc_el = item.find('div', class_='desc')
        name = desc_el.get_text(strip=True) if desc_el else ''
        
        img_el = item.find('img')
        alt = img_el.get('alt', '') if img_el else ''
        img_src = img_el.get('src', '') if img_el else ''
        if img_src and not img_src.startswith('http'):
            img_src = f"{BASE_URL}/{img_src.lstrip('/')}"
            
        ean = ''
        if alt:
            m_ean = re.match(r'^(\d{6,14})\s*-\s*(.+)', alt)
            if m_ean:
                ean = m_ean.group(1)
                if not name:
                    name = m_ean.group(2).strip()
                    
        # Product ID
        pid = None
        btn = item.find('div', class_='BotonAgrega')
        if btn and btn.get('onclick'):
            m_pid = re.search(r"PCompra\('?(\d+)'?\)", btn['onclick'])
            if m_pid:
                pid = m_pid.group(1)
                
        # Fallback ID from detail link or image
        if not pid:
            a_link = item.find('a', href=re.compile(r'Pr=(\d+)'))
            if a_link:
                m_pid2 = re.search(r'Pr=(\d+)', a_link['href'])
                if m_pid2:
                    pid = m_pid2.group(1)
        if not pid and img_src:
            m_pid3 = re.search(r'/(\d+)\.(jpg|png)', img_src)
            if m_pid3:
                pid = m_pid3.group(1)
                
        if not pid or not name:
            return None
            
        # Prices
        reg_price = None
        promo_price = None
        is_promo = False
        
        izqdes = item.find('div', class_='izqdes')
        der = item.find('div', class_='der')
        izq = item.find('div', class_='izq')
        
        if izqdes and der:
            reg_price = parse_price(izqdes.get_text())
            promo_price = parse_price(der.get_text())
            is_promo = True
        elif izq:
            reg_price = parse_price(izq.get_text())
            promo_price = reg_price
        elif der:
            promo_price = parse_price(der.get_text())
            reg_price = promo_price
            
        return {
            'id': pid,
            'ean': ean,
            'name': name,
            'category': category_name,
            'category_id': category_id,
            'price_regular': reg_price or promo_price or 0.0,
            'price_promo': promo_price or reg_price or 0.0,
            'is_promo': is_promo,
            'image': img_src,
            'url': f"{BASE_URL}/productosdet.asp?Pr={pid}"
        }
    except Exception as e:
        return None

def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    # Establish session at Sucursal Mitre (18)
    try:
        s.get(f"{BASE_URL}/login.asp?SE=18", timeout=12)
    except Exception as e:
        print(f"Warning connecting to login: {e}")
    # Request 100 products per page
    s.cookies.set('cantP', '100')
    return s

def scrape_category(s: requests.Session, cat_id: str, cat_name: str, 
                    max_pages_per_cat: int = 50,
                    progress_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
    cat_products = []
    page = 1
    total_pages = 1
    
    while page <= max_pages_per_cat and page <= total_pages:
        url = f"{BASE_URL}/productosnl.asp?pg={page}&nl={cat_id}000000"
        try:
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                print(f"  [Cat {cat_id}] HTTP {r.status_code} on page {page}")
                break
                
            soup = BeautifulSoup(r.text, 'html.parser')
            items = soup.find_all('li', class_='cuadProd')
            if not items:
                break
                
            # Determine total pages if on page 1
            if page == 1:
                pie = soup.find('td', class_='pieBot')
                if pie:
                    m_pages = re.search(r'1\s+de\s+(\d+)', pie.get_text())
                    if m_pages:
                        total_pages = int(m_pages.group(1))
                        
            parsed_count = 0
            for item in items:
                p = parse_product_card(item, cat_name, cat_id)
                if p:
                    cat_products.append(p)
                    parsed_count += 1
                    
            print(f"  [{cat_name}] Pag {page}/{total_pages}: {parsed_count} productos procesados (Total cat: {len(cat_products)})")
            
            if progress_callback:
                progress_callback({
                    'category': cat_name,
                    'page': page,
                    'total_pages': total_pages,
                    'items_found': parsed_count,
                    'total_cat_items': len(cat_products)
                })
                
            # Check if there is next page
            has_next = any('Siguiente' in (inp.get('value') or '') for inp in soup.find_all('input'))
            if not has_next or page >= total_pages:
                break
                
            page += 1
            time.sleep(0.3)  # Polite crawling delay
            
        except Exception as e:
            print(f"  [Cat {cat_id}] Error en pag {page}: {e}")
            break
            
    return cat_products

def run_full_scrape(max_pages_per_cat: int = 50, callback: Optional[Callable] = None):
    global current_status
    current_status["is_running"] = True
    current_status["error"] = None
    current_status["total_items_scraped"] = 0
    current_status["message"] = "Iniciando extracción de productos de La Gallega..."
    
    init_db()
    s = create_session()
    
    all_products = []
    total_saved = 0
    
    start_time = time.time()
    
    try:
        for idx, (cid, cname) in enumerate(CATEGORIES, 1):
            current_status["current_category"] = cname
            current_status["message"] = f"Extrayendo categoría {idx}/{len(CATEGORIES)}: {cname}..."
            print(f"\n==================================================")
            print(f"[{idx}/{len(CATEGORIES)}] Extrayendo categoría: {cname} (ID: {cid})")
            print(f"==================================================")
            
            def page_cb(info):
                current_status["current_page"] = info["page"]
                current_status["total_pages"] = info["total_pages"]
                if callback:
                    callback(info)
                    
            cat_prods = scrape_category(s, cid, cname, max_pages_per_cat=max_pages_per_cat, progress_callback=page_cb)
            
            if cat_prods:
                saved = bulk_upsert_products(cat_prods)
                total_saved += saved
                current_status["total_items_scraped"] = total_saved
                all_products.extend(cat_prods)
                print(f"  -> Guardados/actualizados {saved} productos de {cname} en la base de datos.")
                
            time.sleep(0.5)
            
        elapsed = round(time.time() - start_time, 1)
        
        
        # Update price_history for today
        today_str = time.strftime("%Y%m%d")
        conn = get_connection()
        c = conn.cursor()
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_ean_fecha_unique ON price_history(ean, fecha);")
        c.execute("""
        INSERT OR REPLACE INTO price_history (ean, fecha, precio)
        SELECT ean, ?, price_promo
        FROM products
        WHERE ean != '' AND ean IS NOT NULL AND price_promo > 0
        """, (today_str,))
        hist_count = c.rowcount

        # Export full catalog files from database
        project_dir = os.path.dirname(__file__)
        json_path = os.path.join(project_dir, "lagallega_products.json")
        csv_path = os.path.join(project_dir, "lagallega_products.csv")

        c.execute("""
        SELECT id, ean, name, category, category_id, brand, price_regular, price_promo, is_promo, discount_pct, image_url, url
        FROM products
        ORDER BY category, name
        """)
        db_rows = [dict(r) for r in c.fetchall()]

        print(f"Exportando catalogo completo ({len(db_rows)} productos) a {json_path}...")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(db_rows, f, ensure_ascii=False, indent=2)

        print(f"Exportando catalogo completo a {csv_path}...")
        if db_rows:
            keys = list(db_rows[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(db_rows)

        # Log to db
        c.execute("""
        INSERT INTO scrape_logs (status, total_items, message)
        VALUES ('SUCCESS', ?, ?)
        """, (total_saved, f"Descargados e indexados {total_saved} productos ({hist_count} en histórico) en {elapsed}s"))
        conn.commit()
        conn.close()
        
        current_status["is_running"] = False
        current_status["message"] = f"Finalizado exitosamente: {total_saved} productos indexados en {elapsed}s"
        print(f"\n==================================================")
        print(f"PROCESO COMPLETADO EXITOSAMENTE")
        print(f"Total productos indexados: {total_saved}")
        print(f"Tiempo total: {elapsed} segundos")
        print(f"Archivos generados: lagallega_products.json y lagallega_products.csv")
        print(f"==================================================")
        
    except Exception as e:
        current_status["is_running"] = False
        current_status["error"] = str(e)
        current_status["message"] = f"Error en scraping: {e}"
        print(f"ERROR: {e}")
        
    return total_saved

def main():
    return run_full_scrape()

if __name__ == "__main__":
    max_p = 50
    if len(sys.argv) > 1:
        try:
            max_p = int(sys.argv[1])
        except:
            pass
    run_full_scrape(max_pages_per_cat=max_p)
