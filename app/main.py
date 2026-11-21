import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Configuracion, configuracion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.repositories.demanda_repository import DemandaRepository
from app.repositories.eventos_repository import EventosRepository
from app.routers import analitica, demanda, salud, tablero
from app.services.etl_service import EtlService

logger = logging.getLogger(__name__)


async def mantenimiento(app: FastAPI, cfg: Configuracion) -> None:
    """Prepara índices y columnas, y mantiene ClickHouse al día con una carga incremental.

    Los volúmenes ya inicializados no vuelven a correr los scripts de infra/init: por eso los
    índices de MongoDB y la columna colaborador_id se aseguran aquí, al arrancar (son idempotentes).
    Corre en segundo plano para que el servicio arranque aunque un motor tarde en estar listo.
    """
    preparado = False
    while True:
        try:
            if not preparado:
                await app.state.demanda.preparar_indices()
                await app.state.clickhouse.preparar_responsables()
                preparado = True
            if cfg.etl_intervalo_seg > 0:
                await EtlService(app.state.eventos, app.state.clickhouse, app.state.pool, cfg.procesos_estadisticas).ejecutar()
        except Exception:
            logger.exception("Mantenimiento de la analítica: se reintenta en el siguiente ciclo")
        if preparado and cfg.etl_intervalo_seg <= 0:
            return
        await asyncio.sleep(cfg.etl_intervalo_seg if cfg.etl_intervalo_seg > 0 else 15)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Crea las conexiones y el pool de procesos al iniciar y los cierra al apagar."""
    cfg = configuracion()
    cliente_http = httpx.AsyncClient(base_url=cfg.clickhouse_url, timeout=5.0)
    mongo = AsyncIOMotorClient(cfg.mongo_url, serverSelectionTimeoutMS=2000)
    app.state.pool = ProcessPoolExecutor(max_workers=cfg.procesos_estadisticas)
    app.state.clickhouse = ClickHouseRepository(cliente_http, cfg)
    app.state.eventos = EventosRepository(mongo[cfg.mongo_db])
    app.state.demanda = DemandaRepository(mongo[cfg.mongo_db])
    tarea = asyncio.create_task(mantenimiento(app, cfg))
    try:
        yield
    finally:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea
        await cliente_http.aclose()
        mongo.close()
        app.state.pool.shutdown(wait=True, cancel_futures=True)


app = FastAPI(
    title="COOTEP Turno · analítica",
    description="Indicadores, demanda, recomendación de horario y parámetros de espera. Servicio interno.",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)
app.include_router(salud.router)
app.include_router(analitica.router)
app.include_router(demanda.router)
app.include_router(tablero.router)


def _problema(request: Request, status: int, titulo: str, detalle: str) -> JSONResponse:
    """Errores en formato Problem Details (RFC 9457), igual que los otros dos backends."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": titulo, "status": status, "detail": detalle, "instance": request.url.path},
    )


@app.exception_handler(StarletteHTTPException)
async def error_http(request: Request, error: StarletteHTTPException) -> JSONResponse:
    return _problema(request, error.status_code, "Error", str(error.detail))


@app.exception_handler(RequestValidationError)
async def error_validacion(request: Request, error: RequestValidationError) -> JSONResponse:
    detalle = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors())
    return _problema(request, 400, "Datos inválidos", detalle)


@app.exception_handler(httpx.HTTPError)
async def error_clickhouse(request: Request, error: httpx.HTTPError) -> JSONResponse:
    return _problema(request, 503, "Servicio no disponible", f"ClickHouse no respondió: {error.__class__.__name__}")


@app.exception_handler(PyMongoError)
async def error_mongo(request: Request, error: PyMongoError) -> JSONResponse:
    return _problema(request, 503, "Servicio no disponible", f"MongoDB no respondió: {error.__class__.__name__}")
