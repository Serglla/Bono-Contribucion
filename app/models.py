from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Enum, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from .database import Base


class TipoSorteo(str, enum.Enum):
    SEMANAL = "SEMANAL"
    MENSUAL = "MENSUAL"
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
    created_at = Column(DateTime, server_default=func.now())


class Zona(Base):
    __tablename__ = "zonas"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    descripcion = Column(String)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), nullable=True)
    vendedor_id = Column(Integer, ForeignKey("vendedores.id"), nullable=True)
    cobrador = relationship("Cobrador", back_populates="zonas")
    vendedor = relationship("Vendedor", back_populates="zonas")
    compradores = relationship("Comprador", back_populates="zona")


class Vendedor(Base):
    __tablename__ = "vendedores"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    telefono = Column(String)
    activo = Column(Boolean, default=True)
    boletas = relationship("Boleta", back_populates="vendedor")
    zonas = relationship("Zona", back_populates="vendedor")


class Cobrador(Base):
    __tablename__ = "cobradores"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    telefono = Column(String)
    activo = Column(Boolean, default=True)
    zonas = relationship("Zona", back_populates="cobrador")
    boletas = relationship("Boleta", back_populates="cobrador")
    planillas = relationship("Planilla", back_populates="cobrador", order_by="Planilla.numero")


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
    boletas = relationship("Boleta", back_populates="talonera")


class Planilla(Base):
    __tablename__ = "planillas"
    id = Column(Integer, primary_key=True, index=True)
    cobrador_id = Column(Integer, ForeignKey("cobradores.id"), nullable=False)
    numero = Column(Integer, nullable=False)  # secuencial por cobrador: 1, 2, 3...
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
    planilla_id = Column(Integer, ForeignKey("planillas.id"), nullable=False, unique=True)
    fecha = Column(Date, nullable=False)
    total_cuotas = Column(Integer, default=0)   # cuotas cobradas en este mes
    monto_total = Column(Float, default=0.0)    # total recaudado
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
    cuotas_cobradas = Column(Integer, default=0)  # cuotas cobradas en este mes
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
    cuotas_anticipadas = Column(Integer, default=1)   # cuotas cobradas por el vendedor al momento de la venta
    cuotas_pagadas = Column(Integer, default=0)
    total_pagado = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())

    talonera = relationship("Talonera", back_populates="boletas")
    comprador = relationship("Comprador", back_populates="boletas")
    cobrador = relationship("Cobrador", back_populates="boletas")
    vendedor = relationship("Vendedor", back_populates="boletas")
    planilla = relationship("Planilla", back_populates="boletas")


class Sorteo(Base):
    __tablename__ = "sorteos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    tipo = Column(Enum(TipoSorteo), nullable=False)
    cifras = Column(String, nullable=False)  # ej: "2", "2,3", "2,3,4"
    fecha = Column(Date, nullable=False)
    num_premios = Column(Integer, default=20)         # hasta qué premio se considera (1-20)
    resultado_json = Column(String, nullable=True)  # JSON: ["1234","5678",...] hasta num_premios números
    created_at = Column(DateTime, server_default=func.now())
