# Memoria sobre Sergio

> Este archivo sirve para que Claude recuerde información importante sobre Sergio entre sesiones.
> Compártelo al inicio de cada conversación para que Claude lo lea.
>
> **UBICACIÓN ÚNICA Y AUTORITATIVA:** `D:\MeIA\memoria_sergio.md` (movida desde OneDrive el 18/05/2026)
> No crear copias en subcarpetas. El archivo `bono-app/memoria_sergio.md` es solo un puntero a este archivo.
> Siempre actualizar este archivo y solo este archivo.
> **Por qué se movió:** disco local más rápido y limpio, sin capas de sync de OneDrive. **NO** resolvió el truncamiento silencioso (Claude se equivocó al achacárselo a OneDrive — OneDrive no estaba corriendo). La causa real es un bug del file tool con Edits encadenados en archivos grandes (ver sección de fixes técnicos).

---

## Datos básicos
- **Nombre:** Sergio
- **Correo:** sergiollamera78@gmail.com

## Preferencias de comunicación
- Habla en español

## Preferencias de trabajo
- Va descubriendo requisitos a medida que prueba en la app — no define todo de antemano
- Prefiere ver el problema funcionando antes de validar
- Directo y al punto, sin rodeos
- Trabaja en Windows con PowerShell/CMD, Python 3.12
- Le gusta ir paso a paso y confirmar antes de avanzar
- Tiene modelos físicos reales (planillas en papel) como referencia — el digital debe replicarlos fielmente
- Ajusta visualmente: ve el resultado en pantalla y pide correcciones puntuales

## Notas adicionales
- Trabaja con planilla de bomberos CDELU 2026-2027 (lista de compradores de bono)

---

## Proyectos y tareas conocidas

### bono-app (app de gestión de bono de bomberos)
- **Ubicación:** `D:\MeIA\bono-app\` (movida desde OneDrive el 18/05/2026 para evitar truncamiento por sync)
- **Stack:** Python 3.12 + FastAPI + Jinja2 + Bootstrap 5 + SQLAlchemy 2.0 + SQLite (local) / PostgreSQL (Railway)
- **Levantar:** `py -3.12 -m uvicorn app.main:app --reload` desde la carpeta `bono-app`
- **Login por defecto:** usuario `admin`, contraseña `admin123`
- **Entidades:** Compradores, Taloneras, Boletas, Vendedores, Cobradores, Zonas
- **La app está subida a GitHub y Railway** — el entorno productivo está activo en Railway

#### Despliegue en Railway
- Servicio Bono-Contribucion, conectado a repo GitHub
- PostgreSQL en Railway (postgres-volume), variable DATABASE_URL
- Dockerfile: CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
- SIEMPRE hacer git push para que Railway tome los cambios
- **El sandbox bash NO tiene credenciales de GitHub** → el `git push` siempre debe hacerlo Sergio desde PowerShell en Windows

---

### Fixes técnicos importantes (acumulado)
- Python 3.14 incompatible con SQLAlchemy/passlib → usar siempre py -3.12
- Jinja2 cache bug → templates_config.py con cache_size=0
- bcrypt directo (sin passlib) en app/auth.py
- Starlette nuevo: TemplateResponse(request, "name.html", {ctx_sin_request})
- Pydantic v2 + Form + List[int]: usar siempre request.form().getlist("campo")
- Lock files git stale: `del .git\index.lock` y `del .git\HEAD.lock` desde CMD
- **Escritura de archivos grandes**: usar script Python en bash para evitar truncación del Write/Edit tool
- **Columnas nuevas en ORM (PostgreSQL)**: usar `deferred(Column(...))` si la columna puede no existir aún en la DB al momento del primer SELECT; evita "column does not exist"
- **Migraciones PostgreSQL**: usar `ALTER TABLE x ADD COLUMN IF NOT EXISTS ...` (no soportado en SQLite → detectar dialect con `engine.dialect.name`)
- **Jinja2 + dicts**: acceso por punto (`s.clave`) en Jinja2 lanza `UndefinedError` si la clave no existe en el dict — siempre incluir todas las claves en el dict o usar `s.get("clave", 0)`
- **_stats_bulk**: el dict de stats DEBE incluir la clave `"baja"` aunque sea 0, porque el template accede a `s.baja`
- **Sandbox bash mount stale**: a veces el mount /sessions/.../mnt/ no sincroniza con los Edit del file tool; confiar en lo que devuelve Read y verificar contenido con grep si bash da errores raros.
- **Funciones truncadas silenciosamente** (10/05/2026 y 11/05/2026): Edits encadenados en archivos >500 líneas pueden truncar el archivo sin error. `ast.parse` falla con `SyntaxError: unterminated string literal`. Read tool muestra versión cacheada que no está en disco. Detección con `python3 -c "print(open(f,'rb').read()[-300:])"`. Patch con Python directo desde bash, no con Edit.
- **Modales dentro de `<div class="tab-pane">` inactivos NO se ven**: el tab-pane inactivo tiene `display:none` y los hijos heredan eso. Los modales SIEMPRE deben ir a nivel raíz del bloque content.
- **`data-bs-toggle="collapse"` puede fallar silencioso**: fallback robusto con `onclick="bootstrap.Collapse.getOrCreateInstance(el, {toggle:false}).toggle()"` directo.

---

### Modelo de datos — resumen de relaciones clave

#### Taloneras y boletas
- Cada talonera tiene `num_series` y `offset_series`
- **PATA 0** (11/05/2026): 2 series → mult 0.6667 (2/3 de PATA 1). Cuota 1 = $10.000. Talonera "barata" agregada por situación económica.
- **PATA 1:** 3 series, offset 1501 → ej: 0001 / 1502 / 3003. Cuota 1 = $15.000. Mult 1.0.
- **PATA 2:** 6 series, offset 350 → mult 2.0.
- Función `calcular_numeros(numero_principal, num_series, offset)` en `app/routers/taloneras.py`
- Condiciones de boleta: SIN_VENDER → CAJA → VENDIDO (también BAJA, EN_COBRANZA)
- `Talonera.num_cuotas` (Integer, default 12, **deferred**) — editable en UI
- `Talonera.valor_cuota` (Float, default 0.0) — editable en UI
- `Talonera.num_digitos` — COMUN siempre 4 (0001-9999), CONTADO usa 3.
- `Talonera.multiplicador` — **Float desde 11/05/2026** (antes Integer). Fórmula: `num_series / 3.0` (antes `// 3`). PATA 0=0.6667 (2/3) / PATA 1=1.0 / PATA 2=2.0 / PATA 3=3.0. **NO redondear** el storage: `2/3 × 15000 = 10000.0` exacto en Float Python; con `round(0.6667) × 15000 = 10000.5` (error de $0.50 por boleta).

#### Zona-Cobrador (muchos-a-muchos, desde 02/05/2026)
- Tabla `zona_cobradores`: zona_id PK, cobrador_id PK, asignado_en (DateTime)
- Una zona puede tener múltiples cobradores simultáneamente
- `Zona.cobrador_id` (property) → ID del último cobrador
- `Zona.cobrador` (property) → objeto Cobrador del último asignado
- **CRÍTICO:** ZonaCobrador debe definirse ANTES que Zona y Cobrador en models.py

#### Zona-Vendedor
- `Zona.vendedor_id` FK a vendedores (nullable) — una zona = un vendedor

---

### Módulo Vendedores — actualizado 07/05/2026

#### Modelos
- `Vendedor.es_jefe_equipo` (Boolean, default False) — solo un jefe activo a la vez
- `LiquidacionVendedor`: vendedor_id, fecha, cuotas_vendidas, **cuotas_equiv (Float desde 11/05/2026)**, cuota_1_total, monto_cuotas, comision_cuotas_pct, comision_cuotas, contados_vendidos, monto_contados, comision_contados_pct (default 30%), comision_contados, total_comision, observacion
- `Boleta.liquidacion_vendedor_id` (FK a liquidaciones_vendedor, nullable)
- `EntregaCaja.vendedor_id` (FK a vendedores, nullable)

#### Flujo correcto de boletas por vendedor
```
SIN_VENDER
  → [Entrega a caja] → CAJA (sin liquidacion_vendedor_id)
  → [Liquidar vendedor] → CAJA (con liquidacion_vendedor_id)
  → [Cargar comprador] → VENDIDO
```
- La liquidación se hace sobre boletas en CAJA (sin liq_id)
- Los vendedores NO tienen boletas en BAJA — la baja viene solo de cobranza

#### Modelo de comisión — SIMPLIFICADO
**Unidad base = PATA 1. Todo en múltiplos de PATA 1.**
- `PATA1_VC` = valor_cuota de la talonera COMUN con multiplicador==1
- `PATA1_NC` = num_cuotas de PATA 1
- **Cuota 1 por boleta** = `multiplicador × PATA1_VC` (PATA 0 = 0.6667 × $15.000 = $10.000)
- **Contado por boleta** = `multiplicador × PATA1_NC × PATA1_VC` (PATA 0 = 0.6667 × 12 × $15.000 = $120.000 → 30% = $36.000)
- **Cuotas extras** = cantidad × PATA1_VC
- Cada boleta en `pendientes_json` tiene `multiplicador` (Float desde 11/05/2026); badge lleva `data-mult`

#### Modal de liquidación
- Boletas agrupadas por PATA con checkboxes
- Cálculo en tiempo real con PATA1_VC y PATA1_NC
- **`parseFloat(chk.dataset.mult)` (no `parseInt`)** desde 11/05/2026 — parseInt(0.67) = 0 rompía PATA 0.

#### Router vendedores.py — endpoints clave
- `_stats_bulk(db)`: dict por vendedor con `caja`, `liq_pendiente`, `vendido`, `baja`. Desde 09/05/2026 `vendido` cuenta TODAS las boletas con `comprador_id IS NOT NULL`.
- `GET /vendedores/{vid}/detalle`: incluye `pendientes_json` (multiplicador Float), `pata1_vc`, `pata1_nc`
- `POST /vendedores/{vid}/liquidar`: comisión basada en multiplicador × PATA1
- `POST /vendedores/entrega-caja`: SIN_VENDER → CAJA + REASIGNAR

