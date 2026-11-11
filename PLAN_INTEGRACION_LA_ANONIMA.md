# Plan de Integración de La Anónima - SuperComparador Dev

## 1. Alcance y Requisitos Definidos
1. **Descarga de Catálogo Completo:** Al igual que Carrefour y Jumbo, el scraper descarga todo el catálogo propio de La Anónima e indexa todos los artículos en la tabla `products` (`supermarket = 'La Anónima'`).
2. **Cruce Multitienda Ampliado con TODAS las Tiendas:** A diferencia del alcance inicial, el cruce se realiza contra el **universo completo de supermercados del sistema**:
   - **La Gallega:** 10.374 EANs (pivote base para `competitor_prices` y `price_history`).
   - **COTO Digital:** 16.061 EANs.
   - **Carrefour:** 28.934 EANs.
   - **Jumbo:** 59.117 EANs.
   - **La Reina:** 8.240 EANs.
   - **Total universo EANs únicos globales:** **84.576 EANs**.
3. **Optimización de Tiempos y Rendimiento:**
   - Descarga de las 91 subcategorías de góndola: **~30 segundos** con 30 workers concurrentes.
   - Resolución de GTIN/EAN mediante fichas PDP en paralelo: **~35 req/segundo** (~2.5 a 3 minutos).
   - Escaneo puntual para EANs faltantes prioritarios vía `/buscar/{ean}`.

---

## 2. Arquitectura de Cuatro Fases

### Fase 1: Descarga Masiva del Catálogo Completo (91 Subcategorías)
- **Estructura del Menú Supermercado:** 9 departamentos N1 (Almacén, Bebidas, Lácteos y Frescos, Congelados, Frutas y Verduras, Carnicería, Perfumería, Limpieza, Mascotas).
- **Subcategorías N3:** Exactamente **91 subcategorías de góndola**.
- **Datos extraídos por producto:**
  - `id`: `laanonima_{codigo}`
  - `sku / codigo`: `data-codigo`
  - `name`: `data-nombre`
  - `brand`: `data-marca`
  - `category`: `data-rutacategorias`
  - `price_promo`: `data-precio`
  - `price_regular`: `data-precio_anterior`
  - `image_url`: Imagen oficial CDN (`data-src` / `src`)
  - `url`: Enlace directo a ficha web PDP

### Fase 2: Resolución Concurrente de EANs/GTINs desde Fichas PDP
- **Pool de Conexiones:** `requests.Session()` + `HTTPAdapter(pool_connections=60, pool_maxsize=60)`.
- **Concurrencia:** 35 workers en `ThreadPoolExecutor`.
- **Extracción de GTIN:** Cada PDP contiene un bloque Schema.org JSON-LD estándar con `"gtin": "..."`. Efectividad del 100% en productos catalogados.

### Fase 3: Escaneo Puntual para EANs Restantes
- Para cualquier producto de La Gallega (y otras tiendas) que no haya figurado en la estructura de 91 subcategorías:
  - Consulta `https://www.laanonima.com.ar/buscar/{ean}` con cookie de Rosario (`codigoPostal=2000; Id-Sucursal-Super=186`).
  - Si `"No encontramos resultados para"` no está presente, extrae la tarjeta y la agrega al conjunto.

### Fase 4: Persistencia y Cruce Multitienda en Base de Datos
- **Tabla `products`:** Inserta todos los productos con `supermarket = 'La Anónima'`.
  - Prioridad de imágenes: 1° La Gallega (si coincide EAN), 2° Existente en BD, 3° CDN La Anónima.
- **Tabla `competitor_prices`:** Para cada coincidencia con La Gallega:
  - Normaliza packs y unidades con `check_pack_mismatch()`.
  - Filtra anomalías extremas (>200% o <-70%).
  - Calcula `difference_pct` respecto a La Gallega.
  - Inserta precios promocionales y regulares.
- **Tabla `price_history`:** Inserta el registro histórico del día con `supermarket_id = 5` y fecha actual `YYYYMMDD`.

---

## 3. Integración en el Ecosistema del Proyecto
- **`db.py`**:
  - `SUPERMARKET_CODES['La Anónima'] = 5`
  - `SUPERMARKET_NAMES[5] = 'La Anónima'`
  - Adaptación de comparador, historial de precios, auditoría, canasta y estadísticas.
- **`server.py`**:
  - Endpoints `/api/scraper/laanonima/status` y `/api/scraper/laanonima/start`.
  - Filtros y ordenamiento (`competitor=laanonima`, `sort=diff_laanonima`).
  - Columna de La Anónima en la respuesta del comparador y cálculo de opción ganadora.
- **`static/index.html`**:
  - Logo oficial `static/logos/laanonima.svg` y `static/logos/laanonima.png`.
  - Pestaña de catálogo propio de La Anónima en el header con badge dinámico de conteo.
  - Columna de La Anónima en la tabla comparadora multitienda (11 columnas).
  - Selector de filtro por competidor y ordenamiento por mejor precio en La Anónima.
  - Destacado visual cuando La Anónima resulta la opción más barata.
- **`daily_update.py`**:
  - Paso automático `[7/8]` a las 10:30 hs.
- **Scripts de Automatización:**
  - `scraper_laanonima.bat`
  - `SETUP_LAANONIMA_SCHEDULE.md`
