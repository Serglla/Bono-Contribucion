"""Alta de socio (modal "Nuevo Socio"): qué le pasa a la boleta al cargarle el socio.

Movido desde routers/compradores.py::crear (relevamiento, punto 7). El router
valida permisos, resuelve la zona, llama a `cargar_socio` y arma la respuesta.
Tests de punta a punta: tests/test_alta_socio.py.

PASOS (en este orden, cada uno es una función de abajo)
  1. vendedor_de_la_zona   una zona tiene un solo vendedor: si ya tiene, manda.
  2. socio_de_la_boleta    reintentar el alta reusa el socio (no duplica).
  3. fijar_cuotas          cuántas se cobran según la fecha; contado = todo pago.
  4. asignar_especiales    números de sorteo CONTADO, solo si están libres.
  5. asignar_cobrador      solo si queda algo por cobrar; el manual pisa la zona.
  6. condición             EN_COBRANZA / VENDIDO (reglas_boleta); BAJA no se toca.
"""
from datetime import date
from typing import Optional

from .. import models
from ..cuotas import cuotas_vigentes
from ..models import CondicionBoleta
from . import reglas_boleta as reglas

# Condiciones que el alta puede recalcular (BAJA es definitiva: no se toca).
CONDICIONES_RECALCULABLES = (CondicionBoleta.SIN_VENDER, CondicionBoleta.CAJA,
                             CondicionBoleta.VENDIDO, CondicionBoleta.EN_COBRANZA)


def _entero_positivo(x) -> Optional[int]:
    try:
        v = int(str(x if x is not None else "").strip())
    except (ValueError, TypeError):
        return None
    return v if v > 0 else None


def cobrador_valido(db, raw) -> Optional[int]:
    """Valida el cobrador elegido a mano en el modal (campo opcional, casos poco
    frecuentes): vacío, basura o inexistente → None y manda el de la zona."""
    cid = _entero_positivo(raw)
    if cid is None:
        return None
    cob = db.query(models.Cobrador).get(cid)
    return cob.id if cob else None


def vendedor_de_la_zona(db, zona_id: Optional[int], vendedor_id: Optional[int]) -> Optional[int]:
    """Vendedor que queda en la boleta. Una zona tiene UN vendedor: si ya lo
    tiene, manda ese; si no tiene, se le vincula el que vino del modal."""
    if not zona_id:
        return vendedor_id
    z = db.query(models.Zona).get(zona_id)
    if not z:
        return vendedor_id
    if z.vendedor_id:
        return z.vendedor_id
    if vendedor_id:
        z.vendedor_id = vendedor_id
    return vendedor_id


def socio_de_la_boleta(db, boleta, apellido_nombre: str, direccion: str, telefono: str,
                       zona_id: Optional[int]):
    """Crea el socio, o REUSA el que la boleta ya tiene (idempotencia).

    Antes el socio se creaba siempre: si el navegador reintentaba el alta (el
    fetch cortaba con "Error de conexión" aunque el guardado hubiera entrado),
    cada reintento creaba OTRO socio y quedaban socios fantasma sin boleta."""
    datos = dict(apellido_nombre=apellido_nombre.strip().upper(),
                 direccion=direccion.strip().upper() or None,
                 telefono=telefono.strip() or None)
    c = db.query(models.Comprador).get(boleta.comprador_id) if boleta.comprador_id else None
    if c is None:
        c = models.Comprador(zona_id=zona_id, **datos)
        db.add(c)
    else:
        # Mismo socio, datos posiblemente corregidos en el reintento.
        for k, v in datos.items():
            setattr(c, k, v)
        if zona_id:
            c.zona_id = zona_id
    db.flush()
    return c


def es_contado_liquidado(boleta) -> bool:
    """¿El vendedor la liquidó al contado? Dos señales (cualquiera alcanza):
    modalidad 'contado'/'contado2', o número especial (data vieja o rearmada que
    no dejó modalidad). Si la liquidó EXPLÍCITAMENTE en cuotas, eso manda y el
    número especial colgado se limpia (ver `fijar_cuotas`)."""
    m = (boleta.modalidad_liquidacion or "").strip().lower()
    if m == "cuotas":
        return False
    return (m in reglas.MODALIDADES_CONTADO
            or boleta.numero_especial is not None
            or boleta.numero_especial_2 is not None)