#### Detalle del vendedor — orden y CONTADO
- **Orden jerárquico de PATAs** (`_pata_sort_key`):
  1. PATA con número (PATA 0, 1, 2, 3...) — numéricamente
  2. Otras COMUN sin número — alfabéticamente
  3. CONTADO al final
- **CONTADO pool**: números entregados al vendedor aún no asignados; badge ★ pero NO liquidables

#### Templates vendedores
- `vendedores.html`: tabla limpia, doble-click → detalle
- `vendedor_detalle.html`: tarjetas + nav-tabs (Caja / Liquidaciones) + modales a nivel raíz
- **Helpers JS (11/05/2026):** `fmtMult(m)` (entero si entero, 2 decimales si no) y `fmtPonderado(p)` (redondea a entero).

#### 3 estados de boleta en el detalle (colores)
- Azul claro (`#e7f1ff`): CAJA sin liq_id — pendiente liquidar
- Verde (`#d1e7dd`): CAJA con liq_id — liquidado, pendiente cargar comprador
- Azul opaco: VENDIDO — comprador registrado

#### Migraciones en main.py (startup) — todas en try/except
- `es_jefe_equipo`, `vendedor_id` en entregas_caja
- CREATE TABLE `liquidaciones_vendedor`
- `liquidacion_vendedor_id` en boletas
- `num_cuotas`, `num_digitos` en taloneras
- `cuota_1_total`, `cuotas_equiv` en liquidaciones_vendedor
- `numero_especial_2`, `talonera_especial_2_id` en boletas
- **11/05/2026:** `ALTER COLUMN multiplicador TYPE DOUBLE PRECISION` en taloneras (postgres) + recalcular `num_series / 3.0`. Mismo ALTER TYPE para `cuotas_equiv`.

---

### Módulo Cobranza (/cobranza/) — implementado 01-02/05/2026
- Página principal: selector mes/año, tarjetas por cobrador
- Planilla: template standalone hoja A4, 3 columnas, 4 bloques de 10 filas (120 slots)
- Cuotas anticipadas: celda negra + X blanca
- Tabs: Planillas | Emplantillado | Liquidación
- Modelos: Planilla, Liquidacion, LiquidacionDetalle
- **Filtros de boletas activas** (cobranza.py): usan `condicion IN [VENDIDO, EN_COBRANZA]` — CORRECTO. NO tocar.

---

### Talonera Especial CONTADO — ETAPA 1 + ETAPA 2 (07/05/2026)

**Modelo Boleta — dos slots:**
- `Talonera.tipo` — "COMUN" o "CONTADO"
- Slot 1: `Boleta.numero_especial` + `Boleta.talonera_especial_id`
- Slot 2: `Boleta.numero_especial_2` + `Boleta.talonera_especial_2_id` (deferred)
- `Talonera.boletas` con `foreign_keys="Boleta.talonera_id"`

**Reglas de negocio:**
- CONTADO = pool para sorteo extra al pagar al contado total
- CONTADO 2 VECES = pool para sorteo extra al pagar en 2 cuotas
- Modalidades: cuotas (sin extras) | 1pago (ambos slots) | 2pagos (solo slot 2)

**Pendiente — ETAPA 3:** sorteo CONTADO cruzando numero_especial y numero_especial_2

---

### Otras funcionalidades implementadas
- Login/logout JWT (cookies)
- ABM Compradores, Vendedores, Cobradores, Zonas, Taloneras, Boletas
- Generación de boletas por rango (sin duplicar)
- Auto-asignación de cobrador al guardar comprador (por zona)
- Exportación Excel socios: GET /compradores/exportar (openpyxl==3.1.2)
- Dashboard /reportes/: cards + tablas por talonera, zona, top vendedores/cobradores
  - **Top Vendedores**: cuenta toda boleta con socio cargado, ponderado por `Talonera.multiplicador` (PATA 0=0.67, PATA 1=1, PATA 2=2, ...). NO filtrar por `condicion=VENDIDO`.
  - **11/05/2026:** valores agregados se renderizan con `| round(0) | int` (Float guardado, entero mostrado).
- Módulo Sorteos: ABM Tómbola Nocturna Entre Ríos (SEMANAL/MENSUAL/FINAL/CONTADO)
- Módulo Ganadores: cruza 4c/3c/2c con exclusión
- Color por PATA: Talonera.color, picker en UI

---

### Preferencias de UI consolidadas
- Tablas: table-sm + font-size: 0.8rem + filas ~28px
- Buscador único con selector de columna (desde 09/05/2026)
- Sortable en todos los encabezados
- Fecha: almacena ISO en data-val, muestra dd/mm/yyyy
- Dirección: CSS text-transform: uppercase
- Sin botón eliminar en tablas — solo lápiz
- Vendedores: Entrega a Caja en detalle del vendedor
- **PATA 0, 1, 2, etc. → mostrar como X0, X1, X2** (display via `replace('PATA ', 'X')`)
- Navbar horizontal Bootstrap 5 sticky-top (10/05/2026)
- Sub-secciones grandes → preferir nav-tabs; listas históricas → modales
- **Multiplicador fraccionario** (PATA 0): display numérico `× 0.67` (no fracción `× ⅔`). Agregados redondean a entero al mostrar, Float en DB.

---

### Sesión 09/05/2026 — Auditoría completa del criterio "vendidas/vendido"

**Bug original:** dashboard "Por Talonera" mostraba 0 vendidas para PATA 2/8. Causa: filtro `condicion = VENDIDO`, pero boletas pasan a `CAJA` (Al contado) o `EN_COBRANZA` tras cargar socio.

**Lugares corregidos** (cambiaron `condicion = VENDIDO` → `comprador_id IS NOT NULL`):
1. `reportes.py` stats_por_talonera + stats_por_zona
2. `taloneras.py` validación al ELIMINAR talonera
3. `taloneras.html` badge "X vend."
4. `vendedores.py` `_stats_bulk` → `vendido`

**Filtros que SÍ están bien:** `cobranza.py` con `[VENDIDO, EN_COBRANZA]` — NO tocar.

---

### Sesión 09/05/2026 — UI Socios

**Buscador único** en compradores.html: input + select de columna (Todo / N° Boleta / Apellido / etc.), filtro client-side.

**Mostrar X0/X1/X2:** tabs, badges con `{{ nombre | replace('PATA ', 'X') }}`. DB sigue con "PATA N".

---

### Sesión 10/05/2026 — Navbar horizontal + Sorteos rediseñados

#### Navbar horizontal (`base.html`)
- Topbar Bootstrap 5 navbar-expand-lg sticky-top
- Mobile (<992px): hamburguesa con bloque de usuario al pie
- Colores #1a2a4a azul / #e63946 rojo activo

#### Módulo Sorteos — agrupado por mes (acordeón)
- Backend: `listar` devuelve `sorteos_por_mes` (lista de dicts) y `mes_actual_key`
- Template: accordion Bootstrap, mes actual abierto por defecto
- `<a>` Extracto fuera del `<button>` (HTML válido)

#### Generación de Extracto Mensual
- Endpoint `GET /sorteos/extracto/{year}/{month}`
- SEMANAL/MENSUAL/CONTADO: 1° premio; FINAL: hasta 3° premio
- Coincidencia por sufijo, filtros `fecha_venta < fecha_sorteo` y `comprador_id IS NOT NULL`
- Deduplica por boleta, excluye taloneras CONTADO (son pool)

#### Plantilla A4 imprimible (`sorteo_extracto.html`)
- Times New Roman, hoja simulada 210mm × 297mm
- Toolbar: columnas 1/2/3, tamaño S/M/L, copias 1/4/9 con persistencia localStorage
- Multi-column CSS, `break-inside: avoid`
- Copias 4-up (2×2) y 9-up (3×3) con replicación JS

#### Bug fix — `guardar_resultado` truncada
- Función cortada después de `# Normaliza`. FastAPI devolvía null (200 OK).
- Reconstrucción con normalización zfill(4), validación, try/except.

---

### Sesión 10/05/2026 (cont. 2) — Modal Liquidar compacto + Detalle ponderado

- Etiquetas modal acortadas: Cuotas→Cuo, Contado→Cont, Contado 2 veces→Cont en 2
- Badge total "Taloneras liquidadas" ponderado por multiplicador (no conteo crudo)
- Cabeceras de PATA: "N boleta/s × M = X" usa suma de multiplicadores (desde 11/05/2026 con `fmtMult`/`fmtPonderado`)

---

### Sesión 10/05/2026 (cont. 3) — cuotas_equiv persistido + Refresh post-entrega + Renombrar zonas

#### Refresh tras "Pasar a CAJA"
- En `entregarCajaVendedor()` rama ok con total > 0, programa `setTimeout(reload, 900)`.

#### Columna `cuotas_equiv` (ponderado) en LiquidacionVendedor
- `cuotas_equiv = Column(Float, default=0.0)` (Float desde 11/05/2026)
- Migración: `ALTER TABLE ADD COLUMN cuotas_equiv INTEGER DEFAULT 0` + 11/05/2026 `ALTER COLUMN TYPE DOUBLE PRECISION`
- Backfill: suma multiplicador de boletas atadas; fallback a literal si hay contados mezclados
- Template: tabla muestra `{{ (liq.cuotas_equiv or liq.cuotas_vendidas) | round(0) | int }}` con tooltip exacto

#### Badge "Todas" en /compradores/
- Pondera por multiplicador: `total_compradores = round(sum(t["total"] * t["multiplicador"] for t in tabs))`
- `sin_cobrador` queda literal (personas)

#### Renombrar zonas con propagación automática
- Endpoint `POST /zonas/{zid}/editar`. Modal compartido con script IIFE para precargar inputs.

---

### Sesión 10/05/2026 (cont. 4) — Detalle vendedor: tabs Caja/Liquidaciones + modales

