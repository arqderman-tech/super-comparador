import os
import sys
import time
import logging
import sqlite3
import json
import csv
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "daily_update.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("daily_update")

def main():
    log.info("=" * 60)
    log.info("INICIANDO ACTUALIZACION DIARIA AUTOMATICA - SUPERCOMPARADOR")
    log.info("=" * 60)
    start_total = time.time()

    # 1. Scrapear precios de La Gallega
    log.info("[1/8] Scrapeando precios actualizados de La Gallega...")
    try:
        import scraper
        scraper.main()
        log.info("[OK] Precios de La Gallega actualizados.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear La Gallega: {e}", exc_info=True)

    # 2. Actualizar histórico desde GitHub (GALLEBOT)
    log.info("[2/8] Descargando y sincronizando histórico de precios desde GitHub...")
    try:
        import update_history
        update_history.main(skip_download=False)
        log.info("[OK] Histórico de precios sincronizado.")
    except Exception as e:
        log.error(f"[ERROR] Error al sincronizar histórico: {e}", exc_info=True)

    # 3. Scrapear precios frescos de COTO Digital
    log.info("[3/8] Scrapeando precios actualizados de COTO Digital...")
    try:
        import scraper_coto
        scraper_coto.main()
        log.info("[OK] Precios de COTO Digital actualizados y comparados.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear COTO: {e}", exc_info=True)

    # 4. Scrapear e importar precios de Carrefour
    log.info("[4/8] Scrapeando precios actualizados de Carrefour...")
    try:
        import carrefour_scraper
        carrefour_scraper.main()
        log.info("[OK] Precios de Carrefour actualizados y sincronizados en DB.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear Carrefour: {e}", exc_info=True)

    # 5. Scrapear precios de La Reina
    log.info("[5/8] Scrapeando precios actualizados de La Reina...")
    try:
        import asyncio
        import scraper_reina
        asyncio.run(scraper_reina.main())
        log.info("[OK] Precios de La Reina actualizados y comparados.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear La Reina: {e}", exc_info=True)

    # 6. Scrapear precios de Jumbo
    log.info("[6/8] Scrapeando precios actualizados de Jumbo...")
    try:
        import scraper_jumbo
        scraper_jumbo.main()
        log.info("[OK] Precios de Jumbo actualizados y comparados.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear Jumbo: {e}", exc_info=True)

    # 7. Scrapear precios de La Anónima
    log.info("[7/8] Scrapeando precios actualizados de La Anónima...")
    try:
        import scraper_laanonima
        scraper_laanonima.main()
        log.info("[OK] Precios de La Anónima actualizados y comparados.")
    except Exception as e:
        log.error(f"[ERROR] Error al scrapear La Anónima: {e}", exc_info=True)

    # 8. Exportar catálogo actualizado a CSV y JSON
    log.info("[8/8] Exportando catálogo completo a JSON y CSV...")
    try:
        from db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
        SELECT id, ean, name, category, category_id, brand, price_regular, price_promo, is_promo, discount_pct, image_url, url, supermarket, updated_at
        FROM products
        ORDER BY category, name
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        json_path = os.path.join(BASE_DIR, "lagallega_products.json")
        csv_path = os.path.join(BASE_DIR, "lagallega_products.csv")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)

        if rows:
            keys = list(rows[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(rows)
        log.info(f"[OK] Archivos exportados exitosamente ({len(rows)} productos).")
    except Exception as e:
        log.error(f"[ERROR] Error al exportar archivos: {e}", exc_info=True)

    elapsed = round(time.time() - start_total, 1)
    log.info("=" * 60)
    log.info(f"ACTUALIZACION DIARIA FINALIZADA EXITOSAMENTE EN {elapsed}s")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
