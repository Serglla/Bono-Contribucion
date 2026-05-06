from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Enum, DateTime, Boolean
from sqlalchemy.orm import relationship
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
    cuota_1_total      = Column(Float, default=0.0)   # valor cuota 1 × n boletas (el vendedor ya lo tiene)
    monto_cuotas       = Column(Float, default=0.0)
    comision_cuotas_pct= Column(Float, default=5.0)
    comision_cuotas    = Column(Float, default=0.0)
    # Contados
    contados_vendidos  = Column(Integer, default=0)
    monto_contados     = Column(Float, default=0.0)   # num_cuotas × valor_cuota × n boletas
    comision_contados_pct = Column(Float, default=30.0)
    comision_contados  = Column(Float, default=0.0)
    # Total
    total_comision     = Column(Float, default=0.0)
    observacion = Column(String, nullable=True)
    vendedor = relationship("Vendedor", back_populates="liquidaciones")

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
    zona_id = Column(Integer, ForeignKey("zonas.id"))
    telefono = Column(String)
    zona = relationship("Zona", back_populates="compradores")
    boletas = relationship("Boleta", back_populates="comprador")


class Talonera(Base):
    __tablename__ = "taloneras"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    multiplicador = Column(Integer, default=1)
    numero_inicio = Column(Integer)
    numero_fin = Column(Integer)
    num_series = Column(Integer, default=3)
    offset_series = Column(Integer, default=0)
    activa = Column(Boolean, default=True)
    color = Column(String, default="#ffffff")
    valor_cuota = Column(Float, default=0.0)
    num_cuotas  = Column(Integer, default=12)   # cantidad de cuotas mensuales de la talonera
    # Tipo de talonera: "COMUN" (por defecto) o "CONTADO" (talonera especial para pagos al contado)
    # Una talonera CONTADO no representa boletas reales — es un pool de números
    # que se asignan a boletas comunes cuando se paga al contado.
    tipo = Column(String, default="COMUN", nullable=False)
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
    talonera_id = Column(Integer, ForeignKey("taloneras.id"), nullable=False)
    numero_principal = Column(Integer, nullable=False, index=True)
    numeros_adicionales = Column(String)
    comprador_id = Column(Integer, ForeignKey("compradores.id"))
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"))
    vendedor_id = Column(Integer, ForeignKey("vendedores.id"))
    planilla_id = Column(Integer, ForeignKey("planillas.id"), nullable=True)
    fecha_venta = Column(Date)
    condicion = Column(Enum(CondicionBoleta), default=CondicionBoleta.SIN_VENDER)
    cuotas_pactadas = Column(Integer, default=11)
    cuotas_anticipadas = Column(Integer, default=1)
    cuotas_pagadas = Column(Integer, default=0)
    historial_cuotas = Column(String, nullable=True)  # JSON: {"cuota_num": mes_pagado}
    total_pagado = Column(Float, default=0.0)
    # Talonera especial CONTADO: cuando el socio paga al contado, recibe un
    # número de una talonera tipo CONTADO. numero_especial es el número asignado,
    # talonera_especial_id apunta a la talonera CONTADO de la cual salió.
    numero_especial = Column(Integer, nullable=True, index=True)
    talonera_especial_id = Column(Integer, ForeignKey("taloneras.id"), nullable=True)
    liquidacion_vendedor_id = Column(Integer, ForeignKey("liquidaciones_vendedor.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    talonera = relationship("Talonera", back_populates="boletas",
                            foreign_keys=[talonera_id])
    talonera_especial = relationship("Talonera", foreign_keys=[talonera_especial_id])
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
    fecha            = Column(DateTime, default=_datetime.utcnow)
    usuario_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    vendedor_id      = Column(Integer, ForeignKey("vendedores.id"), nullable=True)
    usuario          = relationship("User")
    vendedor         = relationship("Vendedor")


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