- Nav-tabs Bootstrap: Caja (default, badge azul) / Liquidaciones (badge verde)
- Historiales como modales (no collapse) a nivel raíz, NO dentro de tab-panes
- Modales `#modalHistEntregas` y `#modalHistLiquidaciones` (modal-xl scrollable)

---

### Sesión 10/05/2026 (cont. 5) — Jefe de equipo + Total liquidados solo boletas propias

#### Concepto de negocio
- Jefe de equipo (Ariel) recibe taloneras → institución lo liquida.
- Ariel hace "Pasar Caja" a Pajaro → `Boleta.vendedor_id` cambia.
- Pajaro cobra cuota 1 al socio.
- Al cargar socio en `compradores.py`, `b.vendedor_id` se sobrescribe por `Zona.vendedor_id`. Boleta queda con `liquidacion_vendedor_id = liq_de_Ariel` pero `vendedor_id = Pajaro`. NO es bug.

#### Backend (`vendedores.py`)
- `liquidacion_detalle`: cada boleta trae `reasignado_a_id` / `reasignado_a_nombre` cuando `b.vendedor_id != liq.vendedor_id`.
- `detalle`: cada boleta trae `liq_por_otro_nombre` / `liq_por_otro_es_jefe`.

#### Frontend (`vendedor_detalle.html`)
- Modal "Detalle de liquidación" de Ariel: alert info "N boletas pasaron a otro vendedor". Pills compuestos `[0733][→ Pajaro]`.
- Detalle del vendedor (Pajaro): pills compuestos `[0733][★ Ariel]` con tooltip "Liquidado por Ariel (jefe de equipo)".

#### Fix "Total liquidados" SOLO boletas propias
- `total_boletas_liquidadas` y `total_boletas_liquidadas_eq` se calculan iterando `boletas` (ya filtradas por `vendedor_id=vid`) en lugar de sumar campos persistidos de `LiquidacionVendedor`.
- Pool items CONTADO se siguen sumando.

#### Lección técnica — truncamiento silencioso recurrente
- Síntoma: Edits encadenados en archivos grandes pueden cortar el final sin error visible.
- **Volvió a pasar el 11/05/2026 con main.py.**
- Detección: `python3 -c "print(open(f,'rb').read()[-200:])"` muestra contenido real.
- Patch: Python directo desde bash (no Edit). Validar con `ast.parse` y `env.get_template`.

---

### Sesión 11/05/2026 — PATA 0 (talonera 2 series, $10.000)

**Contexto:** debido a situación económica, la institución sumó talonera barata con 2 números a $10.000 (vs PATA 1 con 3 números a $15.000). Es **2/3 exacto** de PATA 1, lo que ya habíamos previsto cuando se diseñó `multiplicador = num_series // 3`.

**Decisiones:**
- **Nombre:** `PATA 0` (display **X0**).
- **num_cuotas:** definible al crear/editar talonera (ya soportado desde 07/05/2026).
- **Display multiplicador:** numérico con 2 decimales (`× 0.67`). Helpers `fmtMult(m)` y `fmtPonderado(p)`.
- **Redondeo agregados:** entero al mostrar, Float guardado en DB.

**Cambios técnicos:**

1. **`app/models.py`:**
   - `Talonera.multiplicador`: `Integer → Float`, default `1.0`.
   - `LiquidacionVendedor.cuotas_equiv`: `Integer → Float`, default `0.0`.

2. **`app/routers/taloneras.py`:**
   - Fórmula: `multiplicador = num_series / 3.0` (antes `num_series // 3`).
   - **CRÍTICO: NO redondear**: `(2/3) * 15000 = 10000.0` exacto; con `round(2/3, 4) = 0.6667` da `10000.5` (error de $0.50).

3. **`app/main.py` migración (entre num_digitos y cuota_1_total):**
   - `ALTER TABLE taloneras ALTER COLUMN multiplicador TYPE DOUBLE PRECISION USING multiplicador::double precision` (postgres only; SQLite laxo).
   - `UPDATE taloneras SET multiplicador = CAST(num_series AS DOUBLE PRECISION) / 3.0 WHERE tipo='COMUN' AND num_series > 0`.
   - Mismo ALTER TYPE para `liquidaciones_vendedor.cuotas_equiv`.
   - Backfill: `_mult` queda Float (no `int(...)`).

4. **`app/schemas.py`:** `TaloneraCreate.multiplicador: int → float`.

5. **`app/routers/vendedores.py`** (5 lugares con `int(multiplicador)` → `float`):
   - `liquidacion_detalle`: dict boletas + fallback `cuotas_equiv` Float.
   - `detalle`: `multiplicador` en `pendientes_items`, `total_boletas_liquidadas_eq`, `total_vendidas_pond`.
   - `liquidar`: `cuotas_equiv` al persistir.

6. **`app/routers/compradores.py`** badge "Todas":
   - `multiplicador` en tabs: `int(...) → float(...)`.
   - `total_compradores = round(sum(...))`.

7. **Templates:**
   - **`vendedor_detalle.html` (script):**
     - Helpers JS: `fmtMult(m)` y `fmtPonderado(p)`.
     - **CRÍTICO:** `parseInt(chk.dataset.mult) → parseFloat(...)` en `recalcular()`. `parseInt(0.67) = 0` rompía PATA 0.
     - Tabla histórica: `{{ liq.cuotas_equiv | round(0) | int }}` con tooltip exacto.
   - **`reportes.html`** (6 lugares): `| round(0) | int` en agregados.
   - **`boletas.html`**: badge `×N` entero si entero, decimal si no.

**Validación matemática (post-fix):**
- PATA 0: mult = `2/3 ≈ 0.6667`, cuota 1 = `0.6667 × $15.000 = $10.000` exacto, contado = `0.6667 × 12 × $15.000 = $120.000` exacto, comisión 30% = `$36.000`.
- Mixto: `5×PATA0 + 17×PATA1 + 7×PATA2 + 1×PATA3 = 37.33` → display `37`.

**Truncamiento de main.py (segunda vez del bug):**
- `ast.parse` falló con `SyntaxError: unterminated string literal (line 587)`. Últimas 12 líneas desaparecieron.
- Read tool engaña: mostraba 599 líneas. Bash mostró 586 líneas, terminaba en `"Creando usua`.
- Patch: Python directo appendeando bytes. Validar `ast.parse` después.
- Pattern confirmado: archivos >500 líneas + 3+ Edits encadenados = riesgo de truncamiento.

---

### Sesión 14/05/2026 — Liquidación: restricción secuencial + resumen agrupado por mes calendario

**Contexto:** dos correcciones a `/cobranza/liquidacion/{planilla_id}` (template `cobranza_liquidacion_detalle.html` + router `cobranza.py`).

#### 1) Restricción secuencial al marcar cuotas
- **Regla:** no se puede marcar la cuota N sin que la N-1 esté paga. Tampoco se puede desmarcar la N si la N+1 sigue marcada en el mes actual.
- "Paga" incluye: anticipada (`n <= cuotas_anticipadas`, celda `×`), histórica (`historial[n]` con un mes distinto al actual, celda azul), o ya marcada en el mes actual (`sel[bid].has(n)`).
- Backend pasa `boletas_info = {bid: {anticipadas, pactadas, historial: {cuota:mes}}}` al template (vía `| tojson`).
- JS: helper `cuotaPaga(bid, n)` + checks dentro del handler de click. Alertas claras al usuario.

#### 2) Resumen agrupado por MES CALENDARIO (no por número de cuota)
- **Antes:** cada fila del resumen era una cuota (1..12) etiquetada por su mes — cuota 1 → JUNIO, cuota 2 → JULIO, etc. Si un socio pagaba 7 cuotas en mayo, aparecía 1 en JUNIO + 1 en JULIO + ... + 1 en DICIEMBRE.
- **Ahora:** cada fila es el mes calendario en que se cobró. Si pagás 7 cuotas hoy en mayo, MAYO COL 1 = 7, el resto vacío.
- Filas siguen siendo los 12 meses de la campaña (`meses_campana`), label "NOMBRE (num_mes)".
- Server (`cobranza.py`): construye `resumen_otros[mes_calendario] = {1:0, 2:0, 3:0}` con TODOS los meses, excluyendo el mes actual (lo maneja JS).
  ```python
  for k, v in historial_map[b.id].items():
      mes_pago = int(v)
      if mes_pago == planilla.mes: continue
      if 1 <= mes_pago <= 12: resumen_otros[mes_pago][col] += 1
  ```
- Template: cada `<tr data-mes="{{ num_mes }}">`, cada `<td>` de columna lleva `data-base="{{ resumen_otros[num_mes][col] }}"` (baseline server).
- JS `recalcResumen()`:
  - Suma todas las cuotas marcadas en `sel` agrupadas por columna → `cntMesActual = {1: N, 2: M, 3: K}`.
  - Para cada fila: `c1 = base + (esMesActual ? cntMesActual[1] : 0)`. Mes actual recibe la suma total; los demás solo el baseline.

#### Validación (mental + simulada con Python)
- **Test 1 (7 cuotas mayo)**: hist={1:5..7:5}, col=1, planilla.mes=5 → MAYO COL 1 = 7, otros 0 ✓
- **Test 2 (mixto)**: B101 col1 con {1:3,2:3,3:3,4:5,5:5} + B202 col2 con {1:4,2:4,3:5}, planilla.mes=5 → MARZO COL1=3, ABRIL COL2=2, MAYO COL1=2 + COL2=1 ✓

#### Decisiones de UX confirmadas con Sergio
- Mostrar TODOS los meses con históricos en el resumen (vista anual completa).
- Anticipadas/históricas habilitan la siguiente cuota (no exigir re-marcar).

#### Archivos modificados
- `app/routers/cobranza.py` (`liquidacion_detalle`): reemplaza `resumen_inicial` por `resumen_otros`, agrega `boletas_info`.
- `app/templates/cobranza_liquidacion_detalle.html`: filas `data-mes`/`data-base`, JS con `cuotaPaga`/secuencial/recálculo nuevo.

---

