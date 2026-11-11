# 🚀 Cómo subir este repositorio a GitHub - Guía Paso a Paso

Esta guía te muestra cómo subir esta carpeta (`super-comparador-github`) a un repositorio nuevo en GitHub, respetando las restricciones de tamaño de archivo (<50MB).

---

## Paso 1: Crear el repositorio en GitHub

1. Entrá a [https://github.com](https://github.com) e iniciá sesión con tu cuenta.
2. Hacé clic en el botón **"+"** (arriba a la derecha) y seleccioná **"New repository"**.
3. Completá los campos:
   - **Repository name:** `super-comparador` *(o el nombre que prefieras)*.
   - **Description:** `Sistema multitienda de comparación de precios en supermercados con escáner de códigos de barra.`
   - **Visibilidad:** `Public` o `Private` (a tu elección).
   - ⚠️ **NO marques ninguna casilla** (NO marques *Add a README file*, NO marques *Add .gitignore*, NO marques *Choose a license* porque ya están todos incluidos y configurados en esta carpeta).
4. Hacé clic en el botón verde **"Create repository"**.

---

## Paso 2: Abrir la terminal en esta carpeta

Abrí una ventana de PowerShell o Terminal en:

```powershell
cd "C:\Users\usuario\OneDrive - Depaoli Trosce\Aplicaciones\APLICACIONES FUNCIONALES\super-comparador-github"
```

---

## Paso 3: Inicializar Git y verificar archivos

Ejecutá los siguientes comandos en orden:

```bash
# 1. Inicializar el repositorio Git local
git init

# 2. Agregar todos los archivos preparados
git add .

# 3. Verificar qué archivos se van a subir
git status
```

> **Verificación de tamaño:**
> Todo está configurado para que ningún archivo supere los 50 MB (`database.db` pesa ~15.9 MB y los archivos masivos de scrapers están excluidos en `.gitignore`).

---

## Paso 4: Crear el primer commit

```bash
git commit -m "feat: SuperComparador multitienda con escáner de código de barras y base seed funcional"
```

---

## Paso 5: Conectar y subir a GitHub

Reemplazá `TU_USUARIO` por tu nombre de usuario de GitHub:

```bash
# Cambiar la rama principal a main
git branch -M main

# Conectar con tu repositorio remoto
git remote add origin https://github.com/TU_USUARIO/super-comparador.git

# Subir el código a GitHub
git push -u origin main
```

*(Si es la primera vez que usás Git en esta máquina, te pedirá autorizar tu cuenta de GitHub en el navegador o ingresar un Personal Access Token).*

---

## 🔄 Futuras actualizaciones

Cada vez que hagas mejoras o agregues cambios:

```bash
git add .
git commit -m "Descripción de los cambios"
git push
```

> 💡 **Nota sobre scrapers y base de datos:**
> Si corrés scrapers masivos que hagan crecer `database.db` a cientos de megabytes, antes de commitear ejecutá:
> ```powershell
> python export_seed.py
> ```
> Esto recreará la base ligera (~15 MB) con los datos más recientes para mantener el repositorio ágil y dentro de los límites de GitHub.
