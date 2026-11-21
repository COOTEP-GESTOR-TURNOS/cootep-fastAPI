"""API interna del tablero mensual y de los filtros históricos."""

from fastapi import APIRouter, Depends

from app.dependencias import repositorio_clickhouse, servicio_tablero
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas_demanda import ActividadFiltro, ConsultaFiltros, HistorialFiltros, SolicitudTablero, TableroDemanda
from app.seguridad import verificar_token_servicio
from app.services.tablero_service import TableroService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verificar_token_servicio)])


@router.post("/analitica/tablero", response_model=TableroDemanda, tags=["demanda"])
async def tablero(solicitud: SolicitudTablero, servicio: TableroService = Depends(servicio_tablero)) -> TableroDemanda:
    return await servicio.consultar(solicitud)


@router.post("/analitica/filtros", response_model=HistorialFiltros, tags=["demanda"])
async def filtros(
    consulta: ConsultaFiltros,
    repositorio: ClickHouseRepository = Depends(repositorio_clickhouse),
) -> HistorialFiltros:
    opciones = await repositorio.opciones_historicas(consulta.agencia_ids, consulta.desde, consulta.hasta)
    alcance = dict.fromkeys(
        (agencia, servicio, colaborador if consulta.servicio_id in (None, servicio) else None)
        for agencia, servicio, colaborador in opciones
    )
    return HistorialFiltros(opciones=[
        ActividadFiltro(agencia_id=agencia, servicio_id=servicio, colaborador_id=colaborador)
        for agencia, servicio, colaborador in alcance
    ])