### Sesión 14/05/2026 (cont.) — Fix emplanillado + Rearmado de liquidaciones de vendedores

#### Fix: la cobradora no veía sus boletas en Emplanillado
- **Síntoma:** en `/cobranza/emplanillado`, al editar la planilla de una cobradora (ej. CARO) no aparecían boletas que en la sección Socios figuran como "En cobranza".
- **Causa:** todas las queries de `cobranza.py` filtraban `condicion IN [VENDIDO, EN_COBRANZA]`. Pero una boleta puede quedar con `condicion = CAJA` tras liquidarse al vendedor — al cargar el socio (`compradores.py` ~línea 268) solo se cambia `SIN_VENDER → VENDIDO`, NO se re-deriva la condicion. La lista de Socios igual la muestra "En cobranza" porque ese badge se basa en `cobrador_id`, no en `condicion`.
- **Fix aplicado:** en `app/routers/cobranza.py`, los 5 filtros `condicion.in_([VENDIDO, EN_COBRANZA])` → `condicion != CondicionBoleta.BAJA`. Quedan alineados con el criterio del badge "En cobranza" de Socios (basado en `cobrador_id`, excluye BAJA). Líneas: index resumen (57), armar_planilla (115), emplanillado resumen (140), disponibles de planilla_editar (185), planilla preview (552).
- **OJO:** esto contradice la nota vieja "Filtros cobranza.py [VENDIDO, EN_COBRANZA] — NO tocar". Esa nota quedó obsoleta: el escenario CAJA-tras-liquidación no estaba contemplado.

#### Script `rearmar_liquidaciones_10052026.py` — liquidaciones de vendedores al 10/05
- **Contexto:** las liquidaciones viejas (sistema anterior) quedaron desfasadas — solo 26 de 53 boletas vendidas estaban liquidadas.
- **Decisiones con Sergio:** una liquidación por CADA vendedor (agrupado por `Boleta.vendedor_id`); REEMPLAZAR las viejas (borrar cabeceras `LiquidacionVendedor` + limpiar `liquidacion_vendedor_id`); criterio "vendido" = `comprador_id IS NOT NULL` sin filtrar por `fecha_venta`.
- **Qué hace:** borra todas las `LiquidacionVendedor` + limpia enlaces, agrupa boletas con socio por vendedor, crea 1 liquidación por vendedor con `fecha = 10/05/2026`, replica la matemática de `POST /vendedores/{vid}/liquidar`. Modalidad contado = boleta con `numero_especial` o `numero_especial_2`. `cuotas_extras_* = 0` (no derivables). No toca `vendedor_id` ni `condicion`.
- **Flags:** `--dry-run` (no escribe) y `--yes` (sin confirmar). Sigue el patrón de `crear_liquidacion_ariel_10052026.py`.
- **Aplicado en producción (Railway) 14/05/2026:** 53 boletas, 4 liquidaciones — ARIEL 13 (pond. 36), HUGO 26 (pond. 35), PAJARO 7 (pond. 10), VICTOR 7 (pond. 7). Cuota 1 total $1.320.000. Cero contados.
- Verificado en sandbox con copia de la DB local: dry-run, apply e idempotencia OK.

#### Notas operativas — correr scripts contra Railway desde Windows
- Falta `psycopg2-binary` localmente → `py -3.12 -m pip install psycopg2-binary`.
- La `DATABASE_URL` de Railway con host `postgres.railway.internal` NO funciona desde la PC — usar la URL **pública** (`DATABASE_PUBLIC_URL`, host tipo `xxxx.proxy.rlwy.net`).
- Sintaxis PowerShell: `$env:DATABASE_URL="postgresql://..."` (sin espacio después de `$env:`).

#### Lección técnica — mount stale del sandbox (volvió a pasar)
- Tras editar `cobranza.py` con el file tool, el mount de bash mostró el archivo con 180 bytes nulos intercalados → `import` y `ast.parse` fallaban con "source code string cannot contain null bytes".
- El archivo en disco estaba bien (confirmado con el Read tool, que es autoritativo).
- Para testear en el sandbox: copiar `app/` a `/tmp/`, hacer `open(f,'rb').read().replace(b'\x00',b'')` en cada `.py`, y correr ahí.

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 3**: sorteo cruzando numero_especial y numero_especial_2
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)
- Validar con Sergio: columnas "Taloneras" y "Vendidas" en dashboard Por Zona — ¿eliminar una?
- Posible extensión: aplicar `PATA N → XN` en todas las pantallas (filtro Jinja global)
- Validar multi-up extracto con datos reales
- **PATA 0 — probar en producción**: crear talonera real en Railway (num_series=2, valor_cuota=10000, num_cuotas=?). Verificar cuota 1 = $10.000 exacto, modal Liquidar con selecciones mixtas PATA 0 + PATA 1, reportes muestran enteros sin decimales raros.
- **Liquidación — probar en producción**: marcar 7 cuotas seguidas en mayo y verificar resumen MAYO=7. Probar bloqueo al saltar cuota (marcar cuota 9 sin 8 → alerta). Probar bloqueo al desmarcar fuera de orden.

---

### Sesión 18/05/2026 — Fix "Al contado" se colaba al emplanillado

#### Contexto del bug
- En `/cobranza/emplanillado`, planilla de MABEL: aparecían números (6645 EL VAGON, 6648 ARGACHA EDUARDO) con TODAS las 12 cuotas marcadas X (pagadas), siendo que en Socios figuraban como "Al contado" (badge verde). El sistema permitía asignarles cobrador igual, y al armar la planilla del mes esas boletas entraban con sus 12 cuotas en X.
- **Causa raíz:** el badge "Al contado" en `compradores.html` (línea 246) se asigna cuando se cumple `es_contado` (tiene `numero_especial` / `numero_especial_2`) **O** `pagado` (`cuotas_pagadas >= cuotas_pactadas`). Pero los filtros de `cobranza.py` (5 lugares) solo excluían `numero_especial_2 IS NOT NULL`.
- **Nuance crítica del negocio (explicada por Sergio):** las boletas "al contado" pasan un tiempo SIN `numero_especial` asignado. Los números especiales vienen en talonarios de 5; recién cuando se agota un talonario se carga el especial. Hasta entonces el único indicador real de "está paga al contado" es `cuotas_anticipadas = num_cuotas` → `cuotas_pagadas >= cuotas_pactadas`.

#### Fix 1 — `app/routers/cobranza.py` (5 lugares)
Agregada condición `cuotas_pagadas < cuotas_pactadas` JUNTO al filtro existente `numero_especial_2 IS NULL` en:
1. `armar_planilla` (~línea 121) — no asigna a nueva planilla las pagadas en total.
2. `emplanillado` resumen (~línea 154) — no las cuenta como activas.
3. `planilla_editar_form` **limpieza silenciosa** (~línea 195) — `or_(numero_especial_2.isnot(None), cuotas_pagadas >= cuotas_pactadas)` → DESEMPLANILLA las que ya estaban (resuelve el dato existente sin script).
4. `planilla_editar_form` disponibles (~línea 218) — no aparecen en la lista para agregar.
5. `planilla_editar_guardar` (~línea 259) — filtro defensivo.

**Los dos filtros son complementarios, no redundantes:**
- `cuotas_pagadas >= cuotas_pactadas` cubre el período de espera (todavía sin numero_especial).
- `numero_especial_2 IS NOT NULL` cubre después de cerrar el talonario especial.

#### Fix 2 — `app/routers/compradores.py` (~línea 405)
Al cargar el socio, NO auto-asignar `cobrador_id` por zona si la boleta queda toda paga:
```python
_toda_paga = (b.cuotas_pagadas or 0) >= (b.cuotas_pactadas or 0)
if c.zona_id and not _toda_paga:
    z = db.query(models.Zona).get(c.zona_id)
    if z and z.cobrador_id:
        b.cobrador_id = z.cobrador_id
```
Esto previene que aparezcan en el dropdown de cobrador en Socios y que se vuelvan a colar al emplanillado en el futuro.

#### Decisión de diseño
- NO se limpia el `cobrador_id` de boletas históricas que ya tenían cobrador asignado y quedaron pagas — solo se las saca de planillas y dropdowns futuros. El badge "Al contado" en Socios tiene prioridad sobre "En cobranza", así que no se ve raro.

#### Memoria unificada
- Existían dos `memoria_sergio.md` duplicadas: `MeIA/` (vacía) y `MeIA/bono-app/` (469 líneas). Consolidadas en `MeIA/memoria_sergio.md` (raíz). El archivo en `bono-app/` quedó como puntero al de la raíz. Agregada nota explícita al inicio del archivo sobre la ubicación única.

#### Pendiente de verificación
- Hacer git push para que Railway tome los cambios.
- Abrir `/cobranza/emplanillado` → planilla de MABEL: 6645 y 6648 deberían desaparecer (limpieza silenciosa).
- Crear un comprador "al contado" nuevo y verificar que NO se le asigne cobrador automáticamente.

---

### Sesión 18/05/2026 (cont.) — Unificación columna Cobrador + nueva columna Cuotas en Socios

#### Contexto
- En `/compradores/` había dos columnas adyacentes redundantes: "Cobrador" (dropdown) y "Condición" (badge "Al contado" / "En cobranza" / "En cuotas" / "Baja").
- Sergio pidió unificarlas en la columna "Cobrador" y reemplazar "Condición" por una columna "Cuotas" que muestre `pagadas/pactadas` (necesario porque a futuro podría haber taloneras con `num_cuotas != 12` que entren al sorteo final).

#### Lógica unificada en columna "Cobrador" (compradores.html, 5 estados en orden)
1. **BAJA** → badge rojo "Baja" + nombre del cobrador histórico (chico, abajo) si lo tenía
2. **CONTADO** (badge verde) — disparado si `es_contado` (tiene `numero_especial` / `_2`) OR `anticipo_total` (`cuotas_anticipadas >= cuotas_pactadas`, entró pagando todo de entrada). Muestra nombre cobrador si lo tiene (por bug viejo de auto-asignación).
3. **PAGADO** (badge gris) — `cuotas_pagadas >= cuotas_pactadas` pero NO `anticipo_total` (entró parcial y cerró pagando mes a mes en cobranza). Muestra siempre el nombre del cobrador que cerró.
4. **En cobranza** → dropdown con cobrador seleccionado (no toda paga, con cobrador)
5. **Sin cobrador** → dropdown "— Sin cobrador —" (no toda paga, sin cobrador)

