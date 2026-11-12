from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import configuracion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.repositories.eventos_repository import EventosRepository
from app.routers import analitica, salud


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Crea las conexiones y el pool de procesos al iniciar y los cierra al apagar."""
    cfg = configuracion()
    cliente_http = httpx.AsyncClient(base_url=cfg.clickhouse_url, timeout=5.0)
    mongo = AsyncIOMotorClient(cfg.mongo_url, serverSelectionTimeoutMS=2000)
    app.state.pool = ProcessPoolExecutor(max_workers=cfg.procesos_estadisticas)
    app.state.clickhouse = ClickHouseRepository(cliente_http, cfg)
    app.state.eventos = EventosRepository(mongo[cfg.mongo_db])
    try:
        yield
    finally:
        await cliente_http.aclose()
        mongo.close()
        app.state.pool.shutdown(wait=True, cancel_futures=True)


app = FastAPI(
    title="COOTEP Turno · analítica",
    description="Indicadores, recomendación de horario y parámetros de espera. Servicio interno.",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)
app.include_router(salud.router)
app.include_router(analitica.router)


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
