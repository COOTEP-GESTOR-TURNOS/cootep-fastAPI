"""Controladores (la C de MVC). Solo validan la entrada y delegan en los servicios."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencias import repositorio_clickhouse, servicio_estimacion, servicio_etl, servicio_recomendacion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas import Estimacion, Indicadores, ParametrosEspera, Recomendacion, ResultadoEtl, SolicitudEstimacion
from app.seguridad import verificar_token_servicio
from app.services.estimacion_service import EstimacionService
from app.services.etl_service import EtlService
from app.services.recomendacion_service import RecomendacionService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verificar_token_servicio)])


@router.get("/recomendaciones", response_model=Recomendacion, tags=["recomendaciones"])
async def recomendar(
    agencia_id: UUID = Query(alias="agenciaId"),
    servicio_id: UUID = Query(alias="servicioId"),
    fecha: date = Query(),
    servicio: RecomendacionService = Depends(servicio_recomendacion),
) -> Recomendacion:
    """Las 3 franjas con menor espera mediana para ese día de la semana y tipo de día."""
    return await servicio.recomendar(agencia_id, servicio_id, fecha)


@router.get("/estimaciones/parametros", response_model=ParametrosEspera, tags=["estimaciones"])
async def parametros_espera(
    agencia_id: UUID = Query(alias="agenciaId"),
    servicio_id: UUID = Query(alias="servicioId"),
    franja: str = Query(pattern=r"^\d{2}:(00|30)$"),
    servicio: EstimacionService = Depends(servicio_estimacion),
) -> ParametrosEspera:
    """t̄ y p de la franja; 404 si no hay historial suficiente."""
    parametros = await servicio.parametros(agencia_id, servicio_id, franja)
    if parametros is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sin historial suficiente para esa franja")
    return parametros


@router.post("/estimaciones", response_model=Estimacion, tags=["estimaciones"])
async def estimar(solicitud: SolicitudEstimacion, servicio: EstimacionService = Depends(servicio_estimacion)) -> Estimacion:
    return await servicio.estimar(solicitud)


@router.get("/indicadores", response_model=Indicadores, tags=["indicadores"])
async def indicadores(
    agencia_id: UUID = Query(alias="agenciaId"),
    desde: date = Query(),
    hasta: date = Query(),
    repositorio: ClickHouseRepository = Depends(repositorio_clickhouse),
) -> Indicadores:
    if desde > hasta:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "desde no puede ser posterior a hasta")
    valores = await repositorio.indicadores(agencia_id, desde, hasta)
    return Indicadores(agencia_id=agencia_id, desde=desde, hasta=hasta, **valores)


@router.post("/etl/ejecutar", response_model=ResultadoEtl, tags=["etl"])
async def ejecutar_etl(completa: bool = Query(default=False), servicio: EtlService = Depends(servicio_etl)) -> ResultadoEtl:
    """Carga los eventos nuevos (o todos, con completa=true) de MongoDB a ClickHouse."""
    return await servicio.ejecutar(completa)
