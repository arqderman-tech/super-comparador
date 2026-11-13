"""
db_split.py
---------------------------------------------------------------------
Gestor de partición y ensamblado binario de la base de datos completa.
Permite mantener la base de datos entera (~400 MB, 145.000+ productos)
en GitHub sin violar el límite de tamaño de archivo (100 MB).

Cada bloque se guarda como `database.db.partXX` con tamaño de 45 MB.
Al arrancar el servidor o en GitHub Actions, se ensambla en menos de 1 segundo.
---------------------------------------------------------------------
"""

import os
import glob
import hashlib
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
PART_PATTERN = os.path.join(BASE_DIR, "database.db.part*")
PART_FORMAT = os.path.join(BASE_DIR, "database.db.part{:02d}")
CHUNK_SIZE = 45 * 1024 * 1024  # 45 MB por bloque (seguro para GitHub)

def get_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def split_db(src_path=DB_PATH, chunk_size=CHUNK_SIZE):
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"No existe el archivo origen: {src_path}")

    total_size = os.path.getsize(src_path)
    total_mb = total_size / (1024 * 1024)
    print(f"=== Particionando base completa: {src_path} ({total_mb:.2f} MB) ===")

    # Limpiar partes viejas si existen
    for p in glob.glob(PART_PATTERN):
        try:
            os.remove(p)
        except Exception:
            pass

    t0 = time.time()
    part_idx = 0
    with open(src_path, 'rb') as f_in:
        while True:
            chunk = f_in.read(chunk_size)
            if not chunk:
                break
            part_filename = PART_FORMAT.format(part_idx)
            with open(part_filename, 'wb') as f_out:
                f_out.write(chunk)
            part_mb = len(chunk) / (1024 * 1024)
            print(f"  [+] Generado bloque {os.path.basename(part_filename)}: {part_mb:.2f} MB")
            part_idx += 1

    elapsed = time.time() - t0
    print(f"[OK] Partición completada en {elapsed:.2f}s: {part_idx} bloques generados.")
    return part_idx

def join_db(target_path=DB_PATH):
    parts = sorted(glob.glob(PART_PATTERN))
    if not parts:
        raise FileNotFoundError(f"No se encontraron bloques {PART_PATTERN} para ensamblar.")

    print(f"=== Ensamblando base completa desde {len(parts)} bloques ===")
    t0 = time.time()
    temp_target = target_path + ".tmp"
    if os.path.exists(temp_target):
        os.remove(temp_target)

    with open(temp_target, 'wb') as f_out:
        for p in parts:
            part_mb = os.path.getsize(p) / (1024 * 1024)
            print(f"  [>] Ensamblando {os.path.basename(p)} ({part_mb:.2f} MB)...")
            with open(p, 'rb') as f_in:
                while chunk := f_in.read(4 * 1024 * 1024):
                    f_out.write(chunk)

    # Validar que sea un archivo SQLite válido
    with open(temp_target, 'rb') as f:
        header = f.read(16)
        if header != b'SQLite format 3\x00':
            os.remove(temp_target)
            raise ValueError("Error: El archivo ensamblado no tiene una cabecera válida de SQLite 3.")

    if os.path.exists(target_path):
        os.remove(target_path)
    os.rename(temp_target, target_path)

    elapsed = time.time() - t0
    total_mb = os.path.getsize(target_path) / (1024 * 1024)
    print(f"[OK] Base de datos ensamblada con éxito en {target_path} ({total_mb:.2f} MB) en {elapsed:.2f}s.")
    return target_path

def ensure_db(target_path=DB_PATH):
    """
    Garantiza que database.db exista y esté lista para usar.
    Si ya existe y tiene tamaño > 0, no hace nada.
    Si falta pero existen los bloques database.db.part*, los ensambla al instante.
    """
    if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        return target_path

    parts = sorted(glob.glob(PART_PATTERN))
    if parts:
        print("[*] database.db no encontrada o vacía. Ensamblando desde bloques de GitHub...")
        return join_db(target_path)

    raise FileNotFoundError(f"No se encontró {target_path} ni los bloques {PART_PATTERN}")

def db_status():
    parts = sorted(glob.glob(PART_PATTERN))
    print("=== ESTADO DE LA BASE DE DATOS ===")
    if os.path.exists(DB_PATH):
        mb = os.path.getsize(DB_PATH) / (1024 * 1024)
        print(f"  Archivo único: database.db ({mb:.2f} MB)")
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM products")
            p = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM competitor_prices")
            cp = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM price_history")
            h = c.fetchone()[0]
            print(f"  Productos: {p:,} | Precios cruzados: {cp:,} | Registros historial: {h:,}")
            conn.close()
        except Exception as e:
            print(f"  Aviso al leer datos: {e}")
    else:
        print("  Archivo único: NO PRESENTE (requiere ensamblado)")

    if parts:
        print(f"  Bloques particionados: {len(parts)} archivos ({sum(os.path.getsize(p) for p in parts)/(1024*1024):.2f} MB totales)")
        for p in parts:
            print(f"    - {os.path.basename(p)}: {os.path.getsize(p)/(1024*1024):.2f} MB")
    else:
        print("  Bloques particionados: NINGUNO")

if __name__ == '__main__':
    import sys
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if cmd == "split":
        split_db()
    elif cmd == "join":
        join_db()
    elif cmd == "status":
        db_status()
    elif cmd == "ensure":
        ensure_db()
    else:
        print(f"Comando desconocido: {cmd}. Usar: split | join | status | ensure")