**Clave técnica:** la distinción CONTADO vs PAGADO usa `cuotas_anticipadas` (snapshot al cargar el socio) vs `cuotas_pagadas` (acumulado actual). NO depende de si tiene `cobrador_id` (porque hay boletas históricas — ej. 6645 EL VAGON, 6648 ARGACHA — que pagaron al contado pero quedaron con MABEL asignada por bug viejo). El campo `cuotas_anticipadas` se setea al cargar/editar socio en `compradores.py` (líneas 403, 674-676, 883).

- `numero_especial` siempre fuerza CONTADO (independiente del cobrador).
- Etiqueta "CONTADO" (no "AL CONTADO") según preferencia de Sergio.

#### Nueva columna "Cuotas" (reemplaza "Condición")
- Muestra `cuotas_pagadas / cuotas_pactadas` como badge.
- Colores: **verde** si `_pag >= _pac` (cerrada), **gris claro** si `_pag == 0`, **amarillo** si en progreso.
- `data-val` con formato "X/Y"; JS de sort parsea como proporción `pag/pac` para ordenar por % avance (soporta taloneras con distinto `num_cuotas` correctamente).

#### Archivos modificados
- `app/templates/compradores.html`:
  - `<th>Condición</th>` → `<th class="sortable" data-col="9">Cuotas ...</th>`
  - Selector del buscador: opción `9` cambia label "Condición" → "Cuotas"
  - `<td>` del cobrador: bloque if/elif con 5 ramas (badge BAJA / AL CONTADO / PAGADO / dropdown)
  - Nuevo `<td>` Cuotas con badge tricolor según estado
  - `getCellText()` (JS sort): agrega caso para col 9 que parsea "X/Y" en proporción decimal

#### Validación
- Test jinja2 render con 10 casos: CONTADO entrada limpia, CONTADO con cobrador heredado (caso EL VAGON/MABEL), CONTADO via numero_especial, PAGADO mes a mes con varios cobradores, En cobranza, Sin cobrador, BAJA, talonera futura 8 cuotas en ambos modos. Todos correctos.

#### Truncamiento silencioso (TERCERA vez)
- Tras los Edits, el archivo se cortó después de `setTimeout(() => location` perdiendo las 10 líneas finales + `{% endblock %}`. Detectado con `python3 -c "print(open(f,'rb').read()[-500:])"`.
- Parche: script Python que toma `git show HEAD:app/templates/compradores.html`, encuentra el marker `setTimeout(() => location`, y reemplaza la cola del archivo truncado con la cola original (preservando todos mis cambios anteriores al marker). Validación con `env.get_template()` OK.
- Bytes finales: 52318 (vs 51832 originales). Líneas: 1145 (vs 1138 originales).

#### Pendiente de verificación
- Git push para Railway.
- Visual: 6645 EL VAGON y 6648 ARGACHA ahora deberían decir **CONTADO + MABEL** (no PAGADO).
- Confirmar que la columna se ve compacta en mobile.

---

### Sesión 22/05/2026 — Módulo Contabilidad ampliado: egresos reales + ABM gastos

#### Contexto
- La pantalla /contabilidad/ solo mostraba comisiones de vendedores y cobradores.
- Se necesitaba reflejar el modelo de negocio completo: pago mensual a Bomberos (fijo) + premios + gastos de viaje/alojamiento/etc.

#### Nuevos modelos en `app/models.py` (se crean solos vía `create_all`)

```python
class ConfigBono(Base):
    __tablename__ = "config_bono"
    clave       = Column(String, primary_key=True)
    valor_float = Column(Float, default=0.0)

class GastoContabilidad(Base):
    __tablename__ = "gastos_contabilidad"
    id          = Column(Integer, primary_key=True, index=True)
    descripcion = Column(String, nullable=False)
    categoria   = Column(String, default="OTRO")  # PREMIO / VIAJE / ALOJAMIENTO / OTRO
    fecha       = Column(Date, nullable=True)
    monto       = Column(Float, default=0.0)
    created_at  = Column(DateTime, server_default=func.now())
```

#### Router `app/routers/contabilidad.py` — cambios

- `_get_config(db, clave, default)` / `_set_config(db, clave, valor)`: helpers para leer/escribir ConfigBono.
- **Pago Bomberos**: `pago_mensual_bomberos = _get_config(db, "pago_mensual_bomberos")`. Total = `pago_mensual * meses_liquidados` (cantidad de (año, mes) distintos con liquidación de cobranza).
- **Gastos varios**: suma de `GastoContabilidad.monto`.
- **Ganancia neta real**: `total_recaudado - (com_vendedores + com_cobradores + total_bomberos + total_gastos)`.
- **Ganancia proyectada**: `total_esperado - total_egresos` (si se cobra todo).
- Nuevos endpoints:
  - `POST /contabilidad/config/bomberos` — guarda pago mensual (Form: `pago_mensual`)
  - `POST /contabilidad/gastos` — crear gasto
  - `POST /contabilidad/gastos/{id}/editar` — editar gasto
  - `POST /contabilidad/gastos/{id}/eliminar` — eliminar gasto

#### Template `app/templates/contabilidad.html` — cambios

- **Row 2 de KPIs**: Com. vendedores / Com. cobradores / Pago Bomberos (con detalle meses × mensual) / Gastos varios.
- **Row 3 de KPIs**: Total egresos / Ganancia neta real (sobre recaudado) / Ganancia proyectada (sobre esperado).
- **Tab "Egresos"**: formulario inline para configurar pago mensual Bomberos + tabla ABM de gastos con modal agregar/editar + botón eliminar con confirm.

#### Fórmula completa ganancia neta
```
ganancia_neta = total_recaudado
              - com_vendedores      # cuota 1 o 30% contados (liquidaciones)
              - com_cobradores      # % pactado por cobrador (liquidaciones cobranza)
              - pago_mensual_bomberos * meses_con_liquidacion
              - sum(GastoContabilidad.monto)
```

#### Nota técnica
- Truncamiento silencioso volvió a ocurrir con el Write tool en contabilidad.py (archivo >200 líneas). Detectado y parchado con `cp` desde /tmp. Patrón confirmado: Write tool también trunca, no solo Edit. Usar siempre Python/bash para archivos grandes.

---

### Sesión 22/05/2026 (cont.) — Fix buscador Socios + Múltiples planillas por cobrador

#### Fix buscador Socios — columna Cobrador
- **Bug:** al buscar "mabel" en columna Cobrador, aparecían TODAS las filas con dropdown.
- **Causa:** `textoCelda(td, 7)` hacía `data-val + ' ' + innerText`. El `<select>` de cobrador tiene innerText = texto de TODAS las options (todos los nombres de cobradores). Entonces cualquier fila con dropdown matcheaba cualquier nombre de cobrador.
- **Fix:** para col 7 (Cobrador) usar SOLO `data-val` (ya contiene el nombre del cobrador seleccionado). Cols 1 y 5 no cambiaron.
- **Archivo:** `app/templates/compradores.html` — función `textoCelda()`.

#### Múltiples planillas por cobrador por mes
- **Motivación:** un cobrador puede necesitar más de una planilla por mes (ej. MABEL con muchos socios).
- **Cambios en `app/routers/cobranza.py`:**
  1. `emplanillado`: pasa `r.planillas` (lista ordenada por numero) en vez de `r.planilla` (una sola).
  2. `index` (/cobranza/): ídem, pasa `r.planillas` (lista).
  3. `armar_planilla`: ya NO busca planilla existente para reusar. Siempre crea una nueva. Si no hay boletas sin emplanillar (`pendientes_count == 0`), redirige sin crear. Las boletas `planilla_id IS NULL` se asignan a la nueva planilla.
  4. Nuevo endpoint `GET /planilla/{planilla_id}/ver`: muestra una planilla específica por ID (no por cobrador+mes como el viejo `/{cobrador_id}/planilla`). Contiene toda la lógica de grid (3 cols × 40 filas).
- **Cambios en templates:**
  - `cobranza_emplanillado.html`: lista todas las planillas del cobrador para ese mes (P1, P2...) con Ver/Editar/Eliminar por cada una. Botón "Armar nueva planilla" siempre visible si hay boletas sin emplanillar.
  - `cobranza.html`: ídem, lista todas las planillas con botón "Ver P1", "Ver P2", etc. Usa `/cobranza/planilla/{id}/ver`.
- **Flujo para boletas que faltaban:** ir a Emplanillado → botón "Armar nueva planilla" → se crea P2 con las pendientes.
- **Nota:** truncamientos silenciosos ocurrieron en `cobranza.py` y `compradores.html` durante esta sesión. Detectados con `python3 -c "print(open(f,'rb').read()[-300:])"` y parchados desde `git show HEAD:archivo` + script Python.

---

### Sesión 23/05/2026 — Refactoring dashboard /reportes/

#### Tarjetas resumen (5 cards, antes 4)
- **Eliminada:** "Total Boletas"
- **Mantenidas:** Taloneras Vendidas (verde), Baja (rojo), Compradores (azul)
- **Nuevas:**
  - "Vendidas en Cuotas" (celeste): `_sum_mult(comprador_id IS NOT NULL AND numero_especial IS NULL)` — vendidas sin número contado asignado
  - "Al Contado" (amarillo): `_sum_mult(numero_especial IS NOT NULL)` — con número contado slot 1, ponderado por multiplicador
- Grid: `row-cols-2 row-cols-sm-3 row-cols-lg-5`

#### Filas expandibles — todos los bloques
Todos los bloques del dashboard tienen ahora comportamiento de click → expand/collapse con ícono chevron que rota 90°.

