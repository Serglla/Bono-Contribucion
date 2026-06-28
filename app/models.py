from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Enum, DateTime, Boolean
from sqlalchemy.orm import relationship, deferred
from sqlalchemy.sql import func
import enum
from datetime import datetime as _datetime
from .database import Base


class TipoSorteo(str, enum.Enum):
    SEMANAL = "SEMANAL"
    MENSUAL = "MENSUAL"
    CONTADO = "CONTADO"
    FINAL = "FINAL"


class CondicionBoleta(str, enum.Enum):
    VENDIDO = "VENDIDO"
    CAJA = "CAJA"
    BAJA = "BAJA"
    EN_COBRANZA = "EN_COBRANZA"
    SIN_VENDER = "SIN_VENDER"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    permissions = Column(String, nullable=True)  # JSON: {"seccion": {"ver": bool, "editar": bool}}
    created_at = Column(DateTime, server_default=func.now())


class ZonaCobrador(Base):
    """Tabla de asociacion zona <-> cobrador (muchos-a-muchos con timestamp).
    Una zona puede tener multiples cobradores activos al mismo tiempo.
    El 'precargado' al cargar un socio es el de asignado_en mas reciente.
    IMPORTANTE: debe definirse ANTES de Zona y Cobrador para que SQLAlchemy
    resuelva las referencias de string correctamente.
    """
    __tablename__ = "zona_cobradores"
    zona_id = Column(Integer, ForeignKey("zonas.id"), primary_key=True)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), primary_key=True)
    asignado_en = Column(DateTime, default=_datetime.utcnow)
    zona = relationship("Zona", back_populates="zona_cobradores")
    cobrador = relationship("Cobrador", back_populates="zona_cobradores")


class Zona(Base):
    __tablename__ = "zonas"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    descripcion = Column(String)
    # cobrador_id legacy column permanece en la DB pero ya no lo gestiona el ORM.
    # La relacion real es muchos-a-muchos via zona_cobradores.
    vendedor_id = Column(Integer, ForeignKey("vendedores.id"), nullable=True)
    # Marca manual: la zona ya fue recorrida/terminada este bono.
    hecha = Column(Boolean, default=False)
    vendedor = relationship("Vendedor", back_populates="zonas")
    compradores = relationship("Comprador", back_populates="zona")
    zona_cobradores = relationship(
        "ZonaCobrador", back_populates="zona", cascade="all, delete-orphan"
    )

    @property
    def cobrador_activo(self):
        """Ultimo cobrador asignado a esta zona (por timestamp asignado_en)."""
        if not self.zona_cobradores:
            return None
        return max(
            self.zona_cobradores,
            key=lambda zc: zc.asignado_en or _datetime.min
        ).cobrador

    @property
    def cobrador(self):
        """Alias de cobrador_activo para compatibilidad."""
        return self.cobrador_activo

    @property
    def cobrador_id(self):
        """ID del ultimo cobrador asignado a esta zona."""
        c = self.cobrador_activo
        return c.id if c else None


class Vendedor(Base):
    __tablename__ = "vendedores"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    telefono = Column(String)
    activo = Column(Boolean, default=True)
    es_jefe_equipo = Column(Boolean, default=False)
    boletas = relationship("Boleta", back_populates="vendedor")
    zonas = relationship("Zona", back_populates="vendedor")
    liquidaciones = relationship("LiquidacionVendedor", back_populates="vendedor")



