#!/usr/bin/env python3
"""
export_variaciones.py
---------------------------------------------------------------------
Genera static/variaciones.html + static/data/variaciones.json con los
productos que MAS cambiaron de precio en cada periodo (hoy, 1 mes,
3/6/12 meses), por supermercado y por categoria.

Fuente: price_history(ean, fecha, precio, supermarket_id) + products
para nombre/categoria/marca.
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import db  # noqa: F401  (usa DB_PATH)

DB_PATH = os.path.join(BASE_DIR, "database.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
OUT_JSON = os.path.join(DATA_DIR, "variaciones.json")
OUT_HTML = os.path.join(STATIC_DIR, "variaciones.html")

SID_TO_SUPER = {0: "La Gallega", 1: "Coto", 2: "Carrefour",
                3: "La Reina", 4: "Jumbo", 5: "La Anónima"}

# (clave, dias hacia atras). "hoy" se maneja aparte (ultimas 2 fechas).
PERIODOS = [("mes", 30), ("tres_meses", 90), ("seis_meses", 180),
            ("doce_meses", 365)]
TOP_N = 20          # por super, cada sentido
TOP_CAT = 5         # por categoria, cada sentido
MIN_DELTA_PCT = 0.5 # ignora cambios < 0.5%


def f2s(fecha_int):
    s = str(fecha_int)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()

    # ---------- catalogo ean -> info de producto ----------
    print("Cargando catalogo de productos...")
    cur.execute("""SELECT ean, supermarket, name, category, brand,
                          price_regular, price_promo, is_promo
                   FROM products""")
    # info[(ean, super)] y fallback por ean
    info_sup, info_ean = {}, {}
    for ean, sup, name, cat, brand, pr, pp, promo in cur.fetchall():
        if not ean:
            continue
        rec = {"n": name or "?", "c": cat or "Sin categoría",
               "b": brand or "", "pr": pr or 0, "pp": pp or 0,
               "promo": bool(promo)}
        info_sup[(ean, sup)] = rec
        info_ean.setdefault(ean, rec)
    print(f"  {len(info_ean)} EANs con nombre")

    # ---------- fechas disponibles ----------
    cur.execute("SELECT MIN(fecha), MAX(fecha), COUNT(DISTINCT fecha) FROM price_history")
    f_min, f_max, n_fechas = cur.fetchone()
    print(f"Historial: {f_min} -> {f_max} ({n_fechas} fechas)")

    # ---------- ultimas 2 fechas para "hoy" ----------
    cur.execute("SELECT DISTINCT fecha FROM price_history ORDER BY fecha DESC LIMIT 2")
    fechas_desc = [r[0] for r in cur.fetchall()]
    fecha_hoy, fecha_ayer = fechas_desc[0], (fechas_desc[1] if len(fechas_desc) > 1 else None)

    inicio_12m = int((datetime.strptime(str(f_max), "%Y%m%d")
                      - timedelta(days=365)).strftime("%Y%m%d"))

    # ---------- streaming de todo el historial relevante ----------
    print("Procesando price_history...")
    cur.execute("""SELECT ean, supermarket_id, fecha, precio
                   FROM price_history
                   WHERE fecha >= ? AND ean != ''
                   ORDER BY ean, supermarket_id, fecha""",
                (min(inicio_12m, fecha_ayer or inicio_12m),))

    # series[(ean,sid)] = [(fecha,precio), ...] ordenado
    series = {}
    last_key = None
    buf = []
    for ean, sid, fecha, precio in cur:
        key = (ean, sid)
        if key != last_key:
            if buf:
                series[last_key] = buf
            buf = []
            last_key = key
        buf.append((fecha, float(precio)))
    if buf:
        series[last_key] = buf
    print(f"  {len(series)} series ean+super")

    # ---------- calculo de variaciones por periodo ----------
    hoy_dia = datetime.strptime(str(fecha_hoy), "%Y%m%d").date()
    bounds = {"hoy": (fecha_ayer, fecha_hoy)}
    for clave, dias in PERIODOS:
        ini = int((hoy_dia - timedelta(days=dias)).strftime("%Y%m%d"))
        bounds[clave] = (ini, fecha_hoy)

    datos = {k: {} for k in bounds}

    for (ean, sid), obs in series.items():
        if len(obs) < 2:
            continue
        sup = SID_TO_SUPER.get(sid)
        if not sup:
            continue
        for per, (d0, d1) in bounds.items():
            if per == "hoy" and d0 is None:
                continue
            seg = [(f, p) for f, p in obs if d0 <= f <= d1]
            if len(seg) < 2:
                continue
            p0, p1 = seg[0][1], seg[-1][1]
            if not p0:
                continue
            pct = (p1 - p0) / p0 * 100
            if abs(pct) < MIN_DELTA_PCT:
                continue
            rec = {"p0": round(p0, 2), "p1": round(p1, 2),
                   "pct": round(pct, 1), "n": len(seg)}
            # info de producto: prefiere el del mismo super
            inf = info_sup.get((ean, sup)) or info_ean.get(ean)
            if inf:
                rec.update({"name": inf["n"], "cat": inf["c"],
                            "brand": inf["b"], "promo": inf["promo"]})
            else:
                rec.update({"name": f"EAN {ean}", "cat": "Sin categoría",
                            "brand": "", "promo": False})
            datos[per].setdefault(sup, []).append(rec)

    # ---------- ranking + categorias ----------
    salida = {"generado": datetime.now().isoformat(timespec="seconds"),
              "fecha_ref": f2s(f_max),
              "periodos": {
                  "hoy": {"label": "Hoy", "desde": f2s(fecha_ayer), "hasta": f2s(fecha_hoy)},
                  **{k: {"label": {"mes": "Último mes",
                                   "tres_meses": "Últimos 3 meses",
                                   "seis_meses": "Últimos 6 meses",
                                   "doce_meses": "Último año"}[k],
                      "desde": f2s(int((hoy_dia - timedelta(days=d)).strftime("%Y%m%d"))),
                      "hasta": f2s(fecha_hoy)} for k, d in PERIODOS}},
              "supers": list(SID_TO_SUPER.values()),
              "datos": {}}

    for per, por_super in datos.items():
        bloque = {}
        for sup, items in por_super.items():
            suba = sorted([i for i in items if i["pct"] > 0],
                          key=lambda x: -x["pct"])[:TOP_N]
            baja = sorted([i for i in items if i["pct"] < 0],
                          key=lambda x: x["pct"])[:TOP_N]
            # por categoria
            cats = {}
            for it in items:
                cats.setdefault(it["cat"], []).append(it)
            cats_out = {}
            for cat, its in cats.items():
                if len(its) < 3:
                    continue
                cs = sorted([i for i in its if i["pct"] > 0],
                            key=lambda x: -x["pct"])[:TOP_CAT]
                cb = sorted([i for i in its if i["pct"] < 0],
                            key=lambda x: x["pct"])[:TOP_CAT]
                if cs or cb:
                    cats_out[cat] = {"suba": cs, "baja": cb}
            bloque[sup] = {"suba": suba, "baja": baja,
                           "cats": dict(sorted(cats_out.items(),
                                               key=lambda kv: -len(kv[1]["suba"] + kv[1]["baja"]))[:40])}
        salida["datos"][per] = bloque

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
    tam = os.path.getsize(OUT_JSON) / 1024
    print(f"[OK] variaciones.json -> {OUT_JSON} ({tam:.0f} KB)")

    # ---------- HTML ----------
    html = HTML_TEMPLATE.replace("__GENERADO__", salida["generado"])
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] variaciones.html -> {OUT_HTML}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SuperComparador · Variaciones de precio</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
  body { font-family: 'Segoe UI', system-ui, sans-serif; }
  .pos { color:#16a34a; } .neg { color:#dc2626; }
  .card { background:#fff; border-radius:1rem; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .pill { transition:all .15s; }
  .pill.on { background:#1d4ed8; color:#fff; }
  table.mv td, table.mv th { padding:.45rem .6rem; font-size:.85rem; }
  .bar { height:6px; border-radius:3px; }
</style>
</head>
<body class="bg-slate-100 min-h-screen">
<header class="bg-white shadow-sm sticky top-0 z-10">
  <div class="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3 flex-wrap">
    <a href="index.html" class="text-slate-500 hover:text-slate-800"><i class="fa-solid fa-arrow-left"></i></a>
    <h1 class="text-xl font-bold text-slate-800"><i class="fa-solid fa-arrow-trend-up text-blue-600"></i> Variaciones de precio</h1>
    <span id="fechaRef" class="text-xs text-slate-400 ml-auto"></span>
  </div>
</header>
<main class="max-w-6xl mx-auto px-4 py-5">
  <div class="flex flex-wrap gap-2 mb-4" id="periodos"></div>
  <div class="flex flex-wrap gap-2 mb-4" id="supers"></div>
  <div class="flex gap-2 mb-5">
    <button id="btnGeneral" class="pill on px-4 py-1.5 rounded-full text-sm bg-white border">General</button>
    <button id="btnCats" class="pill px-4 py-1.5 rounded-full text-sm bg-white border">Por categoría</button>
  </div>
  <div id="contenido" class="grid gap-4"></div>
</main>
<script>
let DATA=null, per="hoy", sup="Todos", modo="general";
const PER_LABEL={hoy:"Hoy",mes:"Último mes",tres_meses:"3 meses",seis_meses:"6 meses",doce_meses:"12 meses"};

async function init(){
  DATA=await (await fetch("data/variaciones.json")).json();
  document.getElementById("fechaRef").textContent=
    "ref. "+DATA.fecha_ref+" · generado "+DATA.generado.slice(0,16).replace("T"," ");
  const pe=document.getElementById("periodos");
  Object.keys(DATA.periodos).forEach(k=>{
    const b=document.createElement("button");
    b.className="pill px-4 py-1.5 rounded-full text-sm bg-white border"+(k===per?" on":"");
    b.textContent=PER_LABEL[k]||k;
    b.onclick=()=>{per=k;[...pe.children].forEach(c=>c.classList.remove("on"));b.classList.add("on");render();};
    pe.appendChild(b);
  });
  const se=document.getElementById("supers");
  ["Todos",...DATA.supers].forEach(s=>{
    const b=document.createElement("button");
    b.className="pill px-3 py-1.5 rounded-full text-xs bg-white border"+(s===sup?" on":"");
    b.textContent=s;
    b.onclick=()=>{sup=s;[...se.children].forEach(c=>c.classList.remove("on"));b.classList.add("on");render();};
    se.appendChild(b);
  });
  document.getElementById("btnGeneral").onclick=e=>{modo="general";e.target.classList.add("on");document.getElementById("btnCats").classList.remove("on");render();};
  document.getElementById("btnCats").onclick=e=>{modo="cats";e.target.classList.add("on");document.getElementById("btnGeneral").classList.remove("on");render();};
  render();
}

function fmt$(n){return "$"+Number(n).toLocaleString("es-AR",{maximumFractionDigits:0});}
function fila(it){
  const cls=it.pct>0?"pos":"neg", arr=it.pct>0?"▲":"▼";
  return `<tr class="border-t">
    <td class="font-medium">${it.name}</td>
    <td class="text-slate-500 text-xs">${it.cat}${it.brand?" · "+it.brand:""}</td>
    <td class="text-right whitespace-nowrap">${fmt$(it.p0)} → <b>${fmt$(it.p1)}</b></td>
    <td class="text-right font-bold ${cls} whitespace-nowrap">${arr} ${Math.abs(it.pct)}%</td>
  </tr>`;
}
function tabla(items,titulo,icono,cls){
  if(!items||!items.length) return "";
  return `<div class="card overflow-hidden">
    <div class="px-4 py-2 bg-slate-50 font-semibold text-sm flex items-center gap-2">
      <i class="fa-solid ${icono} ${cls}"></i> ${titulo}
      <span class="ml-auto text-xs text-slate-400">${items.length} productos</span></div>
    <table class="mv w-full"><tbody>${items.map(fila).join("")}</tbody></table></div>`;
}

function render(){
  const cont=document.getElementById("contenido");
  const supers = sup==="Todos" ? DATA.supers : [sup];
  let html="";
  if(modo==="general"){
    supers.forEach(s=>{
      const d=DATA.datos[per]?.[s]; if(!d) return;
      html+=`<h2 class="text-lg font-bold text-slate-700 mt-2">${s}</h2>`;
      html+=`<div class="grid md:grid-cols-2 gap-4">`;
      html+=tabla(d.suba,"Más SUBIERON","fa-arrow-trend-up","pos");
      html+=tabla(d.baja,"Más BAJARON","fa-arrow-trend-down","neg");
      html+=`</div>`;
    });
  }else{
    // por categoria: junta todas las categorias del/los super/s
    const cats={};
    supers.forEach(s=>{
      const d=DATA.datos[per]?.[s]; if(!d?.cats) return;
      Object.entries(d.cats).forEach(([c,v])=>{
        cats[c]=cats[c]||{suba:[],baja:[]};
        cats[c].suba.push(...(v.suba||[]).map(x=>({...x,_s:s})));
        cats[c].baja.push(...(v.baja||[]).map(x=>({...x,_s:s})));
      });
    });
    const ordenadas=Object.entries(cats).sort((a,b)=>(b[1].suba.length+b[1].baja.length)-(a[1].suba.length+a[1].baja.length));
    ordenadas.forEach(([c,v])=>{
      v.suba.sort((a,b)=>b.pct-a.pct); v.baja.sort((a,b)=>a.pct-b.pct);
      const filaCat=it=>`<tr class="border-t"><td class="font-medium">${it.name}</td>
        <td class="text-xs text-slate-400">${it._s}</td>
        <td class="text-right whitespace-nowrap">${fmt$(it.p0)} → <b>${fmt$(it.p1)}</b></td>
        <td class="text-right font-bold ${it.pct>0?"pos":"neg"} whitespace-nowrap">${it.pct>0?"▲":"▼"} ${Math.abs(it.pct)}%</td></tr>`;
      html+=`<div class="card overflow-hidden">
        <div class="px-4 py-2 bg-slate-50 font-semibold text-sm"><i class="fa-solid fa-tag text-blue-600"></i> ${c}</div>
        <div class="grid md:grid-cols-2">
          <table class="mv w-full"><tbody>${v.suba.slice(0,5).map(filaCat).join("")||'<tr><td class="p-3 text-slate-400 text-sm">sin subas</td></tr>'}</tbody></table>
          <table class="mv w-full"><tbody>${v.baja.slice(0,5).map(filaCat).join("")||'<tr><td class="p-3 text-slate-400 text-sm">sin bajas</td></tr>'}</tbody></table>
        </div></div>`;
    });
    if(!ordenadas.length) html=`<p class="text-slate-500">Sin datos para este periodo/super.</p>`;
  }
  cont.innerHTML=html||`<p class="text-slate-500">Sin datos para este periodo/super.</p>`;
}
init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
