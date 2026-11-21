"""Publicación del corte común para el tablero y el calendario simulados."""

import asyncio
import logging
from concurrent.futures import Executor
from datetime import date, datetime, timedelta, timezone
from time import time_ns
from uuid import UUID, uuid4

from app.config import ZONA_AGENCIAS
from app.repositories.clickhouse_repository import ClickHouseRepository, OpcionHistorica
from app.repositories.eventos_demo_repository import EventosDemoRepository
from app.schemas_demanda import CoberturaDemo
from app.services.hechos import construir_hechos
from app.services.resumen_demo import CorteDemo, DimensionDia, InstantaneaDemo, TotalesDia, agregar_hechos

logger = logging.getLogger(__name__)


def unir_intervalos(intervalos: tuple[tuple[date, date], ...]) -> tuple[tuple[date, date], ...]:
    unidos: list[tuple[date, date]] = []
    for desde, hasta in sorted(intervalos):
        if unidos and (desde - unidos[-1][1]).days <= 1:
            primero, ultimo = unidos.pop()
            unidos.append((primero, max(ultimo, hasta)))
        else:
            unidos.append((desde, hasta))
    return tuple(unidos)


class ProyeccionDemo:
    def __init__(self, eventos: EventosDemoRepository, clickhouse: ClickHouseRepository, pool: Executor) -> None:
        self._eventos = eventos
        self._clickhouse = clickhouse
        self._pool = pool
        self._bloqueo = asyncio.Lock()

    async def preparar(self) -> None:
        async with self._bloqueo:
            await self._eventos.preparar()

    async def publicar(self, cobertura: CoberturaDemo) -> CorteDemo:
        async with self._bloqueo:
            anterior, _ = await self._eventos.corte()
            intervalos = unir_intervalos((anterior.intervalos if anterior else ()) + ((cobertura.desde, cobertura.hasta),))
            return await self._publicar_completo(intervalos)

    async def reconstruir(self) -> CorteDemo | None:
        """Migra los eventos existentes conservando el corte publicado hasta terminar."""
        async with self._bloqueo:
            anterior, pausada = await self._eventos.corte()
            if anterior is None or pausada:
                return None
            return await self._publicar_completo(anterior.intervalos)

    async def _publicar_completo(self, intervalos: tuple[tuple[date, date], ...]) -> CorteDemo:
        corte = CorteDemo(str(uuid4()), time_ns(), datetime.now(timezone.utc), intervalos)
        await self._eventos.preparar_indices()
        await self._clickhouse.preparar_tabla_demo()
        await self._calcular_y_publicar(corte, None)
        return corte

    async def refrescar(self) -> None:
        async with self._bloqueo:
            anterior, pausada = await self._eventos.corte()
            if anterior is None or pausada or anterior.version < 2:
                return
            instante = datetime.now(timezone.utc)
            dias = await self._eventos.dias_modificados(anterior.actualizado_en, instante)
            hoy = instante.astimezone(ZONA_AGENCIAS).date()
            ultimo = anterior.intervalos[-1][1]
            if not dias and hoy <= ultimo:
                return
            cobertura = anterior.intervalos
            if hoy > ultimo:
                cobertura = unir_intervalos(cobertura + ((ultimo, hoy),))
            corte = CorteDemo(anterior.generacion, max(time_ns(), anterior.revision + 1), instante, cobertura)
            await self._calcular_y_publicar(corte, dias)

    async def _calcular_y_publicar(self, corte: CorteDemo, dias: set[date] | None) -> None:
        grupos = await self._eventos.eventos_por_fila(dias, corte.actualizado_en)
        bucle = asyncio.get_running_loop()
        lotes = await asyncio.gather(*(
            bucle.run_in_executor(self._pool, construir_hechos, lote, True) for lote in grupos.items()
        ))
        diarios: dict[DimensionDia, TotalesDia] = {}
        for lote in lotes:
            for clave, total in agregar_hechos(lote).items():
                diarios.setdefault(clave, TotalesDia()).sumar(total)
        await self._clickhouse.guardar_diarios_demo(corte, diarios)
        # El corte se cambia cuando ClickHouse confirmó la inserción completa.
        await self._eventos.publicar(corte)

    async def leer(
        self, agencias: list[UUID], desde: date, hasta: date, servicio_id: UUID | None = None,
        colaborador_id: UUID | None = None, *, corte: CorteDemo | None = None,
    ) -> InstantaneaDemo:
        if corte is None:
            corte, _ = await self._eventos.corte()
        if corte is None or (corte.version < 2 and (servicio_id is not None or colaborador_id is not None)):
            return InstantaneaDemo(None, {})
        diarios = await self._clickhouse.diarios_demo(corte, agencias, desde, hasta, servicio_id, colaborador_id)
        return InstantaneaDemo(corte, diarios)

    async def opciones_historicas(
        self, agencias: list[UUID], desde: date, hasta: date, servicio_id: UUID | None = None,
    ) -> list[OpcionHistorica]:
        corte, _ = await self._eventos.corte()
        if corte is None:
            return []
        return await self._clickhouse.opciones_historicas_demo(corte, agencias, desde, hasta, servicio_id)

    async def ejecutar_en_segundo_plano(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.refrescar()
            except Exception:
                logger.exception("No fue posible actualizar el corte de demostración")