class LiquidacionVendedor(Base):
    """Liquidacion de comision a un vendedor por boletas vendidas."""
    __tablename__ = "liquidaciones_vendedor"
    id = Column(Integer, primary_key=True, index=True)
    vendedor_id = Column(Integer, ForeignKey("vendedores.id"), nullable=False)
    fecha = Column(DateTime, default=_datetime.utcnow)
    # Cuotas
    cuotas_vendidas    = Column(Integer, default=0)
    # Ponderado por multiplicador de PATA: 17 PATA1 + 7 PATA2 + 1 PATA3 = 17 + 14 + 3 = 34
    # cuotas_vendidas guarda el conteo literal (25); cuotas_equiv el ponderado (34).
    # Float desde PATA 0 (mult 0.67) — guardamos decimales, redondeamos a entero al mostrar.
    cuotas_equiv       = Column(Float, default=0.0)
    cuota_1_total      = Column(Float, default=0.0)   # valor cuota 1 × n boletas (el vendedor ya lo tiene)
    monto_cuotas       = Column(Float, default=0.0)
    comision_cuotas_pct= Column(Float, default=5.0)
    comision_cuotas    = Column(Float, default=0.0)
    # Contados
    contados_vendidos  = Column(Integer, default=0)
    # Ponderado por multiplicador de PATA (mismo criterio que cuotas_equiv).
    # contados_vendidos guarda el conteo literal de boletas; contados_equiv el ponderado
    # (PATA 0 ×0.67, PATA 1 ×1, PATA 2 ×2, ...). deferred: puede no existir en DB vieja.
    contados_equiv     = deferred(Column(Float, default=0.0))
    monto_contados     = Column(Float, default=0.0)   # num_cuotas × valor_cuota × n boletas
    comision_contados_pct = Column(Float, default=30.0)
    comision_contados  = Column(Float, default=0.0)
    # Cuotas extras cobradas (cuota 2, 3, ... que el vendedor cobró directamente al socio)
    cuotas_extras_cantidad = deferred(Column(Integer, default=0))
    cuotas_extras_valor    = deferred(Column(Float, default=0.0))   # valor de cada cuota extra (referencial)
    cuotas_extras_monto    = deferred(Column(Float, default=0.0))   # cantidad × valor
    comision_cuotas_extras = deferred(Column(Float, default=0.0))   # cuotas_extras_monto × comision_cuotas_pct%
    # Cuotas extras PATA 0 (cuota 2, 3, ... de boletas PATA 0, valor $10.000 c/u)
    cuotas_extras_p0_cantidad = deferred(Column(Integer, default=0))
    cuotas_extras_p0_valor    = deferred(Column(Float, default=0.0))   # valor cuota PATA 0 (referencial)
    cuotas_extras_p0_monto    = deferred(Column(Float, default=0.0))   # p0_cantidad × p0_valor
    comision_cuotas_extras_p0 = deferred(Column(Float, default=0.0))   # p0_monto × comision_cuotas_pct%
    # Totales
    total_comision     = Column(Float, default=0.0)   # legacy: se mantiene como total de comision pagada al vendedor
    total_a_rendir     = deferred(Column(Float, default=0.0))   # NUEVO: lo que el vendedor entrega a la org
    observacion = Column(String, nullable=True)
    vendedor = relationship("Vendedor", back_populates="liquidaciones")
    contado_items = relationship(
        "LiquidacionContadoItem",
        back_populates="liquidacion",
        cascade="all, delete-orphan",
    )


class LiquidacionContadoItem(Base):
    """Numero del pool CONTADO/CONTADO 2 VECES que el vendedor declaro como vendido al
    contado en una liquidacion. El numero permanece sin asignar a una boleta hasta que
    se cargue al socio en comprador_editar (donde pasa a numero_especial / numero_especial_2).
    """
    __tablename__ = "liquidacion_contado_items"
    id = Column(Integer, primary_key=True, index=True)
    liquidacion_id = Column(Integer, ForeignKey("liquidaciones_vendedor.id"), nullable=False)
    talonera_id    = Column(Integer, ForeignKey("taloneras.id"), nullable=False)
    numero         = Column(Integer, nullable=False)
    liquidacion = relationship("LiquidacionVendedor", back_populates="contado_items")

class Cobrador(Base):
    __tablename__ = "cobradores"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    telefono = Column(String)
    activo = Column(Boolean, default=True)
    comision_pct = Column(Float, default=10.0)
    zona_cobradores = relationship(
        "ZonaCobrador", back_populates="cobrador", cascade="all, delete-orphan"
    )
    boletas = relationship("Boleta", back_populates="cobrador")
    planillas = relationship("Planilla", back_populates="cobrador", order_by="Planilla.numero")

    @property
    def zonas(self):
        """Lista de zonas asignadas a este cobrador."""
        return [zc.zona for zc in self.zona_cobradores]


