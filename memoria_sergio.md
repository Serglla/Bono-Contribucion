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
- **Sandbox bash mount stale**: a veces el mount /sessions/.../mnt/ no sincroniza con los Edit del file tool; confiar en lo que devuelve Read y verificar contenido con grep si bash da errores raros

---

### Modelo de datos — resumen de relaciones clave

#### Taloneras y boletas
- Cada talonera tiene `num_series` y `offset_series` (separación entre series)
- **PATA 1:** 3 series, offset 1501 → ej: 0001 / 1502 / 3003
- **PATA 2:** 6 series, offset 350 → ej: 4503 / 4853 / 5203 / 5553 / 5903 / 6253
- Función `calcular_numeros(numero_principal, num_series, offset)` en `app/routers/taloneras.py`
- Condiciones de boleta: SIN_VENDER → CAJA → VENDIDO (también BAJA, EN_COBRANZA)
  - BAJA y EN_COBRANZA solo vienen de cobranza, los vendedores no usan BAJA
- `Talonera.num_cuotas` (Integer, default 12, **deferred**) — cantidad de cuotas mensuales de la talonera

#### Zona-Cobrador (muchos-a-muchos, desde 02/05/2026)
- Tabla `zona_cobradores`: zona_id PK, cobrador_id PK, asignado_en (DateTime)
- Una zona puede tener múltiples cobradores simultáneamente
- `Zona.cobrador_id` legacy: columna en DB ignorada por ORM; datos migrados a zona_cobradores en startup
- `Zona.cobrador_id` (property) → ID del último cobrador (mayor asignado_en)
- `Zona.cobrador` (property) → objeto Cobrador del último asignado
- **CRÍTICO:** ZonaCobrador debe definirse ANTES que Zona y Cobrador en models.py

#### Zona-Vendedor
- `Zona.vendedor_id` FK a vendedores (nullable) — una zona = un vendedor
- Se vincula al crear el primer comprador con esa zona

---

### Módulo Vendedores — actualizado 07/05/2026

#### Modelos nuevos/modificados
- `Vendedor.es_jefe_equipo` (Boolean, default False) — solo un jefe activo a la vez
- `LiquidacionVendedor` (tabla `liquidaciones_vendedor`): registra liquidación de comisión al vendedor
  - Campos: vendedor_id, fecha, cuotas_vendidas, **cuota_1_total**, monto_cuotas, comision_cuotas_pct, comision_cuotas,
    contados_vendidos, monto_contados, comision_contados_pct (default 30%), comision_contados, total_comision, observacion
  - **Posición en models.py:** definida ENTRE Vendedor y Cobrador (relación bidireccional)
- `Boleta.liquidacion_vendedor_id` (FK a liquidaciones_vendedor, nullable) — enlaza boleta con su liquidación
- `Boleta.liquidacion_vendedor` — relación ORM a LiquidacionVendedor
- `EntregaCaja.vendedor_id` (FK a vendedores, nullable) — a qué vendedor se entregó

#### Flujo correcto de boletas por vendedor (CRÍTICO — confirmado con Sergio)
```
SIN_VENDER
  → [Entrega a caja] → CAJA (sin liquidacion_vendedor_id) — boleta en mano del vendedor
  → [Liquidar vendedor] → CAJA (con liquidacion_vendedor_id) — liquidado, pendiente cargar comprador
  → [Cargar comprador en sistema] → VENDIDO — comprador registrado
```
- La liquidación al vendedor se hace sobre boletas en CAJA (sin liq_id) — NO sobre VENDIDO
- Las boletas conservan condición CAJA después de liquidar, solo pasan a VENDIDO cuando se carga el comprador
- Los vendedores NO tienen boletas en BAJA — la baja viene solo de cobranza

#### Modelo de comisión del vendedor (confirmado con Sergio)
- **Cuota 1**: el vendedor SIEMPRE se queda con la primera cuota (= valor_cuota por boleta). Es su comisión de venta. Se registra en `cuota_1_total`.
- **Comisión adicional cuotas**: % sobre el monto de cuotas (default 5%). Se paga sobre las boletas vendidas en cuotas (sin contado).
- **Comisión contado**: % sobre el valor TOTAL de la talonera = num_cuotas × valor_cuota (default 30%). Se paga por cada boleta vendida al contado. Ej: PATA 1 = 12 × $15.000 = $180.000 → 30% ≈ $50.000–$54.000.
- **Total a pagar** = cuota_1_total + comision_cuotas + comision_contados

