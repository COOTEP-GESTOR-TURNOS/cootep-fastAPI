"""Consulta mensual común para indicadores, comparación y calendario."""

import asyncio
from datetime import date, datetime, timedelta

import httpx

from app.config import ZONA_AGENCIAS
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas import ValoresIndicadores
from app.schemas_demanda import (
    AgenciaTablero, CalendarioDemanda, DiaDemanda, EstadoPronostico, EventoCalendario,
    FuenteDemanda, IndicadoresAgencia, SolicitudTablero, TableroDemanda,
)
from app.services.demanda_service import DIAS_ENTRENAMIENTO, DIAS_PRONOSTICO, DemandaService, fechas_mes
from app.services.modelo_demanda import nivel_demanda, umbrales_demanda
from app.services.proyeccion_demo import ProyeccionDemo
from app.services.resumen_demo import InstantaneaDemo, TotalesDia


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
    return CalendarioDemanda(agencia_id=None, mes=calendarios[0].mes, fuente=calendarios[0].fuente,
                             dias=dias, estado_pronostico=estado)


class TableroService:
    def __init__(self, demanda: DemandaService, clickhouse: ClickHouseRepository, demo: ProyeccionDemo | None) -> None:
        self._demanda = demanda
        self._clickhouse = clickhouse
        self._demo = demo

    async def consultar(self, solicitud: SolicitudTablero, *, hoy: date | None = None) -> TableroDemanda:
        hoy = hoy or datetime.now(ZONA_AGENCIAS).date()
        mes = fechas_mes(solicitud.mes)
        desde, hasta = mes[0], mes[-1]
        inicio_entrenamiento = hoy - timedelta(days=DIAS_ENTRENAMIENTO)
        ids = [agencia.agencia_id for agencia in solicitud.agencias]
        instantanea = await self._demo.leer(
            ids, min(desde, inicio_entrenamiento), max(hasta, hoy), servicio_id=solicitud.servicio_id,
        ) if self._demo else None
        instantanea_indicadores = instantanea
        if self._demo and instantanea is not None and solicitud.colaborador_id is not None:
            instantanea_indicadores = await self._demo.leer(
                ids, desde, hasta, servicio_id=solicitud.servicio_id, colaborador_id=solicitud.colaborador_id,
                corte=instantanea.corte,
            ) if instantanea.corte else instantanea

        calendarios, (agencias, resumen) = await asyncio.gather(asyncio.gather(*(
            self._demanda.calendario(agencia.agencia_id, solicitud.mes, agencia.departamento, agencia.municipio,
                                     hoy=hoy, instantanea_demo=instantanea, activa=agencia.activa,
                                     servicio_id=solicitud.servicio_id, servicio_activo=agencia.servicio_activo)
            for agencia in solicitud.agencias
        )), self._resumen(solicitud, desde, hasta, instantanea_indicadores))

        if not self._demo and solicitud.colaborador_id is None:
            solicitudes = []
            for agencia, calendario in zip(agencias, calendarios, strict=True):
                registrados = [dia.turnos_registrados for dia in calendario.dias if dia.turnos_registrados is not None]
                if registrados:
                    agencia.solicitados = sum(registrados)
                    solicitudes.append(agencia.solicitados)
            if solicitudes:
                resumen.solicitados = sum(solicitudes)

        umbrales = None
        if instantanea and solicitud.agencia_id is None:
            por_dia: dict[date, int] = {}
            for conteos in instantanea.diarios.values():
                for fecha, total in conteos.items():
                    if inicio_entrenamiento <= fecha < hoy:
                        por_dia[fecha] = por_dia.get(fecha, 0) + total.solicitados
            positivos = [total for total in por_dia.values() if total > 0]
            if len(positivos) >= 2:
                umbrales = umbrales_demanda(positivos)

        fuente: FuenteDemanda = "SIMULADA" if self._demo else "REAL"
        calendario = calendarios[0] if solicitud.agencia_id else calendario_general(calendarios, solicitud.agencias, umbrales, hoy=hoy)
        return TableroDemanda(fuente=fuente, desde=desde, hasta=hasta, agencias=agencias, resumen=resumen,
                             actualizado_en=instantanea.corte.actualizado_en if instantanea and instantanea.corte else None,
                             calendario=calendario)

    async def _resumen(
        self, solicitud: SolicitudTablero, desde: date, hasta: date, instantanea: InstantaneaDemo | None,
    ) -> tuple[list[IndicadoresAgencia], ValoresIndicadores]:
        agencias = solicitud.agencias
        if instantanea:
            general = TotalesDia()
            indicadores = []
            disponible = instantanea.corte is not None
            for agencia in agencias:
                total = instantanea.total(agencia.agencia_id, desde, hasta)
                general.sumar(total)
                valores = total.valores() if disponible else _indicadores_vacios()
                indicadores.append(IndicadoresAgencia(agencia_id=agencia.agencia_id, nombre=agencia.nombre,
                                                       disponible=disponible, **valores.model_dump()))
            return indicadores, general.valores() if disponible else _indicadores_vacios()

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

        indicadores = await asyncio.gather(*(consultar_agencia(agencia) for agencia in agencias))
        return indicadores, _totalizar_operativos(indicadores)