**Por Talonera** — al clickear la fila se despliega una sub-fila `<tr>` con:
- Taloneras COMUN: Contado (contado_1) + Contado 2 Veces (contado_2) — boletas de esa talonera con numero_especial/numero_especial_2 asignado
- Taloneras CONTADO: muestra vendidas_1 y vendidas_2 (los dos slots del pool)
- El botón "Ver socios" sigue en la última columna (no se perdió la navegación)

**Por Zona** — al clickear se despliega:
- Badges con desglose `talonera.nombre: vendidas` (ponderado) por cada talonera COMUN en esa zona
- Botón "Ver socios" → `/compradores/?zona={id}`

**Vendedores / Cobradores** — al clickear el nombre se despliega un `<div>` con badges de sus taloneras y cantidad

#### Backend — cambios en `app/routers/reportes.py`
- `totales`: reemplaza `boletas` por `cuotas` y `contado`
- `stats_por_talonera`: cada entrada COMUN agrega `contado_1`, `contado_2`; cada CONTADO agrega `vendidas_1`, `vendidas_2`
- `top_vendedores` y `top_cobradores`: cambian de SQLAlchemy Rows → dicts Python `{id, nombre, cantidad, taloneras: [{nombre, cantidad}]}`. Query agrupa por (vendedor/cobrador, talonera.nombre).
- `stats_por_zona`: agrega `talonera_detalle: [{nombre, vendidas}]` por zona (query con GROUP BY talonera.nombre)

#### JS — dos funciones de toggle (no Bootstrap collapse)
```javascript
toggleRow(id)  // para <tr>: display 'table-row' o 'none'
toggleDiv(id)  // para <div>: display 'block' o 'none'
```
Bootstrap collapse se evitó porque en `<tr>` usa `display:block` rompiendo el layout de tabla.

---
*Última actualización: 23 de mayo de 2026 — Refactoring dashboard: eliminado Total Boletas, agregadas tarjetas Vendidas en Cuotas + Al Contado, filas expandibles en todos los bloques (Por Talonera, Por Zona, Vendedores, Cobradores).*

---

### Sesión 23/05/2026 (cont.) — Fixes dashboard /reportes/ y /vendedores/ + Refactoring /contabilidad/

#### Fix "Al Contado = 0" en /reportes/
- **Bug:** tarjeta "Al Contado" mostraba $0 aunque había varios socios contado.
- **Causa:** `totales["contado"]` solo contaba boletas con `numero_especial IS NOT NULL`. Pero boletas "al contado" sin número especial asignado todavía (pendiente de cargar talonario) se detectan por `cuotas_anticipadas >= cuotas_pactadas` (`anticipo_total`).
- **Fix en `app/routers/reportes.py`:** `_es_contado = (numero_especial IS NOT NULL) | (cuotas_pactadas > 0 AND cuotas_anticipadas >= cuotas_pactadas)`. Igual que la lógica `es_contado or anticipo_total` de `compradores.html`. También se corrigió `totales["cuotas"]` para excluir los contado.

#### Fix "Total liquidados" inconsistente vendedores (lista vs detalle)
- **Bug:** Hugo mostraba 101 en el detalle del vendedor y 97 en el listado de /vendedores/.
- **Causa:** `_stats_bulk` usaba `SUM(cuotas_equiv)` (snapshot guardado al liquidar). El snapshot quedó desactualizado porque `talonera.multiplicador` cambió después (PATA 2 pasó de ×1 a ×2), y el backfill solo procesa liquidaciones con `cuotas_equiv == 0`. La columna además se migró como INTEGER, no FLOAT.
- **Fix en `app/routers/vendedores.py` (`_stats_bulk`):** `liquidados` ahora se calcula on-the-fly con query directa sobre boletas:
  ```python
  liq_total_rows = db.query(Boleta.vendedor_id, sum(case(contado→1.0, else_=Talonera.multiplicador)))
      .join(Talonera).filter(vendedor_id IS NOT NULL, liquidacion_vendedor_id IS NOT NULL)
      .group_by(Boleta.vendedor_id)
  ```
  Mismo criterio que `total_boletas_liquidadas_eq` en el detalle. Pool items CONTADO se suman aparte. `liquidados_cuotas` / `liquidados_contados` siguen usando snapshots (para el desglose, ya que la modalidad solo se guarda en la liquidación).

#### Refactoring /contabilidad/ — dashboard

**Cambios en `app/routers/contabilidad.py`:**

- La query de boletas ahora incluye BAJA (joinedload cobrador), y separa `boletas` (activas) de `baja_boletas`.
- Helper `_es_contado(b)`: `numero_especial IS NOT NULL OR numero_especial_2 IS NOT NULL OR (cuotas_anticipadas >= cuotas_pactadas > 0)`.
- Nuevas métricas:
  - `gross_cuotas`: `SUM(cuotas_pactadas × valor_cuota)` para activas no-contado.
  - `gross_baja`: `SUM(cuotas_pagadas × valor_cuota)` para BAJA (lo que pagaron antes de darse de baja).
  - `gross_contado`: `SUM(talonera.num_cuotas × valor_cuota)` para activas contado.
  - `com_cobradores_proyectada`: `SUM(cobrador.comision_pct/100 × cuotas_pactadas × valor_cuota)` para activas no-contado con cobrador asignado, excluyendo BAJA.
  - `com_vendedores_contado`: `SUM(lv.comision_contados)` de liquidaciones ya existentes.
  - `total_bruto = gross_cuotas + gross_baja + gross_contado - com_cobradores_proyectada - com_vendedores_contado`
  - `total_egresos_proyectado = total_com_vendedores + com_cobradores_proyectada + total_bomberos + total_gastos`

**Cambios en `app/templates/contabilidad.html`:**
- Card 1 Fila 1: "Total recaudado" → **"Total en Brutos"** (`total_bruto`). Desglose en letra chica: Cuotas / Bajas / Contado / − Com.cob.
- Fila 2: "Com. vendedores" → **"Comisión vendedores"** (mismo valor).
- Fila 2: "Com. cobradores" → **"Comisión cobradores"** = `com_cobradores_proyectada` (proyectado, excluye bajas). Nota "proyectado (excluye bajas)".
- Ganancia proyectada usa `total_egresos_proyectado` (con com.cobradores proyectada en vez de liquidada).

**Lógica de negocio (Total en Brutos):**
- Cuotas activas: proyecta cobro total (cuotas_pactadas × valor_cuota).
- BAJA: solo cuenta lo que realmente pagaron (cuotas_pagadas × valor_cuota).
- Contado: precio total del bono (num_cuotas × valor_cuota) menos comisión del vendedor ya liquidada.
- Se descuenta la comisión proyectada de cobradores (sobre las cuotas que gestionan).

---

### Sesión 23/05/2026 (cont. 2) — Mapa de Socios (Leaflet + Nominatim)

#### Funcionalidad
- En `/compradores/` (Socios), botón "Mapa" arriba a la izquierda (al lado del título), apunta a `/compradores/mapa`.
- Vista de mapa con **Leaflet** + **OpenStreetMap**, centrado en Concepción del Uruguay (-32.4847, -58.2347).
- Cada boleta vendida (`comprador_id IS NOT NULL`) con dirección cargada y talonera COMUN se dibuja como un **círculo con el número de boleta adentro**, coloreado según `Talonera.color`.
- Popup con apellido y nombre, dirección, zona y botón "Editar socio" (link a `/compradores/{id}/editar`).
- Leyenda de patas (X0/X1/X2/...) arriba con el color de cada una.

#### Backend — `app/routers/compradores.py` (al final del archivo)
- `GET /compradores/mapa` → renderiza `compradores_mapa.html`.
- `GET /compradores/mapa-data` → JSON `{ok, centro, patas, puntos}`. Cada punto: `{boleta_id, comprador_id, numero (zfill por num_digitos), apellido_nombre, direccion, zona, pata, pata_label (X0..), color}`. Solo taloneras COMUN.
- Paleta por defecto si la talonera tiene `color` vacío o `#ffffff` (PATA 0 morado, PATA 1 azul, PATA 2 verde, PATA 3 naranja, etc.).

#### Frontend — `app/templates/compradores_mapa.html`
- Carga Leaflet 1.9.4 desde jsDelivr (CDN).
- **Geocoding cliente** con Nominatim (gratis, sin API key). Política: 1 req/segundo (sleep 1100ms entre llamadas).
- Query: `{direccion}, Concepción del Uruguay, Entre Ríos, Argentina`. Si falla, reintenta sin números (caso "CALLE 1234" → "CALLE").
- Cache en `localStorage` key `bono_mapa_geocache_v1`: `{direccion_normalizada: [lat,lng] | null}`. Las direcciones que no se pudieron ubicar se cachean como `null` para no reintentar.
- **Bbox de descarte**: lat ∈ [-33.0, -32.0], lng ∈ [-58.7, -57.8]. Resultados de Nominatim fuera de esa caja se descartan (a veces matchea otra ciudad).
- Marcadores son `L.divIcon` con HTML custom (`<div class="marker-num">`), color de fondo = `Talonera.color`, color de texto auto (negro/blanco según luminosidad).
- Panel de progreso flotante arriba del mapa: "Geocodificando N/M... [X sin ubicar]".
- Botón "Recargar geocoding" arriba a la derecha: borra el cache y recarga (útil si cambiaron direcciones).
- `fitBounds` cada 5 puntos y al final para auto-centrar la vista.

#### Datos del modelo usados
- `Comprador.direccion` (String). Si está vacío, el socio NO aparece en el mapa.
- `Talonera.color` (picker UI). `Talonera.tipo == "COMUN"` (las CONTADO son pool, no representan boletas físicas).
- `Boleta.numero_principal` formateado con `Talonera.num_digitos` (default 4 para COMUN).