def fijar_cuotas(boleta, pactadas_form: Optional[int], anticipadas_form: Optional[int]) -> None:
    """Pactadas, anticipadas y pagas de la boleta al dar de alta el socio.

    Cuántas se cobran = cuotas_vigentes(fecha) — las últimas se regalan (ver
    app/cuotas.py). QUÉ FECHA MANDA: la de compra del modal (el operador manda;
    el modal la prellena con la de la liquidación), si no la de la liquidación
    del vendedor, si no hoy. Tomar "hoy" le comería una cuota a un alta cargada
    el mes siguiente a la liquidación.

    Contado: pactadas = anticipadas = pagas = todas las vigentes, sin importar el
    form. En cuotas: lo del form recortado a lo cobrable (anticipadas ≥ 1)."""
    if (boleta.modalidad_liquidacion or "").strip().lower() == "cuotas":
        # Liquidada en cuotas: un número especial colgado es espurio. Se limpia
        # para que la boleta no quede marcada "12/12 al contado".
        boleta.numero_especial = None
        boleta.talonera_especial_id = None
        boleta.numero_especial_2 = None
        boleta.talonera_especial_2_id = None
        contado = False
    else:
        contado = es_contado_liquidado(boleta)

    nominal = (boleta.talonera.num_cuotas if boleta.talonera and boleta.talonera.num_cuotas else 0) or 12
    fecha_ref = boleta.fecha_venta or getattr(boleta.liquidacion_vendedor, "fecha", None)
    maximo = cuotas_vigentes(nominal, fecha_ref)
    if contado:
        boleta.cuotas_pactadas = maximo
        ant = maximo
    else:
        boleta.cuotas_pactadas = (min(int(pactadas_form), maximo)
                                  if pactadas_form is not None and pactadas_form > 0 else maximo)
        ant = anticipadas_form if anticipadas_form and anticipadas_form > 0 else 1
        ant = min(ant, maximo)   # no anticipar más cuotas de las que existen
    boleta.cuotas_anticipadas = ant
    boleta.cuotas_pagadas = ant  # las anticipadas ya están cobradas


def _especial_libre(db, boleta, num: int, talonera_id: int) -> bool:
    otra = db.query(models.Boleta).filter(
        models.Boleta.id != boleta.id,
        (((models.Boleta.talonera_especial_id == talonera_id) & (models.Boleta.numero_especial == num))
         | ((models.Boleta.talonera_especial_2_id == talonera_id) & (models.Boleta.numero_especial_2 == num))),
    ).first()
    return otra is None


def asignar_especiales(db, boleta, te1=None, ne1=None, te2=None, ne2=None) -> None:
    """Números de sorteo CONTADO (slot 1) y CONTADO 2 VECES (slot 2) del modal.
    Opcionales (llegan después, en talonarios de 5); solo se asignan si el
    número no lo tiene otra boleta."""
    t1, n1 = _entero_positivo(te1), _entero_positivo(ne1)
    t2, n2 = _entero_positivo(te2), _entero_positivo(ne2)
    if t1 and n1 and _especial_libre(db, boleta, n1, t1):
        boleta.numero_especial, boleta.talonera_especial_id = n1, t1
    if t2 and n2 and _especial_libre(db, boleta, n2, t2):
        boleta.numero_especial_2, boleta.talonera_especial_2_id = n2, t2


def asignar_cobrador(db, boleta, socio, cobrador_manual: Optional[int]) -> None:
    """Cobrador de la boleta: solo si queda algo por cobrar (los contados y las
    que entran todas pagas NO llevan). El elegido a mano pisa al de la zona."""
    if not reglas.lleva_cobrador(boleta):
        return
    if cobrador_manual:
        boleta.cobrador_id = cobrador_manual
    elif socio.zona_id:
        z = db.query(models.Zona).get(socio.zona_id)
        if z and z.cobrador_id:
            boleta.cobrador_id = z.cobrador_id


def cargar_socio(db, boleta, *, apellido_nombre: str, direccion: str = "", telefono: str = "",
                 zona_id: Optional[int] = None, fecha_compra: Optional[str] = None,
                 vendedor_id: Optional[int] = None, cobrador_raw=None,
                 cuotas_pactadas: Optional[int] = None, cuotas_anticipadas: Optional[int] = None,
                 te1=None, ne1=None, te2=None, ne2=None):
    """Alta completa: deja socio y boleta listos (sin commit). Devuelve el socio."""
    vendedor = vendedor_de_la_zona(db, zona_id, vendedor_id)
    socio = socio_de_la_boleta(db, boleta, apellido_nombre, direccion, telefono, zona_id)

    boleta.comprador_id = socio.id
    if fecha_compra:
        boleta.fecha_venta = date.fromisoformat(fecha_compra)
    if vendedor:
        boleta.vendedor_id = vendedor

    fijar_cuotas(boleta, cuotas_pactadas, cuotas_anticipadas)
    asignar_especiales(db, boleta, te1, ne1, te2, ne2)
    asignar_cobrador(db, boleta, socio, cobrador_valido(db, cobrador_raw))
    if boleta.condicion in CONDICIONES_RECALCULABLES:
        boleta.condicion = reglas.condicion_vendida(boleta)
    return socio