class Comprador(Base):
    __tablename__ = "compradores"
    id = Column(Integer, primary_key=True, index=True)
    apellido_nombre = Column(String, nullable=False, index=True)
    direccion = Column(String)
    zona_id = Column(Integer, ForeignKey("zonas.id"), index=True)
    telefono = Column(String)
    zona = relationship("Zona", back_populates="compradores")
    boletas = relationship("Boleta", back_populates="comprador")


class Talonera(Base):
    __tablename__ = "taloneras"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    multiplicador = Column(Float, default=1.0)
    numero_inicio = Column(Integer)
    numero_fin = Column(Integer)
    num_series = Column(Integer, default=3)
    offset_series = Column(Integer, default=0)
    activa = Column(Boolean, default=True)
    color = Column(String, default="#ffffff")
    valor_cuota = Column(Float, default=0.0)
    num_cuotas  = deferred(Column(Integer, default=12))  # cantidad de cuotas mensuales; deferred=no rompe SELECT si aún no existe la columna
    # Tipo de talonera: "COMUN" (por defecto) o "CONTADO" (talonera especial para pagos al contado)
    # Una talonera CONTADO no representa boletas reales — es un pool de números
    # que se asignan a boletas comunes cuando se paga al contado.
    tipo = Column(String, default="COMUN", nullable=False)
    # Cantidad de cifras con la que se formatean los números de esta talonera
    # (p.ej. 3 -> "001", 4 -> "0001"). Usado principalmente por taloneras CONTADO,
    # donde el rango puede ser más chico que el rango de boletas comunes (0001-9999).
    # deferred=True para que el SELECT no rompa si la columna aún no existe en la DB.
    num_digitos = deferred(Column(Integer, default=3))
    boletas = relationship("Boleta", back_populates="talonera",
                           foreign_keys="Boleta.talonera_id")


class Planilla(Base):
    __tablename__ = "planillas"
    id = Column(Integer, primary_key=True, index=True)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), nullable=False)
    numero = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    anio = Column(Integer, nullable=False)
    comision_pct = Column(Float, default=10.0)
    fecha_creacion = Column(DateTime, server_default=func.now())
    cobrador = relationship("Cobrador", back_populates="planillas")
    boletas = relationship("Boleta", back_populates="planilla")
    liquidacion = relationship("Liquidacion", back_populates="planilla", uselist=False)


class Liquidacion(Base):
    __tablename__ = "liquidaciones"
    id = Column(Integer, primary_key=True, index=True)
    planilla_id = Column(Integer, ForeignKey("planillas.id"), nullable=True, unique=True)
    fecha = Column(Date, nullable=False)
    total_cuotas = Column(Integer, default=0)
    monto_total = Column(Float, default=0.0)
    comision = Column(Float, default=0.0)
    neto = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())
    planilla = relationship("Planilla", back_populates="liquidacion")
    detalles = relationship("LiquidacionDetalle", back_populates="liquidacion")


class LiquidacionDetalle(Base):
    __tablename__ = "liquidacion_detalles"
    id = Column(Integer, primary_key=True, index=True)
    liquidacion_id = Column(Integer, ForeignKey("liquidaciones.id"), nullable=False)
    boleta_id = Column(Integer, ForeignKey("boletas.id"), nullable=False)
    cuotas_cobradas = Column(Integer, default=0)
    liquidacion = relationship("Liquidacion", back_populates="detalles")
    boleta = relationship("Boleta")


