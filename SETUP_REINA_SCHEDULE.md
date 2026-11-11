# Configuración de Actualización Automática - Precios de La Reina

## Importante

**Solo se utilizan precios reales de La Reina Online.**
- No hay precios simulados de ningún supermercado.
- Los precios de La Reina se obtienen mediante scraping real filtrando por EAN de La Gallega.
- Cobertura actual: ~4,464 productos (43% del catálogo de La Gallega).

## Ejecución Manual

Para actualizar los precios de La Reina manualmente:

```cmd
cd C:\Users\usuario\proyectos\super-comparador-dev
python scraper_reina.py
```

O haz doble clic en `scraper_reina.bat`

## Automatización con Windows Task Scheduler

### Pasos para configurar:

1. **Abrir Programador de Tareas de Windows**
   - Presiona `Win + R`, escribe `taskschd.msc` y presiona Enter

2. **Crear una nueva tarea**
   - Click derecho en "Biblioteca del Programador de Tareas"
   - Selecciona "Crear tarea básica..."

3. **Configurar la tarea**
   - **Nombre**: `SuperComparador Dev - Actualizar Precios La Reina`
   - **Descripción**: Scrapea precios de La Reina Online filtrando por EAN de La Gallega
   - Click en "Siguiente"

4. **Configurar el disparador (Trigger)**
   - Selecciona "Diariamente"
   - Configura la hora (ej: 10:30 AM - 30 minutos después del scraper de COTO)
   - Click en "Siguiente"

5. **Configurar la acción**
   - Selecciona "Iniciar un programa"
   - **Programa o script**: `C:\Users\usuario\proyectos\super-comparador-dev\scraper_reina.bat`
   - **Iniciar en**: `C:\Users\usuario\proyectos\super-comparador-dev`
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

También puedes actualizar La Reina desde la API:

```bash
POST http://localhost:8000/api/scraper/reina/start
```

---

## Funcionamiento del Scraper

El script `scraper_reina.py`:

1. **Obtiene** los EAN de La Gallega desde la base de datos (~10,421 productos)
2. **Descubre** 227 categorías de La Reina desde el menú del sitio
3. **Scrapea** todas las páginas de cada categoría en paralelo (3 workers)
4. **Filtra** solo productos que coinciden por EAN
5. **Actualiza** la tabla `competitor_prices` con precios reales
6. **Calcula** el diferencial de precio vs La Gallega

**Resultados típicos:**
- Cobertura: ~4,464 productos (43% del catálogo de La Gallega)
- Tiempo de ejecución: ~10 minutos
- 227 categorías con paginación completa

---

## Verificación

Para verificar que funciona correctamente:

```cmd
cd C:\Users\usuario\proyectos\super-comparador-dev
python verify_reina.py
```

Esto mostrará:
- Total de productos por supermercado (solo La Reina con precios reales)
- Muestra de comparaciones La Gallega vs La Reina

---

## Secuencia Recomendada de Actualizaciones Diarias

Para mantener el sistema actualizado automáticamente, configura los siguientes horarios:

1. **09:00 AM** - GALLEBOT (GitHub Actions) - Actualiza CSV histórico
2. **10:00 AM** - SuperComparador - Sincroniza histórico desde GitHub (`update_history.py`)
3. **10:30 AM** - SuperComparador - Scrapea COTO Digital (`scraper_coto.py`)
4. **11:00 AM** - SuperComparador - Scrapea La Reina Online (`scraper_reina.py`)
5. **11:30 AM** - SuperComparador - Scrapea Carrefour (`carrefour_scraper.py`)
6. **12:00 PM** - SuperComparador - Scrapea La Gallega (`scraper.py`)