#### Modal de liquidación — selección manual
- Las boletas pendientes se muestran individualmente agrupadas por PATA, con checkboxes clickeables
- Botones "Todo / Ninguno" por PATA + "Seleccionar todo / Deseleccionar todo" global
- Cálculo en tiempo real: cuota 1 + comisión adicional + comisión contado
- El botón "Confirmar" queda deshabilitado hasta seleccionar al menos una boleta
- Backend: `POST /{vid}/liquidar` acepta lista de `boleta_ids` via `request.form().getlist("boleta_ids")`

#### Router vendedores.py — endpoints clave
- `_stats_bulk(db)`: un solo SQL con GROUP BY vendedor+condicion. Dict con claves `caja`, `vendido`, `baja` (SIEMPRE las tres)
- `GET /vendedores/`: lista vendedores con stats (caja, baja, vendido), jefe_equipo
- `GET /vendedores/{vid}/detalle`: boletas agrupadas por PATA + `pendientes_json` + entregas_vendedor + grupos_talonera + grupos_contado + nombres_contado + vendedores_all
- `POST /vendedores/{vid}/liquidar`: acepta boleta_ids seleccionados, nuevo modelo de comisión
- `POST /vendedores/{vid}/toggle-jefe`: marca jefe (primero resetea todos, luego activa el nuevo)
- `POST /vendedores/entrega-caja`: SIN_VENDER → CAJA + REASIGNAR entre vendedores en CAJA (sin liquidar)
- `POST /vendedores/entrega-caja/{id}/editar` y `eliminar`: gestión del historial de entregas

#### Detalle del vendedor — orden y CONTADO en pool (07/05/2026)
- **Orden jerárquico de PATAs en `vendedor_detalle`** (función `_pata_sort_key` en `routers/vendedores.py`):
  1. PATA con número (PATA 1, 2, 3, 4, 5, 6) — ordenadas numéricamente
  2. Otras COMUN sin número (ej. VOLAS) — alfabéticamente
  3. CONTADO al final, primero "CONTADO" sin número y después "CONTADO 2 VECES" / "CONTADO N VECES"
- **CONTADO pool en pantalla detalle**:
  - Para taloneras tipo CONTADO el endpoint suma a `patas` los números entregados al vendedor (vía EntregaCaja) que aún NO fueron asignados a ninguna boleta (se restan los números que ya están en `numero_especial` o `numero_especial_2` de cualquier boleta).
  - Match de talonera_nombre vs talonera CONTADO con `.strip().lower()` para tolerar variantes de mayúsculas/espacios.
  - Estos números pool aparecen como badge azul + ★ pero NO entran en `pendientes_json` ni en `pendientes_count` (no son liquidables, no tienen valor_cuota propio).
- **Flag `contado` en cada boleta**: `b.numero_especial is not None or b.numero_especial_2 is not None` (cualquiera de los dos slots).

#### Entrega a Caja — actualizado 06/05/2026 (noche)
**UI movida al detalle del vendedor (cambio importante):**
- La sección global de Entrega a Caja en `/vendedores/` SE QUITÓ por completo (form + tabla + modales).
  En su lugar hay un aviso: "Hacé doble clic sobre la fila del vendedor para entregarle boletas".
- En `/vendedores/{vid}/detalle` ahora hay **dos botones en orden de flujo**:
  1. **Entregar a Caja** (rojo) — abre modal con PATA / Desde / Hasta. El `vendedor_id` se manda fijo (el del detalle).
  2. **Liquidar vendedor** (verde) — sin cambios.
  Separados visualmente con una flecha `→`.
- Tabla **"Entregas a caja recibidas"** filtrada por ese vendedor (con editar/eliminar inline).

**Comportamiento dual del backend (sin cambios):**
1. Boletas en SIN_VENDER → pasan a CAJA con el vendedor elegido (cuenta como `nuevas`)
2. Boletas en CAJA sin `liquidacion_vendedor_id` y con vendedor distinto → reasigna al nuevo vendedor (cuenta como `reasignadas`). NO toca las liquidadas.

**No ensucia historial:** si `total = nuevas + reasignadas == 0`, hace `db.rollback()` y NO crea fila en EntregaCaja.

Para taloneras CONTADO: `es_contado = True`, no toca boletas, solo registra el rango entregado al vendedor (la asignación real ocurre en ETAPA 2 al cargar al comprador).

#### Templates vendedores
- `vendedores.html`: tabla limpia, doble-click → detalle, badge jefe, stats por vendor.
- `vendedor_detalle.html`: 3 tarjetas resumen + 2 botones de acción (Entregar a Caja → Liquidar) + leyenda + secciones por PATA + tabla "Entregas a caja recibidas" + historial de liquidaciones + 2 modales (Entregar a Caja, Liquidar)

