# Configuración de Actualización Automática - Historial de Precios

## Opción 1: Windows Task Scheduler (Recomendado)

El script `update_history.py` descarga automáticamente el CSV desde GitHub y actualiza la base de datos.

### Pasos para configurar:

1. **Abrir Programador de Tareas de Windows**
   - Presiona `Win + R`, escribe `taskschd.msc` y presiona Enter

2. **Crear una nueva tarea**
   - Click derecho en "Biblioteca del Programador de Tareas"
   - Selecciona "Crear tarea básica..."

3. **Configurar la tarea**
   - **Nombre**: `SuperComparador - Actualizar Historial`
   - **Descripción**: Descarga CSV de precios históricos desde GitHub y actualiza la base de datos
   - Click en "Siguiente"

4. **Configurar el disparador (Trigger)**
   - Selecciona "Diariamente"
   - Configura la hora (ej: 10:00 AM - 1 hora después del scraper de GALLEBOT)
   - Click en "Siguiente"

5. **Configurar la acción**
   - Selecciona "Iniciar un programa"
   - **Programa o script**: `C:\Users\usuario\super-comparador\update_history.bat`
   - **Iniciar en**: `C:\Users\usuario\super-comparador`
   - Click en "Siguiente"

6. **Finalizar**
   - Revisa la configuración
   - Click en "Finalizar"

7. **Probar la tarea**
   - Encuentra la tarea en la lista
   - Click derecho → "Ejecutar"
   - Verifica que se ejecute correctamente

---

## Opción 2: Ejecución Manual

Cuando quieras actualizar el histórico manualmente:

```cmd
cd C:\Users\usuario\super-comparador
python update_history.py
```

O haz doble clic en `update_history.bat`

---

## Opción 3: Cron Job (Linux/Mac)

Si usas WSL o Linux:

```bash
# Abrir crontab
crontab -e

# Agregar esta línea (ejecuta todos los días a las 10:00 AM)
0 10 * * * cd /mnt/c/Users/usuario/super-comparador && python update_history.py >> update_history.log 2>&1
```

---

## Funcionamiento del Script

El script `update_history.py`:

1. **Descarga** el CSV desde GitHub (90 MB)
2. **Detecta** la última fecha en la base de datos
3. **Importa solo registros nuevos** (incremental)
4. **Actualiza** los precios de hoy desde la tabla `products`
5. **Registra** el proceso en `scrape_logs`

**Tiempo de ejecución**: ~30-60 segundos (solo descarga) + ~3 segundos (procesamiento)

---

## Verificación

Para verificar que funciona correctamente:

```cmd
cd C:\Users\usuario\super-comparador
python check_history.py
```

Esto mostrará:
- Total de registros en `price_history`
- Rango de fechas
- Productos con EAN
