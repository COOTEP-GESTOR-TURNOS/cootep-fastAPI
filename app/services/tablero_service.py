"""Consulta mensual común para indicadores, comparación y calendario."""

import asyncio
from datetime import date, datetime, timedelta

import httpx

from app.config import ZONA_AGENCIAS
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.repositories.eventos_repository import EventosRepository
from app.schemas import ValoresIndicadores
from app.schemas_demanda import (
    AgenciaTablero, CalendarioDemanda, DiaDemanda, EstadoPronostico, EventoCalendario,
    FuenteDemanda, IndicadoresAgencia, SolicitudTablero, TableroDemanda,
)
from app.services.demanda_service import DIAS_ENTRENAMIENTO, DIAS_PRONOSTICO, DemandaService, fechas_mes
from app.services.modelo_demanda import nivel_demanda, umbrales_demanda


def _indicadores_vacios() -> ValoresIndicadores:
    return ValoresIndicadores(atendidos=0, cancelados=0, ausentes=0, espera_promedio_min=0,
                              atencion_promedio_min=0, proporcion_preferencial=0)


def _totalizar_operativos(agencias: list[IndicadoresAgencia]) -> ValoresIndicadores:
    disponibles = [agencia for agencia in agencias if agencia.disponible]
    atendidos = sum(agencia.atendidos for agencia in disponibles)
    cancelados = sum(agencia.cancelados for agencia in disponibles)
    ausentes = sum(agencia.ausentes for agencia in disponibles)
    finalizados = atendidos + ausentes + cancelados
    return ValoresIndicadores(
        atendidos=atendidos, ausentes=ausentes, cancelados=cancelados,
        espera_promedio_min=round(sum(a.atendidos * a.espera_promedio_min for a in disponibles) / atendidos, 1) if atendidos else 0,
        atencion_promedio_min=round(sum(a.atendidos * a.atencion_promedio_min for a in disponibles) / atendidos, 1) if atendidos else 0,
        proporcion_preferencial=round(sum(
            (a.atendidos + a.ausentes + a.cancelados) * a.proporcion_preferencial for a in disponibles
        ) / finalizados, 3) if finalizados else 0,
    )


def _fuente(calendarios: list[CalendarioDemanda]) -> FuenteDemanda:
    return "SIMULADA" if any(calendario.fuente == "SIMULADA" for calendario in calendarios) else "REAL"


def calendario_general(
    calendarios: list[CalendarioDemanda], agencias: list[AgenciaTablero], umbrales: tuple[float, float] | None = None,
    *, hoy: date | None = None,
) -> CalendarioDemanda:
    hoy = hoy or datetime.now(ZONA_AGENCIAS).date()
    agrupados = list(zip(*(calendario.dias for calendario in calendarios), strict=True))
    if umbrales is None:
        positivos = [sum(dia.turnos_registrados or 0 for dia in grupo) for grupo in agrupados]
        positivos = [total for total in positivos if total > 0]
        umbrales = umbrales_demanda(positivos) if len(positivos) >= 2 else None

    dias: list[DiaDemanda] = []
    dias_predecibles = 0
    dias_con_pronostico = 0
    agencias_con_pronostico = 0
    for grupo in agrupados:
        observado = [dia.turnos_registrados for dia in grupo if dia.turnos_registrados is not None]
        marcas = grupo[0]
        abiertas = [dia for dia in grupo if dia.dia_habil]
        estimados = [dia.turnos_estimados for dia in abiertas if dia.turnos_estimados is not None]
        estimado = sum(estimados) if abiertas and len(estimados) == len(abiertas) else None
        if abiertas and 0 <= (marcas.fecha - hoy).days < DIAS_PRONOSTICO:
            dias_predecibles += 1
            dias_con_pronostico += estimado is not None
            agencias_con_pronostico += len(estimados)
        dias.append(DiaDemanda(
            fecha=marcas.fecha, dia_habil=bool(abiertas), fin_de_semana=marcas.fin_de_semana, festivo_nacional=marcas.festivo_nacional,
            festivos_putumayo=tuple(sorted({nombre for dia in grupo for nombre in dia.festivos_putumayo})),
            vencimiento_credito=marcas.vencimiento_credito,
            eventos=[EventoCalendario(id=evento.id, fecha=evento.fecha, tipo=evento.tipo,
                                      agencia_id=agencia.agencia_id, agencia_nombre=agencia.nombre)
                     for dia, agencia in zip(grupo, agencias, strict=True) for evento in dia.eventos],
            turnos_registrados=sum(observado) if observado else None,
            turnos_estimados=estimado,
            nivel_demanda=nivel_demanda(estimado, umbrales or (estimado, estimado)) if estimado is not None else None,
        ))

    estado: EstadoPronostico
    if not dias_predecibles:
        estado = "SOLO_CALENDARIO"
    elif dias_predecibles == dias_con_pronostico:
        estado = "DISPONIBLE"
    elif agencias_con_pronostico:
        estado = "INCOMPLETO"
    else:
        estado = "HISTORIAL_INSUFICIENTE"
    return CalendarioDemanda(agencia_id=None, mes=calendarios[0].mes, fuente=_fuente(calendarios),
                             dias=dias, estado_pronostico=estado)


