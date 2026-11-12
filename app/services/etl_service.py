import asyncio
import time
from concurrent.futures import Executor
from datetime import datetime, timezone

from app.repositories.clickhouse_repository import ClickHouseRepository
from app.repositories.eventos_repository import EventosRepository
from app.schemas import ResultadoEtl
from app.services.hechos import construir_hechos


class EtlService:
    """Carga MongoDB → ClickHouse.

    E/S con asyncio (Motor y httpx) y cálculo en procesos paralelos: cada fila es una tarea del
    ProcessPoolExecutor, lanzada con run_in_executor para no bloquear el bucle de eventos que
    atiende las peticiones (sección 6 del informe).
    """

    def __init__(self, eventos: EventosRepository, clickhouse: ClickHouseRepository, pool: Executor, procesos: int) -> None:
        self._eventos = eventos
        self._clickhouse = clickhouse
        self._pool = pool
        self._procesos = procesos

    async def ejecutar(self, completa: bool = False) -> ResultadoEtl:
        inicio = time.perf_counter()
        hasta = datetime.now(timezone.utc)
        desde = None if completa else await self._eventos.ultima_marca()

        por_fila = await self._eventos.eventos_por_fila(desde)
        bucle = asyncio.get_running_loop()
        resultados = await asyncio.gather(
            *(bucle.run_in_executor(self._pool, construir_hechos, lote) for lote in por_fila.items())
        )
        hechos = [hecho for lote in resultados for hecho in lote]
        cargados = await self._clickhouse.insertar_hechos(hechos)
        await self._eventos.guardar_marca(hasta)

        return ResultadoEtl(
            eventos_leidos=sum(len(eventos) for eventos in por_fila.values()),
            filas=len(por_fila),
            hechos_cargados=cargados,
            procesos=self._procesos,
            duracion_ms=int((time.perf_counter() - inicio) * 1000),
        )
