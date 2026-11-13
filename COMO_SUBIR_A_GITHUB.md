# 🚀 Cómo subir este repositorio a GitHub - Guía Simple y Rápida

Todo el proyecto ya está preparado, configurado y listo para ser **100% autónomo en GitHub**. No necesitás instalar herramientas complejas ni configurar tareas programadas en tu computadora.

---

## Paso 1: Crear el repositorio en tu cuenta de GitHub

1. Entrá a tu cuenta: [https://github.com/new](https://github.com/new)
2. Completá:
   - **Repository name:** `super-comparador`
   - **Description:** `Sistema autónomo de comparación de precios en supermercados con escáner móvil de código de barras.`
   - **Visibilidad:** `Public` (recomendado para usar GitHub Pages gratis) o `Private`.
   - ⚠️ **NO marques ninguna casilla** (deja desmarcados *README*, *.gitignore* y *License* porque ya están creados y optimizados aquí).
3. Hacé clic en el botón verde **"Create repository"**.

---

## Paso 2: Subir el código desde esta PC

Abrí la consola de PowerShell y ejecutá:

```powershell
cd "C:\Users\marti\OneDrive - Depaoli Trosce\Aplicaciones\APLICACIONES FUNCIONALES\super-comparador-github"

# El origen ya quedó pre-vinculado, sólo necesitás empujar a main:
git push -u origin main
```

*(Si Git te pide autenticación en el navegador, autorizalo con tu cuenta `arqderman-tech`).*

---

## Paso 3: Activar la Web Autónoma (GitHub Pages)

Para que puedas abrir el comparador y el escáner de código de barras directamente desde tu celular en cualquier supermercado:

1. En tu repositorio de GitHub, andá a la pestaña **Settings** (Configuración).
2. En el menú lateral izquierdo, hacé clic en **Pages**.
3. En la sección **Build and deployment > Source**, seleccioná **GitHub Actions**.
4. ¡Listo! Tu sitio web estará activo en:
   👉 **`https://arqderman-tech.github.io/super-comparador/`**

---

## 🤖 ¿Cómo funciona la automatización diaria?

- **No necesitás hacer nada más:** Todos los días a las **05:00 AM** (hora argentina), el flujo de [GitHub Actions](.github/workflows/daily-scraper.yml) se ejecutará en los servidores de GitHub en la nube.
- Relevará los precios de **La Gallega, COTO, Carrefour, Jumbo, La Reina y La Anónima**, actualizará los históricos, regenerará la base optimizada y actualizará la web automáticamente.
- También podés forzar una actualización manual cuando quieras desde la pestaña **Actions > SuperComparador Daily Scraper & Cloud Sync > Run workflow**.
