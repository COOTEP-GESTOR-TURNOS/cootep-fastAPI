"""Inyección de dependencias de FastAPI: los routers reciben servicios, no conexiones.
En las pruebas se reemplazan con app.dependency_overrides."""

from fastapi import Depends, Request

from app.config import configuracion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.repositories.demanda_repository import DemandaRepository
from app.repositories.eventos_repository import EventosRepository
from app.services.demanda_service import DemandaService
from app.services.estimacion_service import EstimacionService
from app.services.etl_service import EtlService
from app.services.proyeccion_demo import ProyeccionDemo
from app.services.recomendacion_service import RecomendacionService
from app.services.tablero_service import TableroService


def repositorio_clickhouse(request: Request) -> ClickHouseRepository:
    return request.app.state.clickhouse


def repositorio_eventos(request: Request) -> EventosRepository:
    return request.app.state.eventos


def repositorio_demanda(request: Request) -> DemandaRepository:
    return request.app.state.demanda


def servicio_demanda(repo: DemandaRepository = Depends(repositorio_demanda)) -> DemandaService:
    return DemandaService(repo, configuracion().demanda_demo)


def proyeccion_demo(request: Request) -> ProyeccionDemo:
    return request.app.state.proyeccion_demo


def proyeccion_demo_activa(request: Request) -> ProyeccionDemo | None:
    return proyeccion_demo(request) if configuracion().demanda_demo else None


def servicio_tablero(
    demanda: DemandaService = Depends(servicio_demanda),
    clickhouse: ClickHouseRepository = Depends(repositorio_clickhouse),
    demo: ProyeccionDemo | None = Depends(proyeccion_demo_activa),
) -> TableroService:
    return TableroService(demanda, clickhouse, demo)


def servicio_recomendacion(repo: ClickHouseRepository = Depends(repositorio_clickhouse)) -> RecomendacionService:
    return RecomendacionService(repo, configuracion())


def servicio_estimacion(repo: ClickHouseRepository = Depends(repositorio_clickhouse)) -> EstimacionService:
    return EstimacionService(repo, configuracion())


def servicio_etl(
    request: Request,
    eventos: EventosRepository = Depends(repositorio_eventos),
    clickhouse: ClickHouseRepository = Depends(repositorio_clickhouse),
) -> EtlService:
    return EtlService(eventos, clickhouse, request.app.state.pool, configuracion().procesos_estadisticas)
