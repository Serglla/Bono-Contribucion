# Memoria sobre Sergio

> Este archivo sirve para que Claude recuerde información importante sobre Sergio entre sesiones.
> Compártelo al inicio de cada conversación para que Claude lo lea.

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
- **Ubicación:** `C:\Users\sergi\OneDrive\Escritorio\MeIA\bono-app\`
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
*Última actualización: 14 de mayo de 2026 (cont.) — Fix emplanillado (cobranza.py: filtros `condicion IN [VENDIDO, EN_COBRANZA]` → `!= BAJA` en 5 lugares, alineado con el badge 'En cobranza' de Socios). Script `rearmar_liquidaciones_10052026.py`: rearma todas las liquidaciones de vendedores al 10/05 agrupando por `vendedor_id`, modo reemplazo, criterio `comprador_id IS NOT NULL`. Aplicado en Railway: 53 boletas, 4 liquidaciones. Notas operativas: psycopg2-binary + DATABASE_PUBLIC_URL para correr scripts desde Windows.*
