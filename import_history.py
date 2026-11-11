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
COMPACT_CSV = os.path.join(DATA_DIR, "precios_compacto.csv")
PRECIOS_DIR = os.path.join(DATA_DIR, "precios")
BASE_URL = "https://raw.githubusercontent.com/arqderman-tech/GALLEBOT/main/data/precios"
MANIFEST_URL = f"{BASE_URL}/manifest.json"
CSV_LEGACY_URL = "https://raw.githubusercontent.com/arqderman-tech/GALLEBOT/main/data/precios_compacto.csv"

def download_manifest():
    """Descarga el manifest.json para saber qué archivos de histórico existen"""
    try:
        response = requests.get(MANIFEST_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except:
        return None

def get_historical_df():
    """Obtiene el DataFrame del histórico (ya sea particionado o legado)"""
    # Intentar con el nuevo sistema particionado
    manifest = download_manifest()

    if manifest and 'partes' in manifest:
        partes = manifest['partes']
        print(f"Usando {len(partes)} archivos particionados...")

        all_dfs = []
        for parte in partes:
            archivo = parte['archivo']
            url = f"{BASE_URL}/{archivo}"
            local_path = os.path.join(PRECIOS_DIR, archivo)

            try:
                response = requests.get(url, stream=True, timeout=60)
                response.raise_for_status()

                os.makedirs(PRECIOS_DIR, exist_ok=True)
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                df = pd.read_csv(local_path, encoding='utf-8')
                all_dfs.append(df)
                print(f"  Cargado {archivo}: {len(df):,} filas")

            except Exception as e:
                print(f"  Error descargando {archivo}: {e}")

        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
    else:
        # Fallback al CSV legado
        print("Usando CSV legado...")
        if os.path.exists(COMPACT_CSV):
            return pd.read_csv(COMPACT_CSV, encoding='utf-8')
        else:
            # Descargar desde GitHub
            print("Descargando CSV legado desde GitHub...")
            response = requests.get(CSV_LEGACY_URL, stream=True, timeout=60)
            response.raise_for_status()

            with open(COMPACT_CSV, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return pd.read_csv(COMPACT_CSV, encoding='utf-8')

    return None

def init_history_table(conn):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ean TEXT NOT NULL,
        fecha TEXT NOT NULL,
        precio REAL NOT NULL
    );
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_hist_ean_fecha ON price_history(ean, fecha);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hist_fecha ON price_history(fecha);")
    conn.commit()

def import_csv():
    # Obtener el DataFrame del histórico
    df = get_historical_df()
    if df is None:
        print("Error: No se pudo obtener el histórico.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    init_history_table(conn)
    c = conn.cursor()

    # Check if already imported
    c.execute("SELECT COUNT(*) FROM price_history")
    count_existing = c.fetchone()[0]
    if count_existing > 500000:
        print(f"La tabla price_history ya tiene {count_existing} registros.")
        conn.close()
        return count_existing

    print(f"Iniciando importación masiva ({len(df):,} registros)...")
    start_time = time.time()

    batch = []
    total_imported = 0
    batch_size = 50000

    for _, row in df.iterrows():
        ean = str(row.get("ean", "")).strip()
        fecha = str(row.get("fecha", "")).strip()
        precio_str = str(row.get("precio", "0")).strip()
        if not ean or not fecha:
            continue
        try:
            precio = float(precio_str)
        except:
            continue

        batch.append((ean, fecha, precio))

        if len(batch) >= batch_size:
            c.executemany("INSERT INTO price_history (ean, fecha, precio) VALUES (?, ?, ?)", batch)
            conn.commit()
            total_imported += len(batch)
            print(f"  -> Importados {total_imported:,} registros...")
            batch = []

    if batch:
        c.executemany("INSERT INTO price_history (ean, fecha, precio) VALUES (?, ?, ?)", batch)
        conn.commit()
        total_imported += len(batch)

    elapsed = round(time.time() - start_time, 2)
    print(f"\n==========================================")
    print(f"IMPORTACIÓN COMPLETADA")
    print(f"Total registros históricos importados: {total_imported:,}")
    print(f"Tiempo: {elapsed} segundos")
    print(f"==========================================")

    # Also add price history of TODAY from products table!
    today = datetime.now().strftime("%Y%m%d")
    print(f"Registrando precios de hoy ({today}) en el histórico...")
    c.execute("""
    INSERT INTO price_history (ean, fecha, precio)
    SELECT ean, ?, price_promo
    FROM products
    WHERE ean != '' AND price_promo > 0
    """, (today,))
    conn.commit()
    conn.close()
    return total_imported

if __name__ == "__main__":
    import_csv()
