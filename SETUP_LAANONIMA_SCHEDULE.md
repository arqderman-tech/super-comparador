# Configuración de Actualización Automática - Precios de La Anónima Online

## Importante

**Solo se utilizan precios reales de La Anónima Online (Sucursal Rosario / CP 2000 / Sucursal 186).**
- Sin precios simulados ni proxies lentos.
- La extracción opera en dos fases de alto rendimiento:
  1. **Fase 1 (Catálogo Propio):** Descarga paralela de las 91 subcategorías de góndola de Supermercado mediante `ThreadPoolExecutor` con Keep-Alive (`codigoPostal=2000; Id-Sucursal-Super=186`).
  2. **Fase 2 (Cruce Multitienda Ampliado):** Resolución de códigos de barras EAN/GTIN mediante extracción paralela de esquemas schema.org JSON-LD de las fichas de producto (PDP) a más de 35 req/s, cruzando simultáneamente con La Gallega (para la tabla `competitor_prices` y `price_history`) y con el universo global de todas las tiendas (COTO, Carrefour, Jumbo y La Reina).
  3. **Fase 3 (Escaneo Puntual EAN):** Consulta directa de EANs prioritarios faltantes mediante el buscador `/buscar/{ean}`.
- Normaliza precios de listas, promociones de góndola, descuentos porcentuales y ajustes de packs (`check_pack_mismatch`).

---

## Ejecución Manual

Para actualizar los precios de La Anónima manualmente:

```cmd
cd "C:\Users\usuario\OneDrive - Depaoli Trosce\Aplicaciones\APLICACIONES FUNCIONALES\super-comparador-dev"
python scraper_laanonima.py
```

O haz doble clic en el archivo por lotes:
`scraper_laanonima.bat`

Para una prueba rápida de verificación:
```cmd
python scraper_laanonima.py --quick
```

También puedes iniciarlo desde la interfaz web en cualquier momento mediante la API REST:
- **Iniciar:** `POST http://localhost:8000/api/scraper/laanonima/start`
- **Estado:** `GET http://localhost:8000/api/scraper/laanonima/status`

---

## Integración Diaria Automática

La Anónima ya está integrada como el paso **[7/8]** en el orquestador `daily_update.py` y se ejecuta automáticamente todos los días a las **10:30 hs** junto a La Gallega, COTO, Carrefour, La Reina y Jumbo.
