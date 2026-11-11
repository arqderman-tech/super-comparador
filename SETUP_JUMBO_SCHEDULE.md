# Configuración de Actualización Automática - Precios de Jumbo Argentina

## Importante

**Solo se utilizan precios reales de Jumbo Argentina (VTEX IO).**
- Sin precios simulados ni proxies lentos.
- Los precios de Jumbo se obtienen mediante la API REST nativa de VTEX IO de `www.jumbo.com.ar`.
- Descarga el catálogo completo de Jumbo y cruza simultáneamente por código de barras (EAN) contra La Gallega.
- Normaliza precios de listas (`ListPrice`), promociones (`4x2`, `3x2`, `2do al 70%`, etc.) y ajustes de packs.

## Ejecución Manual

Para actualizar los precios de Jumbo manualmente:

```cmd
cd "C:\Users\usuario\OneDrive - Depaoli Trosce\Aplicaciones\APLICACIONES FUNCIONALES\super-comparador-dev"
python scraper_jumbo.py
```

O haz doble clic en `scraper_jumbo.bat`.

También puedes iniciarlo desde la interfaz web en cualquier momento mediante la API:
`POST http://localhost:8000/api/scraper/jumbo/start`

## Integración Diaria Automática

Jumbo ya está integrado como el paso **[6/7]** en `daily_update.py` y se ejecuta automáticamente todos los días a las **10:30 hs** junto a La Gallega, COTO, Carrefour y La Reina.