class TableroService:
    def __init__(self, demanda: DemandaService, clickhouse: ClickHouseRepository, eventos: EventosRepository) -> None:
        self._demanda = demanda
        self._clickhouse = clickhouse
        self._eventos = eventos

    async def consultar(self, solicitud: SolicitudTablero, *, hoy: date | None = None) -> TableroDemanda:
        hoy = hoy or datetime.now(ZONA_AGENCIAS).date()
        mes = fechas_mes(solicitud.mes)
        desde, hasta = mes[0], mes[-1]

        calendarios, (agencias, resumen), corte = await asyncio.gather(
            asyncio.gather(*(
                self._demanda.calendario(agencia.agencia_id, solicitud.mes, agencia.departamento, agencia.municipio,
                                         hoy=hoy, activa=agencia.activa,
                                         servicio_id=solicitud.servicio_id, servicio_activo=agencia.servicio_activo)
                for agencia in solicitud.agencias
            )),
            self._resumen(solicitud, desde, hasta),
            self._eventos.ultima_marca(),
        )

        # Los solicitados salen del calendario (MongoDB): incluyen los turnos de hoy que el ETL aún no cargó.
        if solicitud.colaborador_id is None:
            solicitudes = []
            for agencia, calendario in zip(agencias, calendarios, strict=True):
                registrados = [dia.turnos_registrados for dia in calendario.dias if dia.turnos_registrados is not None]
                if registrados:
                    agencia.solicitados = sum(registrados)
                    solicitudes.append(agencia.solicitados)
            if solicitudes:
                resumen.solicitados = sum(solicitudes)

        if solicitud.agencia_id:
            calendario = calendarios[0]
        else:
            umbrales = await self._umbrales_generales(solicitud, hoy)
            calendario = calendario_general(calendarios, solicitud.agencias, umbrales, hoy=hoy)
        return TableroDemanda(fuente=_fuente(calendarios), actualizado_en=corte, desde=desde, hasta=hasta,
                             agencias=agencias, resumen=resumen, calendario=calendario)

    async def _umbrales_generales(self, solicitud: SolicitudTablero, hoy: date) -> tuple[float, float] | None:
        """Niveles del total de todas las agencias, con la misma ventana que el pronóstico de cada una."""
        totales = await self._demanda.totales_diarios(
            [agencia.agencia_id for agencia in solicitud.agencias], hoy - timedelta(days=DIAS_ENTRENAMIENTO), hoy,
            solicitud.servicio_id,
        )
        positivos = [total for total in totales.values() if total > 0]
        return umbrales_demanda(positivos) if len(positivos) >= 2 else None

    async def _resumen(
        self, solicitud: SolicitudTablero, desde: date, hasta: date,
    ) -> tuple[list[IndicadoresAgencia], ValoresIndicadores]:
        async def consultar_agencia(agencia: AgenciaTablero) -> IndicadoresAgencia:
            try:
                valores = await self._clickhouse.indicadores(
                    agencia.agencia_id, desde, hasta, servicio_id=solicitud.servicio_id, colaborador_id=solicitud.colaborador_id,
                )
                disponible = True
            except httpx.HTTPError:
                valores = _indicadores_vacios().model_dump()
                disponible = False
            return IndicadoresAgencia(agencia_id=agencia.agencia_id, nombre=agencia.nombre,
                                       disponible=disponible, **valores)

        indicadores = await asyncio.gather(*(consultar_agencia(agencia) for agencia in solicitud.agencias))
        return indicadores, _totalizar_operativos(indicadores)