#### 3 estados de boleta en el detalle de vendedor (colores)
- 🔵 Azul claro (`#e7f1ff`): CAJA sin liq_id — en mano del vendedor, se puede liquidar
- 🟢 Verde (`#d1e7dd`): CAJA con liq_id — liquidado, pendiente que se cargue el comprador
- 🔵 Azul opaco (bg-primary opacity .55): VENDIDO — comprador ya registrado en sistema

#### Migraciones en main.py (startup) — todas en try/except
- `es_jefe_equipo` en vendedores (BOOLEAN DEFAULT FALSE)
- `vendedor_id` en entregas_caja (INTEGER REFERENCES vendedores)
- CREATE TABLE `liquidaciones_vendedor` (si no existe)
- `liquidacion_vendedor_id` en boletas (INTEGER REFERENCES liquidaciones_vendedor)
- `num_cuotas` en taloneras (INTEGER DEFAULT 12) — usa IF NOT EXISTS en PostgreSQL
- `cuota_1_total` en liquidaciones_vendedor (REAL DEFAULT 0.0) — usa IF NOT EXISTS en PostgreSQL
- `numero_especial_2` y `talonera_especial_2_id` en boletas (07/05/2026, ETAPA 2)
- **CRÍTICO**: todas las migraciones deben estar dentro de try/except. La línea `inspector.get_columns()` fuera de try/except crashea todo el startup si falla.

---

### Módulo Cobranza (/cobranza/) — implementado 01-02/05/2026
- Página principal: selector mes/año, tarjetas por cobrador
- Planilla: template standalone hoja A4, 3 columnas, 4 bloques de 10 filas (120 slots)
- Cuotas anticipadas: celda negra + X blanca (cobrador no las gestiona)
- Tabs: Planillas | Emplantillado | Liquidación
- Modelos: Planilla (cobrador_id, numero, mes, anio, comision_pct), Liquidacion, LiquidacionDetalle

---

### Talonera Especial CONTADO — ETAPA 1 + ETAPA 2 (07/05/2026)

**Concepto:** sorteos extra que se regalan al socio según cómo paga la talonera. Pool de números de una talonera tipo CONTADO, asignados a boletas comunes.

**Modelo Boleta — dos slots:**
- `Talonera.tipo` (String, default "COMUN") — "COMUN" o "CONTADO"
- **Slot 1** = sorteo "CONTADO":
  - `Boleta.numero_especial` (Integer, nullable, indexado)
  - `Boleta.talonera_especial_id` (FK a taloneras, nullable)
- **Slot 2** = sorteo "CONTADO 2 VECES":
  - `Boleta.numero_especial_2` (Integer, nullable, indexado, **deferred**)
  - `Boleta.talonera_especial_2_id` (FK a taloneras, nullable, **deferred**)
- `Talonera.boletas` con `foreign_keys="Boleta.talonera_id"` (porque hay 3 FK a taloneras)
- `Boleta.talonera_especial` y `Boleta.talonera_especial_2` con `foreign_keys` explícito

**Reglas de negocio (confirmadas con Sergio):**
- Talonera **CONTADO** = pool de números para sorteo extra cuando paga al contado total (1 sola vez).
- Talonera **CONTADO 2 VECES** = pool de números para sorteo extra cuando paga en 2 cuotas.
- Modalidades en `comprador_editar`:
  - **En cuotas (12 cuotas)**: sin extras, ambos slots vacíos.
  - **1 sólo pago (al contado total)**: AMBOS extras → asigna slot 1 (CONTADO) + slot 2 (CONTADO 2 VECES).
  - **2 pagos**: solo slot 2 (CONTADO 2 VECES); slot 1 vacío.
- El vendedor cobra el contado y entrega físicamente los números al socio. Después se registran en el sistema al cargar al comprador (asignación manual eligiendo del pool del vendedor).

**ETAPA 1 (05/05/2026) — base implementada:**
- Modelo + ABM de talonera CONTADO en `/taloneras/` (botón "Nueva Talonera Especial").
- EntregaCaja registra rangos de números CONTADO entregados al vendedor (sin tocar boletas — `es_contado=True`).
- Detalle vendedor muestra los números pool entregados pero no asignados (con sort jerárquico CONTADO antes que CONTADO 2 VECES).
- TipoSorteo.CONTADO ya existe en el enum (sin usar todavía en sorteos).

