from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from .models import CondicionBoleta


# ── Auth ──────────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    password: str
    is_admin: bool = False

class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str]
    is_admin: bool
    class Config: from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str


# ── Zona ──────────────────────────────────────────────────────────────────────
class ZonaCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None

class ZonaOut(ZonaCreate):
    id: int
    class Config: from_attributes = True


# ── Vendedor ──────────────────────────────────────────────────────────────────
class VendedorCreate(BaseModel):
    nombre: str
    telefono: Optional[str] = None
    activo: bool = True

class VendedorOut(VendedorCreate):
    id: int
    class Config: from_attributes = True


# ── Cobrador ──────────────────────────────────────────────────────────────────
class CobradorCreate(BaseModel):
    nombre: str
    telefono: Optional[str] = None
    activo: bool = True

class CobradorOut(CobradorCreate):
    id: int
    class Config: from_attributes = True


# ── Comprador ─────────────────────────────────────────────────────────────────
class CompradorCreate(BaseModel):
    apellido_nombre: str
    direccion: Optional[str] = None
    zona_id: Optional[int] = None
    telefono: Optional[str] = None

class CompradorOut(CompradorCreate):
    id: int
    zona: Optional[ZonaOut] = None
    class Config: from_attributes = True


# ── Talonera ──────────────────────────────────────────────────────────────────
class TaloneraCreate(BaseModel):
    nombre: str
    multiplicador: int = 1
    numero_inicio: Optional[int] = None
    numero_fin: Optional[int] = None
    activa: bool = True

class TaloneraOut(TaloneraCreate):
    id: int
    class Config: from_attributes = True


# ── Boleta ────────────────────────────────────────────────────────────────────
class BoletaCreate(BaseModel):
    talonera_id: int
    numero_principal: int
    numeros_adicionales: Optional[str] = None
    comprador_id: Optional[int] = None
    cobrador_id: Optional[int] = None
    vendedor_id: Optional[int] = None
    fecha_venta: Optional[date] = None
    condicion: CondicionBoleta = CondicionBoleta.SIN_VENDER
    cuotas_pactadas: int = 11
    cuotas_pagadas: int = 0
    total_pagado: float = 0.0

class BoletaOut(BoletaCreate):
    id: int
    talonera: Optional[TaloneraOut] = None
    comprador: Optional[CompradorOut] = None
    cobrador: Optional[CobradorOut] = None
    vendedor: Optional[VendedorOut] = None
    class Config: from_attributes = True
