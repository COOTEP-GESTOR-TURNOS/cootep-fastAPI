"""Tablero mensual: indicadores de ClickHouse, calendario de cada agencia y vista general."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import httpx

from app.schemas_demanda import AgenciaTablero, CalendarioDemanda, DiaDemanda, SolicitudTablero
from app.services.demanda_service import DemandaService
from app.services.tablero_service import TableroService, calendario_general

MOCOA = UUID("a0000000-0000-4000-8000-000000000001")
ORITO = UUID("a0000000-0000-4000-8000-000000000003")
CORTE = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)


class Repositorio:
    """Historial por agencia; Mocoa tiene datos de demostración y Orito reales."""

    def __init__(self, hoy: date):
        base = {hoy - timedelta(days=dia): 20 + dia % 5 for dia in range(1, 120)}
        self.datos = {MOCOA: {**base, hoy: 7}, ORITO: {fecha: total * 2 for fecha, total in base.items()}}

    async def fuente(self, agencia_id):
        return "SIMULADA" if agencia_id == MOCOA else "REAL"

    async def primer_dia(self, agencia_id, hasta, servicio_id=None):
        return min((fecha for fecha in self.datos[agencia_id] if fecha < hasta), default=None)

    async def historial(self, agencia_id, desde, hasta, servicio_id=None):
        return {fecha: total for fecha, total in self.datos[agencia_id].items() if desde <= fecha < hasta}

    async def eventos(self, agencia_id, desde, hasta):
        return {}


class ClickHouse:
    async def indicadores(self, agencia_id, desde, hasta, servicio_id=None, colaborador_id=None):
        if agencia_id == ORITO:
            raise httpx.ConnectError("ClickHouse no respondió")
        return {"atendidos": 10, "cancelados": 1, "ausentes": 1, "espera_promedio_min": 6.0,
                "atencion_promedio_min": 4.0, "proporcion_preferencial": 0.25}


class Eventos:
    async def ultima_marca(self):
        return CORTE


def _tablero(hoy: date, agencia_id=None) -> SolicitudTablero:
    agencias = [AgenciaTablero(agencia_id=MOCOA, nombre="Mocoa", departamento="PUTUMAYO", municipio="Mocoa")]
    if agencia_id is None:
        agencias.append(AgenciaTablero(agencia_id=ORITO, nombre="Orito", departamento="PUTUMAYO", municipio="Orito"))
    return SolicitudTablero(mes=hoy.strftime("%Y-%m"), agencias=agencias, agencia_id=agencia_id)


def test_tablero_de_una_agencia_concilia_solicitados_con_el_calendario():
    hoy = date(2026, 10, 1)
    tablero = asyncio.run(TableroService(DemandaService(Repositorio(hoy)), ClickHouse(), Eventos())
                          .consultar(_tablero(hoy, MOCOA), hoy=hoy))

    assert tablero.fuente == "SIMULADA" and tablero.actualizado_en == CORTE
    assert tablero.calendario.agencia_id == MOCOA
    # Los solicitados incluyen los de hoy (MongoDB); los atendidos llegan por el ETL (ClickHouse).
    assert tablero.agencias[0].solicitados == tablero.resumen.solicitados == 7
    assert tablero.resumen.atendidos == 10


def test_tablero_general_suma_agencias_y_marca_las_que_no_respondieron():
    hoy = date(2026, 10, 1)
    tablero = asyncio.run(TableroService(DemandaService(Repositorio(hoy)), ClickHouse(), Eventos())
                          .consultar(_tablero(hoy), hoy=hoy))

    assert tablero.fuente == "SIMULADA"  # basta una agencia con datos de demostración
    assert [agencia.disponible for agencia in tablero.agencias] == [True, False]
    assert tablero.resumen.atendidos == 10
    assert tablero.calendario.agencia_id is None
    # Los niveles del total usan el historial sumado: un mes futuro también distingue alta y baja.
    niveles = {dia.nivel_demanda for dia in tablero.calendario.dias if dia.turnos_estimados is not None}
    assert niveles and niveles != {"MEDIA"}


def test_calendario_general_solo_exige_pronostico_de_agencias_habiles():
    hoy = date(2026, 10, 2)

    def dia(numero, habil, total):
        return DiaDemanda(fecha=date(2026, 10, numero), dia_habil=habil, fin_de_semana=numero == 3,
                          festivo_nacional=None, festivos_putumayo=(), vencimiento_credito=False,
                          eventos=[], turnos_registrados=None, turnos_estimados=total, nivel_demanda=None)

    agencias = [AgenciaTablero(agencia_id=MOCOA, nombre="Mocoa"), AgenciaTablero(agencia_id=ORITO, nombre="Orito")]
    mocoa = CalendarioDemanda(agencia_id=MOCOA, mes="2026-10", fuente="REAL", estado_pronostico="DISPONIBLE",
                              dias=[dia(2, False, None), dia(3, False, None), dia(5, True, 4)])
    orito = CalendarioDemanda(agencia_id=ORITO, mes="2026-10", fuente="REAL", estado_pronostico="HISTORIAL_INSUFICIENTE",
                              dias=[dia(2, True, 12), dia(3, False, None), dia(5, True, None)])
    general = calendario_general([mocoa, orito], agencias, hoy=hoy)

    assert general.dias[0].dia_habil and general.dias[0].turnos_estimados == 12
    assert not general.dias[1].dia_habil and general.dias[1].turnos_estimados is None
    assert general.dias[2].dia_habil and general.dias[2].turnos_estimados is None
    assert general.estado_pronostico == "INCOMPLETO"
    assert general.fuente == "REAL"