class Boleta(Base):
    __tablename__ = "boletas"
    id = Column(Integer, primary_key=True, index=True)
    talonera_id = Column(Integer, ForeignKey("taloneras.id"), nullable=False, index=True)
    numero_principal = Column(Integer, nullable=False, index=True)
    numeros_adicionales = Column(String)
    comprador_id = Column(Integer, ForeignKey("compradores.id"), index=True)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), index=True)
    vendedor_id = Column(Integer, ForeignKey("vendedores.id"), index=True)
    planilla_id = Column(Integer, ForeignKey("planillas.id"), nullable=True, index=True)
    fecha_venta = Column(Date)
    condicion = Column(Enum(CondicionBoleta), default=CondicionBoleta.SIN_VENDER)
    cuotas_pactadas = Column(Integer, default=11)
    cuotas_anticipadas = Column(Integer, default=1)
    cuotas_pagadas = Column(Integer, default=0)
    historial_cuotas = Column(String, nullable=True)  # JSON: {"cuota_num": mes_pagado}
    # Mes calendario (1-12) en que el socio se dio de baja durante la cobranza.
    # Se setea al guardar la liquidación cuando el cobrador marca la baja con clic
    # derecho. NULL = sin baja. Va de la mano con condicion = BAJA.
    mes_baja = Column(Integer, nullable=True)
    total_pagado = Column(Float, default=0.0)
    # Talonera especial CONTADO: cuando el socio paga al contado, recibe un
    # número de una talonera tipo CONTADO. numero_especial es el número asignado,
    # talonera_especial_id apunta a la talonera CONTADO de la cual salió.
    # Slot 1 (numero_especial / talonera_especial_id) = sorteo "CONTADO"
    # Slot 2 (numero_especial_2 / talonera_especial_2_id) = sorteo "CONTADO 2 VECES"
    # Reglas:
    #   - Pago en 1 sola cuota → ambos slots asignados (CONTADO + CONTADO 2 VECES)
    #   - Pago en 2 cuotas    → solo slot 2 asignado (CONTADO 2 VECES)
    numero_especial = Column(Integer, nullable=True, index=True)
    talonera_especial_id = Column(Integer, ForeignKey("taloneras.id"), nullable=True)
    numero_especial_2 = deferred(Column(Integer, nullable=True, index=True))
    talonera_especial_2_id = deferred(Column(Integer, ForeignKey("taloneras.id"), nullable=True))
    liquidacion_vendedor_id = Column(Integer, ForeignKey("liquidaciones_vendedor.id"), nullable=True)
    # Modalidad con la que esta boleta entró a su liquidación: 'cuotas' | 'contado' | 'contado2'.
    # Se setea al liquidar y al agregar números a una liquidación existente. Permite
    # recalcular correctamente el rinde al editar una liquidación desde el historial.
    # Null en boletas previas a esta migración (se asume 'cuotas' al editarlas).
    modalidad_liquidacion = deferred(Column(String, nullable=True))
    created_at = Column(DateTime, server_default=func.now())

    talonera = relationship("Talonera", back_populates="boletas",
                            foreign_keys=[talonera_id])
    talonera_especial = relationship("Talonera", foreign_keys=[talonera_especial_id])
    talonera_especial_2 = relationship("Talonera", foreign_keys=[talonera_especial_2_id])
    comprador = relationship("Comprador", back_populates="boletas")
    cobrador = relationship("Cobrador", back_populates="boletas")
    vendedor = relationship("Vendedor", back_populates="boletas")
    planilla = relationship("Planilla", back_populates="boletas")
    liquidacion_vendedor = relationship("LiquidacionVendedor")


class EntregaCaja(Base):
    __tablename__ = "entregas_caja"
    id               = Column(Integer, primary_key=True, index=True)
    talonera_nombre  = Column(String, nullable=False)
    desde            = Column(Integer, nullable=False)
    hasta            = Column(Integer, nullable=False)
    boletas_afectadas = Column(Integer, default=0)
    observacion      = Column(String, nullable=True)
    tipo             = Column(String, default="ENTREGA")  # ENTREGA | RETIRO
    fecha            = Column(DateTime, default=_datetime.utcnow)
    usuario_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    vendedor_id      = Column(Integer, ForeignKey("vendedores.id"), nullable=True)
    usuario          = relationship("User")
    vendedor         = relationship("Vendedor")