**ETAPA 2 (07/05/2026) — asignación a boletas:**
- **Endpoint nuevo**: `GET /compradores/boleta/{boleta_id}/contado-disponibles`
  - Devuelve para el vendedor de la boleta: `taloneras_contado` con `numeros_libres` por talonera (entregados al vendedor − asignados a otras boletas) + `current` con la asignación actual de esta boleta.
  - Cada talonera tiene un `rol` inferido por nombre: "CONTADO" / "CONTADO_2" / "OTRO". Sirve para que la UI sepa qué talonera mostrar en cada slot.
  - Si la boleta YA tiene un número asignado para una talonera, ese número se incluye en `numeros_libres` aunque la talonera no esté en el pool del vendedor (para que se vea preseleccionado).
- **POST `/compradores/{id}/editar`** procesa por boleta:
  - `modalidad_<bid>` ∈ {"cuotas", "1pago", "2pagos"}
  - `te_<bid>` / `ne_<bid>` (slot 1 — talonera_especial_id / numero_especial)
  - `te2_<bid>` / `ne2_<bid>` (slot 2 — talonera_especial_2_id / numero_especial_2)
  - Validación silenciosa: `_esta_libre(num, talonera_id, except_boleta_id)` — chequea que el número no esté ya asignado a otra boleta en ningún slot para esa talonera.
- **UI `comprador_editar.html`**:
  - Bajo cada boleta: 3 radios de modalidad + dos selectores condicionales (talonera + número). Pre-selecciona modalidad y números si la boleta ya tenía algo cargado (modalidad inferida: ambos slots → 1pago, solo slot 2 → 2pagos, ninguno → cuotas).
  - JS hace fetch al endpoint nuevo, popula los selects de talonera con los CONTADO disponibles del vendedor, y al elegir talonera popula los números libres. Si la boleta tiene un número actual no incluido en libres (porque está reservado para sí misma), igual lo agrega para que se vea seleccionado.
- **Vendedor detalle (`vendedores.py`)**: la query de "asignados" ahora cubre ambos slots (slot 1 y slot 2) — el pool en pantalla refleja correctamente lo entregado a un socio.

**Pendiente — ETAPA 3 (sorteo CONTADO):**
- En `routers/sorteos.py` ver_ganadores: cuando tipo == CONTADO, cruzar contra `Boleta.numero_especial` y/o `Boleta.numero_especial_2` según la talonera del sorteo (puede ser sorteo de CONTADO o de CONTADO 2 VECES).

---

### Otras funcionalidades implementadas
- Login/logout JWT (cookies)
- ABM Compradores, Vendedores, Cobradores, Zonas, Taloneras, Boletas
- Generación de boletas por rango (sin duplicar)
- Auto-asignación de cobrador al guardar comprador (por zona)
- Exportación Excel socios: GET /compradores/exportar (openpyxl==3.1.2)
- Dashboard /reportes/: cards + tablas por talonera, zona, top vendedores/cobradores
- Módulo Sorteos: ABM Tómbola Nocturna Entre Ríos (SEMANAL/MENSUAL/FINAL/CONTADO)
  - Scraper no funciona (sitio usa JS) — carga manual via modal
  - Módulo Ganadores: cruza 4c/3c/2c con exclusión
- Color por PATA: Talonera.color, picker en UI, fila compradores coloreada ({color}40)

---

### Preferencias de UI consolidadas
- Tablas: table-sm + font-size: 0.8rem + filas ~28px (compactas)
- Filtros inline por columna + sortable en todos los encabezados
- Fecha: almacena ISO en data-val, muestra dd/mm/yyyy
- Dirección: CSS text-transform: uppercase en la celda
- Sin botón eliminar en tablas — solo lápiz; el eliminar queda en la pantalla de edición
- Compradores: N° Boleta · Apellido y Nombre · Dirección · Zona · Fecha compra · Vendedor · Cobrador · Teléfono · Acciones
- Cobradores: badges bg-danger-subtle, checkboxes de zonas en grilla scrolleable (max-height 220px)
- **Vendedores**: la sección "Entrega a Caja" NO está en el listado global; vive en el detalle del vendedor (acceso por doble-click)

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 3**: módulo sorteo cruzando Boleta.numero_especial y numero_especial_2
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)
- **Liquidación vendedor**: pendiente configurar `num_cuotas` en cada talonera desde la UI de taloneras (por ahora default 12)

---
*Última actualización: 07 de mayo de 2026 — ETAPA 2 de talonera CONTADO implementada. Boleta tiene 2 slots de número especial (CONTADO + CONTADO 2 VECES). En `/compradores/{id}/editar` cada boleta tiene radio de modalidad (cuotas / 1 pago / 2 pagos) + selectores que cargan dinámicamente el pool del vendedor via `GET /compradores/boleta/{bid}/contado-disponibles`. Detalle de vendedor ordena PATAs jerárquicamente (PATA 1-6, otras COMUN, CONTADO, CONTADO 2 VECES) y descuenta del pool ambos slots ya asignados.*