#### Nota técnica — mount stale del sandbox bash
- Volvió a ocurrir tras el Edit del file tool en `compradores.py`. El mount mostraba 1210 líneas (versión vieja, mtime del 18/05) mientras que el archivo en disco vía Read tool tenía 1306 líneas con los endpoints nuevos.
- Confirmado patrón: file tool es autoritativo, el mount bash puede quedarse colgado en archivos preexistentes. Archivos nuevos (como `compradores_mapa.html`) sí sincronizan.
- Validación: aislar el bloque nuevo en `/tmp`, hacer `ast.parse` ahí + parsear el template con Jinja2 (`env.get_template()`). Ambas pasaron OK.

#### Pendiente
- Hacer `git push` desde PowerShell para que Railway tome los cambios.
- Probar en producción: muchas direcciones de CdelU vienen con formato variable (abreviaturas, calles sin número, etc.). Si Nominatim falla con varias, evaluar agregar geocoding server-side cacheado en DB (tabla `geocode_cache(direccion PK, lat, lng, last_try)`).

---

### Sesión 23/05/2026 (cont. 3) — Mapa de Socios v2: cache server-side + filtros por pata y vendedor

#### Cambios

**1. Cache de geocoding compartido en DB (en vez de localStorage)**
- Nuevo modelo `GeocodeCache` en `app/models.py`:
  ```python
  class GeocodeCache(Base):
      __tablename__ = "geocode_cache"
      direccion = Column(String, primary_key=True)   # UPPER + trim + 1 espacio
      lat       = Column(Float, nullable=True)        # NULL = no ubicable
      lng       = Column(Float, nullable=True)
      intentos  = Column(Integer, default=1)
      last_try  = Column(DateTime, server_default=func.now())
  ```
- Se crea sola vía `Base.metadata.create_all(bind=engine)` (línea 11 de main.py). No requiere migración manual.
- Helper `_norm_direccion(s)` en `compradores.py`: UPPER + trim + colapso de espacios. Misma normalización en server y cliente.

**2. Endpoints nuevos en `app/routers/compradores.py`**
- `GET /compradores/mapa-data` actualizado:
  - Hace JOIN con `Vendedor` (`outerjoin`) para incluir `vendedor_id`/`vendedor_nombre` por punto.
  - Pre-carga TODO `GeocodeCache` en un solo SELECT → `cache_map[direccion] = (lat, lng)`.
  - Cada punto trae `lat`/`lng` ya cargados si están en cache, sino `None`.
  - Flag `ya_intentado` = True si la dirección está en cache_map (con `(None, None)` = fallo previo). El cliente NO reintenta esos.
  - Devuelve `vendedores: [{id, nombre}]` ordenados alfabéticamente para el select del filtro.
  - Devuelve `total_cacheadas` y `total_pendientes` para debug.
- `POST /compradores/mapa-geocode-save`:
  - Body JSON: `{direccion, lat, lng}` (lat/lng pueden ser null = no ubicable).
  - Normaliza la dirección, valida bbox CdelU, upsert en GeocodeCache.
  - Cualquier usuario logueado con permiso `ver` puede grabar coords (no admin) — para que cada visita ayude a llenar el cache.
- `POST /compradores/mapa-geocode-reset` (admin only):
  - Borra todo `GeocodeCache`. Útil si cambiaron muchas direcciones o el cache quedó sucio.

**3. UI con filtros — `app/templates/compradores_mapa.html`**
- Tarjeta de filtros arriba del mapa, dos columnas:
  - **Patas**: chips clickables con borde del color de la pata + `<span>` punto del color + label "X0/X1/X2". Click → toggle off/on (clase `.off` con `opacity:.35` y `text-decoration:line-through`). Estado en `patasOff` (Set).
  - **Vendedor**: `<select>` con `— Todos —` + vendedores del JOIN. Estado en `vendedorFiltro` (string).
- Función `aplicarFiltros()`: itera todos los markers, `okPata = !patasOff.has(pt.pata)` && `okVend = !vendedorFiltro || String(pt.vendedor_id) === vendedorFiltro`. Add/remove del map y `fitBounds` a los visibles.
- Contador "Mostrando X de N ubicaciones" debajo de los filtros.
- Popup ahora muestra el vendedor: `<i class="bi bi-person-badge"></i> Vendedor: NOMBRE`.

**4. Flujo del cliente**
- Al cargar `/compradores/mapa`:
  1. Fetch `/mapa-data` → recibe puntos con lat/lng pre-cargados desde cache server.
  2. Para cada punto con coords: dibuja marker, agrega a `markers[]`.
  3. Para cada punto sin coords y `!ya_intentado`: queda en `pendientes`.
  4. `aplicarFiltros()` para mostrar contadores y bounds.
  5. Si hay pendientes: geocodifica uno por uno con Nominatim (1.1 seg sleep), POSTea cada resultado a `/mapa-geocode-save` (incluyendo null/null para fallos para no reintentar).
- Botón "Reset cache server" arriba a la derecha SOLO visible para admin (`{% if user.is_admin %}` en template).

#### Decisiones de UX
- Cache server >>> localStorage: primera persona "paga" el costo de geocoding, todos los demás ven el mapa al instante en cualquier dispositivo.
- Cuando una dirección no se ubica, se guarda con `lat=null/lng=null` para NO reintentar (el campo `ya_intentado` evita el ciclo).
- Bbox sanity check sigue aplicando en el endpoint `save` (rechaza coords fuera de CdelU).
- Los chips de pata mantienen el color del Talonera.color (consistente con tabs de Socios y dashboard).

#### Archivos modificados
- `app/models.py`: agregada clase `GeocodeCache` (al final, después de `GastoContabilidad`).
- `app/routers/compradores.py`: helper `_norm_direccion`, `/mapa-data` ampliado, nuevos `/mapa-geocode-save` y `/mapa-geocode-reset`.
- `app/templates/compradores_mapa.html`: reescrito con filtros + integración server-cache (≈380 líneas).

#### Pendiente
- Git push a Railway. `Base.metadata.create_all` crea `geocode_cache` automáticamente al levantar.
- Probar con el dataset real: ver cuántas direcciones se ubican OK con Nominatim. Si muchas fallan, considerar agregar autocompletado de calles típicas de CdelU.

#### Nota — mount stale del sandbox (TERCERA o CUARTA vez en pocos días)
- Los archivos modificados con Edit/Write del file tool no se reflejan en el mount bash, pero el archivo en disco está bien (confirmado con Read tool sucesivos).
- En esta sesión: compradores.py mostraba 1210 líneas en bash y 1413 en Read tool; models.py 16290 bytes vs 358 líneas; template 9923 vs 380 líneas.
- Workaround: validar bloques nuevos en isolación con `ast.parse` / Jinja `env.get_template` desde /tmp.
- En producción (Windows + uvicorn) los cambios sí se aplican porque el host file system es la fuente, no el mount bash.

---

### Sesión 23/05/2026 (cont. 4) — Mapa de Socios v3: colores 100% desde Talonera + panel "No ubicadas"

#### 1) Eliminada paleta hardcoded — el mapa toma SOLO el color de Talonera
- **Motivación:** la paleta default que tenía hardcoded (PATA 1=azul, PATA 2=verde, etc.) pintaba mal las taloneras cuyo color real es distinto. Sergio dijo "X1 es gris" y "X= es azul y blanco" — esos colores reales viven en `Talonera.color` (picker en sección Taloneras), no en una paleta inventada.
- **Fix en `app/routers/compradores.py` `/mapa-data`:**
  ```python
  SIN_COLOR_FALLBACK = "#9e9e9e"   # gris neutro
  ...
  color = (t.color or "").strip().lower()
  if not color or color in ("#ffffff", "#fff", "white"):
      color = SIN_COLOR_FALLBACK
  ```
- Comportamiento: gris neutro `#9e9e9e` es señal visual de "esta talonera no tiene color seteado, andá a Taloneras a configurarlo". Cualquier otro color = lo que está en DB.
- **No hay cache de colores**: cada request a `/mapa-data` releva `Talonera.color` actualizado, así que cambiar el color en Taloneras se refleja al recargar el mapa.
- Limitación: una talonera no puede tener un color "bicolor" tipo azul+blanco en el marcador (es un solo color de fondo). El texto del número se renderiza en blanco o negro automáticamente según luminosidad del fondo.

#### 2) Panel "No ubicadas" con lista y botón Reintentar

**Backend — nuevos endpoints en `compradores.py`:**
- `GET /compradores/mapa-no-ubicadas` → devuelve lista de socios cuya dirección está en `GeocodeCache` con `lat IS NULL` (= fallaron previamente). Cada item: `{comprador_id, apellido_nombre, direccion, numero, pata, pata_label, vendedor_nombre}`. JOIN con Talonera y Vendedor, filtrado a taloneras COMUN.
- `POST /compradores/mapa-geocode-retry` → borra del `GeocodeCache` SOLO las entradas con `lat IS NULL` (no toca las ubicadas OK). Cualquier usuario logueado puede ejecutar (no requiere admin). La próxima carga del mapa va a reintentar esas direcciones con Nominatim.
- Diferencia con `/mapa-geocode-reset` (admin): reset borra TODO, retry borra solo los fallos.

**Frontend — `compradores_mapa.html`:**
- Botón naranja "No ubicadas (N)" en la fila de filtros, oculto si N=0. Click → toggle del panel (Bootstrap Collapse via `getOrCreateInstance`).
- Panel `<div class="collapse" id="panelNoUbicadas">` con:
  - Header: ícono warning + botón "Reintentar geocoding".
  - Tabla: N° boleta / Pata / Socio / Dirección (resaltada) / Vendedor / botón lápiz para editar el socio.
  - Footer informativo: "Si corregís la dirección del socio, apretá Reintentar y recargá".
- Función `cargarNoUbicadas()` fetcha `/mapa-no-ubicadas` y renderiza la tabla. Se llama:
  1. Cuando termina la primera fase (puntos desde cache server).
  2. Cuando termina el loop de geocoding cliente (refresca con los fallos nuevos).
- Botón "Reintentar geocoding" llama a `/mapa-geocode-retry` con `POST`, muestra cuántas se borraron y recarga la página.