class BonoAnterior(Base):
    """Historial de números vendidos en el bono ANTERIOR (importado desde Excel).
    Cada fila = un bono vendido. Sirve para medir rendimiento de cada zona vs el
    bono actual y detectar compradores que aún no renovaron."""
    __tablename__ = "bono_anterior"
    id              = Column(Integer, primary_key=True, index=True)
    pata            = Column(String)                       # "PATA 2"
    apellido_nombre = Column(String, index=True)
    direccion       = Column(String)
    zona            = Column(String, index=True)           # tal cual viene (ej. "35")
    cobrador        = Column(String)
    condicion       = Column(String)
    vendedor        = Column(String)
    multiplicador   = Column(Float, default=1.0)           # de la PATA (PATA 2 = 2.0, etc.)
    importado_en    = Column(DateTime, default=_datetime.utcnow)


class Sorteo(Base):
    __tablename__ = "sorteos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    descripcion = Column(String, nullable=True)
    tipo = Column(Enum(TipoSorteo), nullable=False)
    cifras = Column(String, nullable=False)
    fecha = Column(Date, nullable=False)
    num_premios = Column(Integer, default=20)
    resultado_json = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    premios = relationship(
        "PremioSorteo",
        back_populates="sorteo",
        cascade="all, delete-orphan",
        order_by="PremioSorteo.orden",
    )


class PremioSorteo(Base):
    """Premio asociado a un sorteo.

    clase:
      - "ORDEN"  → orden de compra / dinero. `monto` es el valor en $ y SUMA
                   al balance de costos del bono apenas hay un ganador valido.
      - "FISICO" → bien (moto, TV, bici). `monto` es solo costo de referencia
                   (NO entra al balance automatico; se carga aparte como gasto
                   al comprarlo).
    modalidad:
      - "POSICION" → un unico ganador para esa posicion (FINAL 1°/2°/3°).
      - "CADA_UNO" → todos los ganadores del sorteo reciben este premio
                     (ej: semanal "$30.000 a cada uno").
    """
    __tablename__ = "premios_sorteo"
    id          = Column(Integer, primary_key=True, index=True)
    sorteo_id   = Column(Integer, ForeignKey("sorteos.id"), nullable=False, index=True)
    orden       = Column(Integer, default=1)          # posicion / orden de listado
    descripcion = Column(String, nullable=False)
    clase       = Column(String, default="ORDEN")     # ORDEN | FISICO
    monto       = Column(Float, default=0.0)
    modalidad   = Column(String, default="POSICION")  # POSICION | CADA_UNO
    created_at  = Column(DateTime, server_default=func.now())

    sorteo = relationship("Sorteo", back_populates="premios")
    entregas = relationship(
        "EntregaPremio",
        back_populates="premio",
        cascade="all, delete-orphan",
    )


class EntregaPremio(Base):
    """Asignacion de un premio de sorteo a una boleta ganadora + entrega.

    Persiste el ganador (que de otro modo se calcula al vuelo) para poder
    emitir el recibo de entrega y rastrear si ya fue entregado.
    """
    __tablename__ = "entregas_premio"
    id             = Column(Integer, primary_key=True, index=True)
    premio_id      = Column(Integer, ForeignKey("premios_sorteo.id"), nullable=False, index=True)
    boleta_id      = Column(Integer, ForeignKey("boletas.id"), nullable=False, index=True)
    numero_ganador = Column(String, nullable=True)   # numero que salio favorecido (4 digitos)
    entregado      = Column(Boolean, default=False)
    fecha_entrega  = Column(Date, nullable=True)
    observacion    = Column(String, nullable=True)
    created_at     = Column(DateTime, server_default=func.now())

    premio = relationship("PremioSorteo", back_populates="entregas")
    boleta = relationship("Boleta")


