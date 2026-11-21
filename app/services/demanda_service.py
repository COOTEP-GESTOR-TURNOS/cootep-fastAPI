"""Calendario mensual con el historial y el pronóstico de demanda de una agencia."""

import asyncio
from calendar import monthrange
from datetime import date, datetime, timedelta
from uuid import UUID

from app.config import ZONA_AGENCIAS
from app.repositories.demanda_repository import DemandaRepository
from app.schemas_demanda import CalendarioDemanda, DiaDemanda, EstadoPronostico
from app.services.calendario_demanda import cierre_calendario, marcas_calendario
from app.services.modelo_demanda import entrenar, nivel_demanda, pronosticar, umbrales_demanda

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
    """Lee el historial de `eventos_turno` (reales y simulados por igual) y pronostica los días hábiles."""

    def __init__(self, repositorio: DemandaRepository) -> None:
        self._repositorio = repositorio

    async def totales_diarios(
        self, agencias: list[UUID], desde: date, hasta: date, servicio_id: UUID | None = None,
    ) -> dict[date, int]:
        """Turnos pedidos por día sumando las agencias (hasta exclusivo)."""
        totales: dict[date, int] = {}
        for historial in await asyncio.gather(*(
            self._repositorio.historial(agencia, desde, hasta, servicio_id) for agencia in agencias
        )):
            for fecha, conteo in historial.items():
                totales[fecha] = totales.get(fecha, 0) + conteo
        return totales

    async def calendario(
        self, agencia_id: UUID, mes: str, departamento: str | None, municipio: str | None, *, hoy: date | None = None,
        activa: bool = True, servicio_id: UUID | None = None, servicio_activo: bool = True,
    ) -> CalendarioDemanda:
        hoy = hoy or datetime.now(ZONA_AGENCIAS).date()
        fechas = fechas_mes(mes)
        observables = [fecha for fecha in fechas if fecha <= hoy]
        horizonte = [fecha for fecha in fechas if 0 <= (fecha - hoy).days < DIAS_PRONOSTICO]
        inicio_entrenamiento = hoy - timedelta(days=DIAS_ENTRENAMIENTO)

        desde_eventos = min(fechas[0], inicio_entrenamiento) if horizonte else fechas[0]
        lectura_eventos = self._repositorio.eventos(agencia_id, desde_eventos, fechas[-1])
        lectura_fuente = self._repositorio.fuente(agencia_id)
        primer_dia = None
        historial: dict[date, int] = {}
        if horizonte or observables:
            desde_historial = inicio_entrenamiento if horizonte else observables[0]
            hasta_historial = min(hoy, fechas[-1]) + timedelta(days=1)
            eventos, fuente, historial, primer_dia = await asyncio.gather(
                lectura_eventos,
                lectura_fuente,
                self._repositorio.historial(agencia_id, desde_historial, hasta_historial, servicio_id),
                self._repositorio.primer_dia(agencia_id, hoy + timedelta(days=1), servicio_id),
            )
        else:
            eventos, fuente = await asyncio.gather(lectura_eventos, lectura_fuente)

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
            return primer_dia is not None and primer_dia <= fecha <= hoy

        if predecibles:
            serie = {fecha: historial.get(fecha, 0) for fecha in _rango(inicio_entrenamiento, hoy) if fecha_registrada(fecha)}
            # El modelo solo aprende de los días que la agencia atendió.
            serie_habil = {
                fecha: total for fecha, total in serie.items()
                if not cierre_calendario(marcas_calendario(fecha, departamento, municipio), tipos.get(fecha, ()))
            }
            positivos = [conteo for conteo in serie_habil.values() if conteo > 0]
            if len(serie) >= MIN_COBERTURA and len(positivos) >= MIN_DIAS_POSITIVOS:
                modelo = await asyncio.to_thread(entrenar, serie_habil, tipos)
                estimaciones = await asyncio.to_thread(pronosticar, modelo, predecibles, tipos)
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
        return CalendarioDemanda(agencia_id=agencia_id, mes=mes, fuente=fuente, estado_pronostico=estado, dias=dias)
