# Configuración de Actualización Automática - Precios de COTO

## Importante

**Solo se utilizan precios reales de COTO Digital.**
- No hay precios simulados de ningún supermercado.
- Los precios de COTO se obtienen mediante scraping real filtrando por EAN de La Gallega.
- Cobertura actual: ~2,500 productos (24% del catálogo de La Gallega).

## Ejecución Manual

Para actualizar los precios de COTO manualmente:

```cmd
cd C:\Users\usuario\super-comparador
python scraper_coto.py
```

O haz doble clic en `scraper_coto.bat`

## Automatización con Windows Task Scheduler

### Pasos para configurar:

1. **Abrir Programador de Tareas de Windows**
   - Presiona `Win + R`, escribe `taskschd.msc` y presiona Enter

2. **Crear una nueva tarea**
   - Click derecho en "Biblioteca del Programador de Tareas"
   - Selecciona "Crear tarea básica..."

3. **Configurar la tarea**
   - **Nombre**: `SuperComparador - Actualizar Precios COTO`
   - **Descripción**: Scrapea precios de COTO Digital filtrando por EAN de La Gallega
   - Click en "Siguiente"

4. **Configurar el disparador (Trigger)**
   - Selecciona "Diariamente"
   - Configura la hora (ej: 11:00 AM)
   - Click en "Siguiente"

5. **Configurar la acción**
   - Selecciona "Iniciar un programa"
   - **Programa o script**: `C:\Users\usuario\super-comparador\scraper_coto.bat`
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

## API Endpoint

También puedes actualizar COTO desde la API:

```bash
POST http://localhost:8000/api/scraper/coto/start
```

---

## Funcionamiento del Scraper

El script `scraper_coto.py`:

1. **Obtiene** los EAN de La Gallega desde la base de datos (~10,000 productos)
2. **Scrapea** 38 categorías de COTO Digital en paralelo
3. **Filtra** solo productos que coinciden por EAN
4. **Actualiza** la tabla `competitor_prices` con precios reales
5. **Calcula** el diferencial de precio vs La Gallega

**Resultados típicos:**
- Cobertura: ~2,500 productos (24% del catálogo de La Gallega)
- Tiempo de ejecución: ~8-10 segundos

---

## Verificación

Para verificar que funciona correctamente:

```cmd
cd C:\Users\usuario\super-comparador
python verify_coto.py
```

Esto mostrará:
- Total de productos por supermercado (solo COTO con precios reales)
- Muestra de comparaciones La Gallega vs COTO
