import sqlite3
import csv
import os
import time
import requests
import json
import pandas as pd
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
# URLs de GALLEBOT - ahora usa archivos particionados
BASE_URL = "https://raw.githubusercontent.com/arqderman-tech/GALLEBOT/main/data/precios"
MANIFEST_URL = f"{BASE_URL}/manifest.json"
CSV_LEGACY_URL = "https://raw.githubusercontent.com/arqderman-tech/GALLEBOT/main/data/precios_compacto.csv"
CSV_LEGACY_PATH = os.path.join(DATA_DIR, "precios_compacto.csv")
PRECIOS_DIR = os.path.join(DATA_DIR, "precios")

def download_manifest():
    """Descarga el manifest.json para saber qué archivos de histórico existen"""
    print(f"Descargando manifest desde {MANIFEST_URL}...")
    try:
        response = requests.get(MANIFEST_URL, timeout=30)
        response.raise_for_status()
        manifest = response.json()
        print(f"[OK] Manifest descargado: {len(manifest.get('partes', []))} partes encontradas")
        return manifest
    except Exception as e:
        print(f"[WARNING] No se pudo descargar manifest: {e}")
        print("  Intentando con CSV legado...")
        return None

def download_csv():
    """Descarga los archivos particionados de histórico desde GitHub"""
    os.makedirs(PRECIOS_DIR, exist_ok=True)

    # Intentar con el nuevo sistema particionado
    manifest = download_manifest()

    if manifest and 'partes' in manifest:
        # Descargar cada parte
        partes = manifest['partes']
        print(f"Descargando {len(partes)} archivos de histórico...")

        all_dfs = []
        for parte in partes:
            archivo = parte['archivo']
            url = f"{BASE_URL}/{archivo}"
            local_path = os.path.join(PRECIOS_DIR, archivo)

            print(f"  Descargando {archivo} ({parte['filas']:,} filas, {parte['bytes']:,} bytes)...")

            try:
                response = requests.get(url, stream=True, timeout=60)
                response.raise_for_status()

                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                # Leer el CSV y agregar al DataFrame combinado
                df = pd.read_csv(local_path, encoding='utf-8')
                all_dfs.append(df)
                print(f"    [OK] {archivo} cargado: {len(df):,} filas")

            except Exception as e:
                print(f"    [ERROR] Error descargando {archivo}: {e}")

        if all_dfs:
            df_combined = pd.concat(all_dfs, ignore_index=True)
            print(f"\n[OK] Histórico combinado: {len(df_combined):,} registros totales")
            return df_combined
        else:
            print("[ERROR] No se pudo descargar ninguna parte")
            return None
    else:
        # Fallback al CSV legado
        print(f"Descargando CSV legado desde {CSV_LEGACY_URL}...")
        try:
            response = requests.get(CSV_LEGACY_URL, stream=True, timeout=60)
            response.raise_for_status()

            downloaded = 0
            with open(CSV_LEGACY_PATH, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            print(f"[OK] CSV legado descargado ({downloaded:,} bytes)")
            df = pd.read_csv(CSV_LEGACY_PATH, encoding='utf-8')
            return df

        except Exception as e:
            print(f"[ERROR] Error descargando CSV legado: {e}")
            return None

def get_existing_dates_in_db(conn):
    """Obtiene todas las fechas ya registradas en la base de datos"""
    c = conn.cursor()
    c.execute("SELECT DISTINCT fecha FROM price_history")
    return set(str(row[0]) for row in c.fetchall())

def import_new_records(conn, df):
    """Importa registros del DataFrame con fechas que no estén ya en la base de datos"""
    c = conn.cursor()

    # Asegurar índice único
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_ean_fecha_supermarket ON price_history(ean, fecha, supermarket_id);")
    conn.commit()

    from db import SUPERMARKET_CODES
    supermarket_id = SUPERMARKET_CODES['La Gallega']

    existing_dates = get_existing_dates_in_db(conn)
    if existing_dates:
        print(f"Fechas ya presentes en DB ({len(existing_dates)} fechas). Más reciente: {max(existing_dates)}")
    else:
        print("No hay datos previos en DB, importando todo...")

    if df is None or len(df) == 0:
        print("[ERROR] No hay datos para importar")
        return 0

    # Convertir fecha a integer si no lo es
    if 'fecha' in df.columns:
        df['fecha'] = pd.to_numeric(df['fecha'], errors='coerce').fillna(0).astype(int)

    # Filtrar registros de fechas nuevas
    df_filtered = df[~df['fecha'].isin(existing_dates)]

    if len(df_filtered) == 0:
        print("No hay fechas nuevas para importar")
        return 0

    fechas_nuevas = sorted(df_filtered['fecha'].unique())
    print(f"Fechas nuevas a importar: {len(fechas_nuevas)}")
    print(f"Registros nuevos: {len(df_filtered):,}")

    # Importar en batches
    batch = []
    total_imported = 0
    batch_size = 50000

    start_time = time.time()

    for _, row in df_filtered.iterrows():
        ean = str(row.get("ean", "")).strip()
        fecha = row.get("fecha", 0)
        precio_str = str(row.get("precio", "0")).strip()

        if not ean or fecha == 0:
            continue

        try:
            precio = float(precio_str)
        except:
            continue

        batch.append((ean, int(fecha), precio, supermarket_id))

        if len(batch) >= batch_size:
            c.executemany("INSERT OR IGNORE INTO price_history (ean, fecha, precio, supermarket_id) VALUES (?, ?, ?, ?)", batch)
            conn.commit()
            total_imported += len(batch)
            print(f"  -> Importados {total_imported:,} registros nuevos...")
            batch = []

    if batch:
        c.executemany("INSERT OR IGNORE INTO price_history (ean, fecha, precio, supermarket_id) VALUES (?, ?, ?, ?)", batch)
        conn.commit()
        total_imported += len(batch)

    elapsed = round(time.time() - start_time, 2)

    print(f"\n{'='*50}")
    print(f"IMPORTACIÓN HISTÓRICA COMPLETADA")
    print(f"Registros nuevos importados: {total_imported:,}")
    print(f"Fechas nuevas agregadas: {fechas_nuevas}")
    print(f"Tiempo: {elapsed} segundos")
    print(f"{'='*50}")

    return total_imported

def update_today_prices(conn, force=False):
    """Actualiza los precios de hoy en price_history desde la tabla products"""
    today_date = datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y%m%d")
    c = conn.cursor()

    # Verificar cuándo fue el último scrape de products
    c.execute("SELECT MAX(updated_at) FROM products")
    row = c.fetchone()
    last_updated = row[0] if row and row[0] else ""

    is_today = last_updated.startswith(today_date)
    if not is_today and not force:
        print(f"\n[INFO] Los productos en 'products' corresponden a {last_updated[:10]}.")
        print("  (Para actualizar al día de hoy, ejecute scraper.py)")
        return 0

    print(f"\nActualizando precios de hoy ({today_str}) desde products...")
    c.execute("""
    INSERT OR REPLACE INTO price_history (ean, fecha, precio)
    SELECT ean, ?, price_promo
    FROM products
    WHERE ean != '' AND price_promo > 0
    """, (today_str,))
    added = c.rowcount
    conn.commit()

    print(f"Precios de hoy registrados en price_history: {added:,}")
    return added

def log_update(conn, status, message):
    """Registra el update en la tabla de logs"""
    c = conn.cursor()
    c.execute("""
    INSERT INTO scrape_logs (status, total_items, message)
    VALUES (?, ?, ?)
    """, (status, 0, message))
    conn.commit()

def main(skip_download=False):
    print(f"\n{'='*60}")
    print(f"ACTUALIZACIÓN DE HISTÓRICO DE PRECIOS")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Crear directorio data si no existe
    os.makedirs(DATA_DIR, exist_ok=True)

    # Descargar CSV (si no se salta)
    if not skip_download:
        df_hist = download_csv()
        if df_hist is None:
            print("[ERROR] No se pudo descargar el histórico")
            return
    else:
        print("Saltando descarga (usando archivos existentes)...")
        # Intentar cargar archivos existentes
        try:
            manifest_path = os.path.join(PRECIOS_DIR, "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                partes = manifest.get('partes', [])
                all_dfs = []
                for parte in partes:
                    archivo = parte['archivo']
                    local_path = os.path.join(PRECIOS_DIR, archivo)
                    if os.path.exists(local_path):
                        df = pd.read_csv(local_path, encoding='utf-8')
                        all_dfs.append(df)
                if all_dfs:
                    df_hist = pd.concat(all_dfs, ignore_index=True)
                else:
                    df_hist = None
            elif os.path.exists(CSV_LEGACY_PATH):
                df_hist = pd.read_csv(CSV_LEGACY_PATH, encoding='utf-8')
            else:
                df_hist = None
        except Exception as e:
            print(f"[ERROR] Error cargando archivos existentes: {e}")
            df_hist = None

    # Conectar a DB
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")

    try:
        # Importar registros nuevos
        new_records = import_new_records(conn, df_hist)

        # Actualizar precios de hoy
        today_records = update_today_prices(conn)

        # Log del proceso
        message = f"Histórico actualizado: {new_records:,} registros nuevos, {today_records:,} precios de hoy"
        log_update(conn, "SUCCESS", message)

        print(f"\n[OK] Proceso completado exitosamente")
        print(f"Mensaje de log: {message}")

    except Exception as e:
        error_msg = f"Error en actualización: {e}"
        print(f"\n[ERROR] {error_msg}")
        log_update(conn, "ERROR", error_msg)

    finally:
        conn.close()

if __name__ == "__main__":
    main()