class HabilitacionSorteo(Base):
    """Override manual de habilitacion para cobrar un premio.

    Por defecto la habilitacion se calcula al vuelo: un ganador esta habilitado
    si pago al menos una cuota en el mes del sorteo, o si la boleta se vendio en
    ese mismo mes antes del sorteo. Esta tabla permite forzar el resultado por
    EXCEPCION (ej: habilitar a un socio que pago fuera de termino o por caja).

    Una fila (sorteo_id, boleta_id) reemplaza el calculo automatico:
      - habilitado = True  -> habilitado manualmente (excepcion)
      - habilitado = False -> deshabilitado manualmente
    Si no hay fila, manda el calculo automatico.
    """
    __tablename__ = "habilitaciones_sorteo"
    id          = Column(Integer, primary_key=True, index=True)
    sorteo_id   = Column(Integer, ForeignKey("sorteos.id"), nullable=False, index=True)
    boleta_id   = Column(Integer, ForeignKey("boletas.id"), nullable=False, index=True)
    habilitado  = Column(Boolean, default=True)
    motivo      = Column(String, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())


class ConfigBono(Base):
    """Par clave/valor para configuracion global del bono (ej: pago_mensual_bomberos)."""
    __tablename__ = "config_bono"
    clave        = Column(String, primary_key=True)
    valor_float  = Column(Float, default=0.0)


class GastoContabilidad(Base):
    """Egreso registrado manualmente: premios, viajes, alojamiento, etc."""
    __tablename__ = "gastos_contabilidad"
    id           = Column(Integer, primary_key=True, index=True)
    descripcion  = Column(String, nullable=False)
    categoria    = Column(String, default="OTRO")    # PREMIO / VIAJE / ALOJAMIENTO / SUELDO / OTRO
    periodicidad = Column(String, default="UNICO")   # UNICO | MENSUAL
    fecha        = Column(Date, nullable=True)
    monto        = Column(Float, default=0.0)        # si MENSUAL: monto por mes
    created_at   = Column(DateTime, server_default=func.now())


class GeocodeCache(Base):
    """Cache compartido de geocoding de direcciones de socios.

    Se llena a demanda: el frontend del mapa consulta Nominatim respetando
    1 req/seg y POSTea las coords al endpoint para que queden cacheadas
    para todos los usuarios (en cualquier dispositivo).

    lat/lng en NULL = la direccion no se pudo ubicar (no reintentar)
    intentos = cuantas veces se intento geocodificarla (debug)
    last_try = timestamp del ultimo intento
    """
    __tablename__ = "geocode_cache"
    direccion = Column(String, primary_key=True)  # direccion normalizada (UPPER trim)
    lat       = Column(Float, nullable=True)
    lng       = Column(Float, nullable=True)
    intentos  = Column(Integer, default=1)
    last_try  = Column(DateTime, server_default=func.now())

    lat       = Column(Float, nullable=True)
    lng       = Column(Float, nullable=True)
    intentos  = Column(Integer, default=0)
    last_try  = Column(DateTime, nullable=True)


class EntregaCobrador(Base):
    """Adelantos de dinero que el cobrador entrega DURANTE el mes, a cuenta de la
    cobranza. Se descuentan del saldo en la liquidación consolidada del cobrador.
    Son sueltos (no atados a una planilla): cobrador + período (mes/año) + monto."""
    __tablename__ = "entregas_cobrador"
    id          = Column(Integer, primary_key=True, index=True)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), nullable=False, index=True)
    fecha       = Column(Date, nullable=False)
    mes         = Column(Integer, nullable=False)   # período de cobranza al que aplica
    anio        = Column(Integer, nullable=False)
    monto       = Column(Float, default=0.0)
    # tipo de entrega: "EFECTIVO" (adelanto de plata) o "PREMIO" (premio/gasto que el
    # cobrador pagó por cuenta de la institución). Ambos bajan el saldo a entregar,
    # pero se muestran separados. El costo del premio para la ganancia se cuenta en
    # el módulo Sorteos/Premios, NO acá (para no duplicar).
    tipo        = Column(String, default="EFECTIVO")
    observacion = Column(String, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())
    cobrador    = relationship("Cobrador")
