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
- **git HEAD.lock bloqueado en sandbox**: el sandbox no puede borrar `.git/HEAD.lock`. Sergio debe correrlo desde PowerShell: `del .git\HEAD.lock`. Siempre pedir a Sergio que lo haga antes del push cuando aparece este error.
- **Nuevos archivos (Write tool)** no truncan — solo los Edits encadenados en archivos existentes grandes. Para templates nuevos usar `cat > archivo << 'EOF'` en bash.

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
- **Filtros de boletas activas** (cobranza.py): usan `condicion != BAJA` desde 14/05/2026 (antes `IN [VENDIDO, EN_COBRANZA]` — obsoleto)

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
- **Filtro Jinja `pesos`** (22/05/2026): en `templates_config.py`, formatea Float como `$1.234.567` con puntos de miles.

---

### Sesión 22/05/2026 — Auto-completar vendedor desde liquidación + Sección Contabilidad

#### 1) Auto-completar vendedor al buscar boleta en Nuevo Socio

**Contexto:** en la ficha "Nuevo Socio", el campo Vendedor debe surgir automáticamente cuando se ingresa el número de boleta, porque ese número fue liquidado a un vendedor específico.

**Backend (`app/routers/taloneras.py` — `buscar_boleta_global`):**
- Endpoint `GET /taloneras/buscar-boleta/{numero}` ahora devuelve también `vendedor_id` y `vendedor_nombre`.
- Prioridad: si la boleta tiene `liquidacion_vendedor_id` → usa ese vendedor. Si no, usa `boleta.vendedor_id` como fallback.

**Frontend (`app/templates/compradores.html`):**
- Nuevo span `#vendedorBoletaBadge` con ícono ⚡ verde junto al label "Vendedor".
- Al buscar boleta exitosamente: si `b.vendedor_id` existe → se desbloquea el select (incluso si la zona lo tenía fijado), se asigna el valor y aparece el badge verde con el nombre.
- **La liquidación tiene prioridad máxima — pisa a la zona.**
- Al resetear el comprador (post-guardado): el badge se oculta y el select vuelve al valor de memoria.

#### 2) Sección Contabilidad (`/contabilidad/`)

**Acceso:** solo `is_admin`. El nombre de usuario `admin` en el navbar es ahora un `<a href="/contabilidad/">` (otros usuarios ven texto plano).

**Archivos nuevos:**
- `app/routers/contabilidad.py` — router con prefix `/contabilidad`
- `app/templates/contabilidad.html` — template con cards + tabs

**KPI Cards (2 filas):**
- Fila 1: Total recaudado | Total esperado | Falta cobrar | % Avance (progress bar)
- Fila 2: Comisiones vendedores | Comisiones cobradores | Ganancia neta institución

**Cálculos:**
- `total_recaudado` = `sum(Boleta.total_pagado)` para boletas con comprador, excluye BAJA
- `total_esperado` = `sum(cuotas_pactadas × talonera.valor_cuota)` por boleta
- `falta_cobrar` = `sum(max(0, pactadas - pagadas) × valor_cuota)` por boleta
- `total_com_vendedores` = `sum(LiquidacionVendedor.total_comision)`
- `total_com_cobradores` = `sum(Liquidacion.comision)` (liquidaciones de cobranza)
- `ganancia_neta` = recaudado − com_vendedores − com_cobradores

**Tabs:**
- **Por mes**: tabla con monto cobrado / comisión cobradores / neto por mes de planilla (desde `Liquidacion` → `Planilla.mes/anio`)
- **Vendedores**: acordeón por vendedor; subtabla por liquidación (fecha, mes, cuotas equiv, com. cuotas, com. contados, total)
- **Cobradores**: acordeón por cobrador; subtabla por planilla liquidada (fecha, mes, monto, comisión, neto)

**Registrado en `main.py`:** `from .routers import ... contabilidad` + `app.include_router(contabilidad.router)`

**Filtro `pesos` agregado a `templates_config.py`:**
```python
def _pesos(v):
    return "$" + f"{int(round(float(v or 0))):,}".replace(",", ".")
_env.filters["pesos"] = _pesos
```

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 3**: sorteo cruzando numero_especial y numero_especial_2
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Validar con Sergio: columnas "Taloneras" y "Vendidas" en dashboard Por Zona — ¿eliminar una?
- Posible extensión: aplicar `PATA N → XN` en todas las pantallas (filtro Jinja global)
- Validar multi-up extracto con datos reales
- **PATA 0 — probar en producción**: crear talonera real en Railway (num_series=2, valor_cuota=10000, num_cuotas=?). Verificar cuota 1 = $10.000 exacto, modal Liquidar con selecciones mixtas PATA 0 + PATA 1, reportes muestran enteros sin decimales raros.
- **Liquidación — probar en producción**: marcar 7 cuotas seguidas en mayo y verificar resumen MAYO=7. Probar bloqueo al saltar cuota (marcar cuota 9 sin 8 → alerta). Probar bloqueo al desmarcar fuera de orden.
- **Contabilidad**: probar en producción con datos reales de Railway. Verificar que los totales cuadran.

---
*Última actualización: 22 de mayo de 2026 — Auto-completar vendedor desde liquidación en Nuevo Socio + Sección Contabilidad completa (KPIs, por mes, vendedores, cobradores).*
