"""Calendario mensual con el historial y el pronóstico de demanda de una agencia."""

import asyncio
from calendar import monthrange
from datetime import date, datetime, timedelta
from uuid import UUID

from app.config import ZONA_AGENCIAS
from app.repositories.demanda_repository import DemandaRepository
from app.schemas_demanda import CalendarioDemanda, DiaDemanda, EstadoPronostico, FuenteDemanda
from app.services.calendario_demanda import cierre_calendario, marcas_calendario
from app.services.modelo_demanda import entrenar, nivel_demanda, pronosticar, umbrales_demanda
from app.services.resumen_demo import InstantaneaDemo

DIAS_ENTRENAMIENTO = 365
DIAS_PRONOSTICO = 90
MIN_COBERTURA = 60
MIN_DIAS_POSITIVOS = 20


def _rango(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(days=desfase) for desfase in range((hasta - desde).days)]


def fechas_mes(mes: str) -> list[date]:
    anio, numero_mes = map(int, mes.split("-"))
    return [date(anio, numero_mes, dia) for dia in range(1, monthrange(anio, numero_mes)[1] + 1)]


class DemandaService:
    def __init__(self, repositorio: DemandaRepository, demo: bool) -> None:
        self._repositorio = repositorio
        self._fuente: FuenteDemanda = "SIMULADA" if demo else "REAL"

    async def calendario(
        self, agencia_id: UUID, mes: str, departamento: str | None, municipio: str | None, *, hoy: date | None = None,
        instantanea_demo: InstantaneaDemo | None = None, activa: bool = True,
        servicio_id: UUID | None = None, servicio_activo: bool = True,
    ) -> CalendarioDemanda:
        hoy = hoy or datetime.now(ZONA_AGENCIAS).date()
        fechas = fechas_mes(mes)
        observables = [fecha for fecha in fechas if fecha <= hoy]
        horizonte = [fecha for fecha in fechas if 0 <= (fecha - hoy).days < DIAS_PRONOSTICO]
        inicio_entrenamiento = hoy - timedelta(days=DIAS_ENTRENAMIENTO)

        desde_eventos = min(fechas[0], inicio_entrenamiento) if horizonte else fechas[0]
        hasta_eventos = fechas[-1]
        lectura_eventos = self._repositorio.eventos(agencia_id, desde_eventos, hasta_eventos)
        primer_dia = None
        historial: dict[date, int] = {}
        if self._fuente == "SIMULADA":
            eventos = await lectura_eventos
            if instantanea_demo:
                historial = {fecha: total.solicitados for fecha, total in instantanea_demo.diarios.get(agencia_id, {}).items()}
        elif horizonte or observables:
            desde_historial = inicio_entrenamiento if horizonte else observables[0]
            hasta_historial = min(hoy, fechas[-1]) + timedelta(days=1)
            fin_observacion = hoy + timedelta(days=1)
            eventos, historial, primer_dia = await asyncio.gather(
                lectura_eventos,
                self._repositorio.historial(agencia_id, desde_historial, hasta_historial, servicio_id),
                self._repositorio.primer_dia(agencia_id, fin_observacion, servicio_id),
            )
        else:
            eventos = await lectura_eventos

        tipos = {fecha: {evento.tipo for evento in registros} for fecha, registros in eventos.items()}
        marcas_por_fecha = {fecha: marcas_calendario(fecha, departamento, municipio) for fecha in fechas}
        habiles = {
            fecha for fecha, marcas in marcas_por_fecha.items()
            if (fecha < hoy or (activa and servicio_activo)) and not cierre_calendario(marcas, tipos.get(fecha, ()))
        }
        predecibles = [fecha for fecha in horizonte if fecha in habiles]
        estimaciones: dict[date, int] = {}
        estado: EstadoPronostico = "HISTORIAL_INSUFICIENTE" if predecibles else "SOLO_CALENDARIO"
        umbrales: tuple[float, float] | None = None

        def fecha_registrada(fecha: date) -> bool:
            if self._fuente == "SIMULADA":
                return bool(instantanea_demo and fecha <= hoy and (fecha in historial or instantanea_demo.cubre(fecha)))
            return primer_dia is not None and primer_dia <= fecha <= hoy

        if predecibles:
            serie = {fecha: historial.get(fecha, 0) for fecha in _rango(inicio_entrenamiento, hoy) if fecha_registrada(fecha)}
            positivos = [conteo for conteo in serie.values() if conteo > 0]
            if len(serie) >= MIN_COBERTURA and len(positivos) >= MIN_DIAS_POSITIVOS:
                modelo = await asyncio.to_thread(entrenar, serie, tipos, departamento, municipio)
                estimaciones = await asyncio.to_thread(pronosticar, modelo, predecibles, tipos, departamento, municipio)
                umbrales = umbrales_demanda(positivos)
                estado = "DISPONIBLE"
        dias = []
        for fecha in fechas:
            marcas = marcas_por_fecha[fecha]
            estimacion = estimaciones.get(fecha)
            dias.append(DiaDemanda(
                fecha=fecha,
                dia_habil=fecha in habiles,
                fin_de_semana=marcas.fin_de_semana,
                festivo_nacional=marcas.festivo_nacional,
                festivos_putumayo=marcas.festivos_putumayo,
                vencimiento_credito=marcas.vencimiento_credito,
                eventos=eventos.get(fecha, []),
                turnos_registrados=historial.get(fecha, 0) if fecha_registrada(fecha) else None,
                turnos_estimados=estimacion,
                nivel_demanda=nivel_demanda(estimacion, umbrales or (0, 0)) if estimacion is not None else None,
            ))
        return CalendarioDemanda(agencia_id=agencia_id, mes=mes, fuente=self._fuente, estado_pronostico=estado, dias=dias)
