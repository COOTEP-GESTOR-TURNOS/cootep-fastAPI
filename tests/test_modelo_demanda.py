"""Pronóstico de demanda medido con los datos que propone el generador de prueba.

El simulador sube la demanda los lunes y viernes, cerca de los vencimientos del 5 y el 10 y en
los días de pago de subsidio y de nómina. El modelo debe aprender esos efectos de los eventos por
día de cada agencia: equivocarse casi lo mismo que un pronóstico perfecto (el que conoce la media
exacta de cada día) y menos que el promedio simple de cada día de la semana.
"""

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from uuid import NAMESPACE_OID, uuid5

import pytest

from app.services.calendario_demanda import cierre_calendario, marcas_calendario
from app.services.modelo_demanda import entrenar, evaluar, pronosticar
from app.services.simulacion_demo import (
    AgenciaDemo, CatalogoDemo, fechas, programar_eventos, simular_jornada, volumen_esperado,
)

HOY = date(2026, 11, 20)


def _id(nombre: str) -> str:
    return str(uuid5(NAMESPACE_OID, nombre))


@pytest.fixture(scope="module")
def escenario():
    agencia = AgenciaDemo.model_validate({
        "id": _id("agencia"), "codigo": "AG01", "nombre": "Agencia Mocoa", "municipio": "Mocoa",
        "departamento": "PUTUMAYO", "activa": True,
        "servicios": [{"id": _id("caja"), "codigo": "CAJA", "prefijo": "C", "ventanillas": 3,
                       "activa": True, "apertura": "08:00", "cierre": "16:00"}],
        "funcionarios": [{"id": _id(f"funcionario-{i}"), "servicioId": None} for i in range(3)],
        "asociados": [_id(f"asociado-{i}") for i in range(1_000)],
    })
    catalogo = CatalogoDemo(semilla=7, agencias=[agencia])
    desde = HOY - timedelta(days=270)
    eventos: dict[date, set[str]] = defaultdict(set)
    for evento in programar_eventos(catalogo, desde, HOY + timedelta(days=90)):
        eventos[date.fromisoformat(evento["fecha"])].add(evento["tipo"])
    historial = {
        fecha: sum(1 for _ in simular_jornada(agencia, fecha, catalogo.semilla, eventos.get(fecha, ())))
        for fecha in fechas(desde, HOY)
        if not cierre_calendario(marcas_calendario(fecha, "PUTUMAYO", "Mocoa"), eventos.get(fecha, ()))
    }
    return historial, eventos, agencia, catalogo.semilla


def test_el_modelo_se_equivoca_lo_mismo_que_un_pronostico_perfecto(escenario):
    """Ningún pronóstico baja del azar del simulador: el perfecto conoce la media exacta de cada día."""
    historial, eventos, agencia, semilla = escenario
    resultado = evaluar(historial, eventos, dias_prueba=40)
    assert resultado is not None
    prueba = sorted(historial)[-40:]
    perfecto = mean(abs(round(volumen_esperado(agencia, fecha, semilla, eventos.get(fecha, ()))) - historial[fecha]) for fecha in prueba)
    assert resultado.error_modelo <= perfecto * 1.1 + 0.2
    # Y le gana a lo que haría alguien sin modelo: el promedio de cada día de la semana.
    assert resultado.error_modelo < resultado.error_referencia


def test_aprende_el_efecto_de_los_pagos_de_cada_agencia(escenario):
    historial, eventos, _, _ = escenario
    modelo = entrenar(historial, eventos)
    martes = date(2026, 12, 15)  # sin vencimientos cerca
    normal, subsidio, nomina = (pronosticar(modelo, [martes], {martes: tipos})[martes]
                                for tipos in ((), {"PAGO_SUBSIDIO_FAMILIAR"}, {"PAGO_NOMINA"}))
    # El generador multiplica la demanda por 1,45 con subsidio y 1,35 con nómina.
    assert 1.25 < subsidio / normal < 1.65
    assert 1.15 < nomina / normal < 1.55


def test_no_depende_del_dia_del_mes(escenario):
    historial, eventos, _, _ = escenario
    modelo = entrenar(historial, eventos)
    # Dos martes lejos de los vencimientos y sin eventos: misma demanda esperada.
    dias = [date(2026, 12, 22), date(2026, 12, 29)]
    pronostico = pronosticar(modelo, dias, {})
    assert pronostico[dias[0]] == pronostico[dias[1]]


def test_sin_historial_suficiente_no_evalua():
    pocos = {date(2026, 11, 2) + timedelta(days=dia): 20 for dia in range(10)}
    assert evaluar(pocos, {}, dias_prueba=20) is None