#### Flujo típico para Sergio
1. Abre el mapa, ve "No ubicadas (5)" naranja.
2. Click → ve tabla con 5 socios, copia la dirección del primero.
3. Click en lápiz → edita el socio, corrige la dirección (ej: agrega altura), guarda.
4. Vuelve al mapa, panel "No ubicadas" sigue mostrando los 5.
5. Click en "Reintentar geocoding" dentro del panel → confirma → se borran las 5 del cache → se recarga.
6. El mapa vuelve a geocodificar las 5 con Nominatim (1.1 seg c/u = ~6 seg). Las que ahora matchean salen ubicadas; las que siguen fallando vuelven al panel.

#### Archivos modificados
- `app/routers/compradores.py`: helper `_norm_direccion`, `/mapa-data` (color cleanup), `/mapa-no-ubicadas` (GET), `/mapa-geocode-retry` (POST). Reset queda igual.
- `app/templates/compradores_mapa.html`: panel `#panelNoUbicadas`, botón `#btnNoUbicadas`, función `cargarNoUbicadas()`, handlers de toggle/retry.

#### Pendiente
- Git push a Railway.
- Probar con las direcciones reales — anotar cuántas falla Nominatim para CdelU. Si son muchas, considerar:
  - Reglas heurísticas de normalización (ej: "BV." → "BOULEVARD", "AV." → "AVENIDA").
  - Geocoder alternativo (Mapbox, Photon de Komoot) si Nominatim no rinde.
  - Permitir coordenadas manuales (clickear en el mapa → asignar lat/lng a un socio sin ubicar).

---

### Sesión 23/05/2026 (cont. 5) — Mapa de Socios v4: edición inline de no ubicadas + reset selectivo del cache

#### Edición inline en el panel "No ubicadas"
- En cada fila del panel hay botón lápiz. Click → la celda Dirección se reemplaza por `<input class="inp-dir">` y los botones de acción cambian a ✅ Guardar / ❌ Cancelar.
- Atajos de teclado: **Enter** guarda, **Escape** cancela.
- Tras guardar (POST exitoso):
  1. El cliente espera 1.1 s y consulta Nominatim con la dirección nueva (respeta el rate limit aunque sea una sola consulta).
  2. Si no matchea, reintenta sin números (caso típico "CALLE 1234" → "CALLE").
  3. Si encuentra coords dentro del bbox CdelU: crea el marker, lo agrega al `markers[]`, llama `aplicarFiltros()`, postea las coords al cache server, **anima la fila con `.table-success`** y la elimina del panel tras 700 ms.
  4. Si sigue sin ubicar: guarda como fallido en el cache (lat/lng null) y muestra badge rojo "sigue sin ubicar" en la fila para que se pruebe otra variante.
- Función `actualizarContador()`: tras eliminar filas exitosas, refresca el contador del botón "No ubicadas (N)" y, si N=0, oculta el botón y colapsa el panel automáticamente.

#### Endpoint nuevo — `POST /compradores/{cid}/actualizar-direccion-mapa`
- Acepta body JSON `{direccion}` o form data. Normaliza a UPPER+trim+1-espacio. Requiere permiso `editar`.
- Actualiza `Comprador.direccion` con la nueva (también en UPPER).
- **Reset selectivo del `GeocodeCache`:**
  - Siempre borra la entrada con la dirección vieja normalizada (sea fallida o exitosa).
  - Siempre borra la entrada con la dirección nueva normalizada (también sea fallida o exitosa), salvo que sea la misma que la vieja.
  - Esto fuerza una regeocodificación fresca a Nominatim — si Sergio editó es porque algo cambió y no queremos usar coords viejas.
- Devuelve `{ok, direccion, direccion_norm, cache_borradas}`.

#### Por qué reset selectivo y no global
- Las entradas exitosas de otras direcciones (otros socios con direcciones distintas) NO se tocan — siguen sirviendo a todos.
- Si alguien sí necesita reset total, está el botón "Reset cache server" (admin) que llama a `POST /mapa-geocode-reset`.
- Si solo quiere reintentar los fallos sin tocar exitosas, está el botón "Reintentar geocoding" del panel (cualquier usuario) que llama a `POST /mapa-geocode-retry` y borra solo las entradas con `lat IS NULL`.

#### Archivos modificados
- `app/routers/compradores.py`:
  - Nuevo `POST /{cid}/actualizar-direccion-mapa`.
  - Lógica de reset selectivo: borra vieja siempre + nueva siempre (a menos que coincidan).
- `app/templates/compradores_mapa.html`:
  - `cargarNoUbicadas()` ahora arma `tr.dataset.cid`, `tr.dataset.direccion`, y un objeto `pt` por fila enriquecido con `color`/`zona`/`vendedor_id` del `datos.puntos`.
  - Funciones `entrarEdicion(tr, pt)`, `salirEdicion(tr, pt, original, mostrar)`, `guardarYReubicar(tr, pt)` y `actualizarContador()`.

#### Pendiente
- Git push a Railway.
- Si la dirección nueva editada coincide con la dirección vieja exactamente (normalizada), no se borra dos veces — el endpoint chequea `nueva_norm != direccion_vieja_norm` antes del segundo delete.
- Si una dirección la comparten varios socios, al editarla en uno se borra del cache para todos. La próxima carga del mapa la regeocodifica una sola vez (Nominatim) y la cachea de nuevo. Comportamiento aceptable.

---
*Última actualización: 23 de mayo de 2026 (cont. 5) — Mapa de Socios v4: edición inline de dirección en el panel "No ubicadas" (Enter/Escape, anima fila al ubicar OK), reset selectivo del GeocodeCache al editar (borra vieja + nueva sin importar estado), endpoint nuevo POST /{cid}/actualizar-direccion-mapa.*

---

### Sesión 23/05/2026 (cont. 6) — Mapa de Socios v5: filtro cobrador + cambio cobrador desde popup + volver al mapa desde edición

#### 1) Botón "Volver" desde comprador_editar.html preserva el origen

- El link "Editar socio" en los popups del mapa ahora incluye `?from=mapa` en la URL (`/compradores/{id}/editar?from=mapa`).
- `comprador_editar.html` detecta ese param con JS al cargar:
  - Cambia el href del botón "Volver" a `/compradores/mapa`.
  - Setea un hidden input `from_page=mapa` en el formulario.
- El POST `/compradores/{id}/editar` acepta `from_page: Optional[str] = Form(None)`. Si vale `"mapa"`, el redirect post-save va a `/compradores/mapa` en vez de `/compradores/`.

#### 2) Filtro por cobrador en el mapa

- **Backend (`compradores.py` — `mapa_data`):**
  - Agrega `outerjoin` con `models.Cobrador` (vía `Boleta.cobrador_id`).
  - Cada punto incluye `cobrador_id` y `cobrador_nombre`.
  - La respuesta JSON incluye `cobradores: [{id, nombre}]` ordenados alfabéticamente (igual que `vendedores`).
- **Frontend (`compradores_mapa.html`):**
  - Nuevo `<select id="filtroCobrador">` junto al de vendedor (ahora la fila de filtros tiene 4 columnas: patas en `col-md-8` → `col-md-8`, vendedor en `col-md-2`, cobrador en `col-md-2`).
  - Variable `cobradorFiltro = ""` en el estado global.
  - Función `renderCobradores()` que llena el select y agrega listener de `change`.
  - `aplicarFiltros()` incluye `okCob = !cobradorFiltro || String(m.pt.cobrador_id) === cobradorFiltro`.

#### 3) Cambio de cobrador desde el popup del mapa

- Función `buildPopupHtml(pt)` separada del `crearMarcador`: construye el HTML del popup (permite reconstruirlo tras un cambio). Contiene:
  - Número, pata, nombre, dirección, zona, vendedor.
  - Select `sel-cobrador-popup` con todos los cobradores, con el actual preseleccionado.
  - Botón `btn-cobrador-guardar` con ícono ✓.
  - Div `cobrador-popup-status` para feedback (spinner / ✓ / error).
  - Link "Editar socio" con `?from=mapa`.
- `crearMarcador` usa `buildPopupHtml(pt)` con `maxWidth: 300`.
- Handler `map.on('popupopen', ...)`:
  1. Localiza `.btn-cobrador-guardar` y `.sel-cobrador-popup` dentro del popup abierto.
  2. Al click: POST a `/compradores/asignar-cobrador` con `comprador_id` y `cobrador_id` (FormData).
  3. Si OK: actualiza `mrkEntry.pt.cobrador_id` y `cobrador_nombre`, llama `mrkEntry.marker.setPopupContent(buildPopupHtml(pt))` tras 800 ms para refrescar el popup. Si hay filtro de cobrador activo, llama `aplicarFiltros()` para reordenar la visibilidad.
  4. Si error: muestra mensaje y re-habilita el botón.

#### Archivos modificados
- `app/routers/compradores.py`:
  - `mapa_data`: join Cobrador, `cobrador_id`/`cobrador_nombre` en puntos y lista `cobradores` en respuesta.
  - `editar` POST: nuevo param `from_page: Optional[str] = Form(None)`, redirect condicional.
- `app/templates/comprador_editar.html`:
  - Botón Volver con `id="btnVolver"`.
  - Hidden `<input name="from_page" id="fromPageInput">` en el form.
  - Bloque JS `(function(){ const params = new URLSearchParams(...); if (from=mapa) { btnVolver.href = '/compradores/mapa'; fromPageInput.value = 'mapa'; } })();` al inicio del script.
- `app/templates/compradores_mapa.html`:
  - Filtro cobrador en UI.
  - `cobradorFiltro` en estado global.
  - `renderCobradores()`.
  - `buildPopupHtml(pt)` separado.
  - `crearMarcador` usa `buildPopupHtml`.
  - `map.on('popupopen', ...)` para cambio de cobrador.
  - `renderCobradores()` llamado en `cargar()`.

---
*Última actualización: 23 de mayo de 2026 (cont. 6) — Mapa v5: volver al mapa tras edición (from=mapa), filtro por cobrador, cambio de cobrador desde popup con AJAX.*
