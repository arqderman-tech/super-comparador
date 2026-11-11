# 🛒 SuperComparador Multitienda

Sistema integral de **comparación de precios en supermercados argentinos**, relevamiento automatizado mediante scrapers, auditoría de ofertas contra series históricas de precios y **escáner móvil de códigos de barra en góndola**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)
![TailwindCSS](https://img.shields.io/badge/TailwindCSS-CDN-38B2AC)
![SQLite](https://img.shields.io/badge/SQLite-FTS5-003B57)
![GitHub Ready](https://img.shields.io/badge/GitHub-Size%20Optimized%20(<50MB)-brightgreen)

---

## 🌟 Características Principales

1. **📱 Escáner de Código de Barras en Góndola (Móvil & Cámara)**
   - Diseñado específicamente para usarse desde el navegador del celular en la góndola del supermercado.
   - Visor de cámara en vivo con retícula láser de escaneo, sonido de confirmación (*beep*) y vibración háptica.
   - Controles de cámara: switch de cámara trasera/frontal y botón de linterna.
   - **Modo Foto Directa (`capture="environment"`):** Permite escanear al instante incluso navegando por red local (`http://IP:8000`) sin restricciones de certificados SSL.
   - Búsqueda manual por EAN con botones rápidos de prueba de productos reales.

2. **⚖️ Comparador Multitienda en Tiempo Real**
   - Compara precios idénticos y equivalentes entre **La Gallega, COTO, Carrefour, La Reina, Jumbo y La Anónima**.
   - Identifica el **Mejor Precio** y calcula el **Ahorro Máximo** en pesos y porcentaje.
   - Desglose de promociones complejas por unidad (*2x1*, *2da al 70%*, *3x2*, *4x3*, *descuento directo*).

3. **📈 Auditoría y Evolución Histórica de 7 Meses**
   - Gráfico interactivo diario de evolución de precios (febrero a hoy).
   - Detección algorítmica de **Oferta Real Verificada** vs **Oferta Inflada / Falsa Oferta** contrastando contra precios base previos.
   - Métricas de inflación acumulada por producto, precio mínimo, máximo y promedio histórico.

4. **🧺 Canasta de Compras Multitienda**
   - Añadí productos a tu carrito y compará el costo total consolidado entre distintas cadenas para saber dónde conviene hacer la compra completa.

---

## 🚀 Inicio Rápido (Out of the Box)

El repositorio incluye una base de datos ligera **`database.db` (~15 MB)** pre-cargada con **más de 800 productos reales**, precios multitienda y series históricas completas. El proyecto funciona inmediatamente sin necesidad de scrapear primero.

### 1. Clonar e Instalar Dependencias

```bash
git clone https://github.com/TU_USUARIO/super-comparador.git
cd super-comparador

# Instalar dependencias
pip install -r requirements.txt
```

### 2. Ejecutar la Aplicación

**En Windows:**
Hacé doble clic en [`run_app.bat`](run_app.bat) o ejecutá:

```powershell
python -X utf8 server.py
```

Abrí tu navegador en:
- **En tu computadora:** [http://localhost:8000](http://localhost:8000)
- **Desde tu celular en la misma red Wi-Fi:** `http://<IP-DE-TU-PC>:8000` *(por ejemplo: `http://192.168.1.50:8000`)*

---

## 📦 Restricciones de Tamaño de GitHub & Gestión de Datos

GitHub establece:
- **Límite estricto:** 100 MB por archivo (rechaza el commit).
- **Advertencia:** 50 MB por archivo.

La base de datos de producción completa con cientos de miles de productos supera los 350 MB. Para garantizar que este repositorio se mantenga **liviano, clone rápido y cumpla al 100% las políticas de GitHub**, se implementó la siguiente arquitectura:

- **`database.db` (~15.9 MB):** Base de datos seed incluida en el repo con datos representativos de todas las categorías y cadenas, lista para usar.
- **`.gitignore`:** Ignora archivos masivos generados por los scrapers (`output_carrefour/`, `data/`, `lagallega_products.json`, dumps temporales).
- **`export_seed.py`:** Herramienta para regenerar o actualizar la base ligera `database.db` a partir de un scrape local completo antes de subir cambios a GitHub:
  ```bash
  python export_seed.py
  ```
- **`init_db.py`:** Permite inicializar una base de datos completamente vacía con la estructura y tablas FTS5 si se desea arrancar de cero:
  ```bash
  python init_db.py
  ```

---

## 🕷️ Scrapers de Supermercados

El proyecto cuenta con scrapers especializados para cada supermercado:

| Supermercado | Script | Método |
| :--- | :--- | :--- |
| **La Gallega** | `scraper.py` | API / HTTP Directo |
| **COTO** | `scraper_coto.py`, `coto_base.py` | Playwright / HTML Parsing |
| **Carrefour** | `carrefour_scraper.py`, `import_carrefour.py` | API VTEX / CSV Pipeline |
| **Jumbo** | `scraper_jumbo.py` | API VTEX / GraphQL |
| **La Reina** | `scraper_reina.py` | Web Scraping / Catálogo |
| **La Anónima** | `scraper_laanonima.py` | API VTEX |
| **Historial** | `update_history.py` | Descarga de serie histórica diaria |

### Actualización Automática Diaria
Podés ejecutar todas las actualizaciones consolidadas con:
```bat
daily_update.bat
```

---

## 🛠️ Tecnologías Utilizadas

- **Backend:** FastAPI, Uvicorn, SQLite3 con FTS5 (Full Text Search).
- **Frontend:** Vanilla JavaScript modular, Tailwind CSS (vía CDN), FontAwesome 6, Chart.js, HTML5-QRCode.
- **Scraping:** Requests, Playwright, BeautifulSoup4, Curl-Cffi, Pandas.

---

## 📄 Licencia

Este proyecto se distribuye bajo la licencia MIT. Consultá el archivo para más información.
