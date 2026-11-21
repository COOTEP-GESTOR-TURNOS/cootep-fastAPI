"""API interna del tablero mensual y de la publicación de datos ficticios."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import ZONA_AGENCIAS
from app.dependencias import proyeccion_demo, proyeccion_demo_activa, repositorio_clickhouse, servicio_tablero
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas_demanda import ActividadFiltro, CoberturaDemo, ConsultaFiltros, HistorialFiltros, SolicitudTablero, TableroDemanda
from app.seguridad import verificar_token_servicio
from app.services.proyeccion_demo import ProyeccionDemo
from app.services.tablero_service import TableroService

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verificar_token_servicio)])


@router.post("/analitica/tablero", response_model=TableroDemanda, tags=["demanda"])
async def tablero(solicitud: SolicitudTablero, servicio: TableroService = Depends(servicio_tablero)) -> TableroDemanda:
    return await servicio.consultar(solicitud)


@router.post("/analitica/filtros", response_model=HistorialFiltros, tags=["demanda"])
async def filtros(
    consulta: ConsultaFiltros,
    repositorio: ClickHouseRepository = Depends(repositorio_clickhouse),
    demo: ProyeccionDemo | None = Depends(proyeccion_demo_activa),
) -> HistorialFiltros:
    fuente = demo if demo else repositorio
    opciones = await fuente.opciones_historicas(consulta.agencia_ids, consulta.desde, consulta.hasta)
    alcance = dict.fromkeys(
        (agencia, servicio, colaborador if consulta.servicio_id in (None, servicio) else None)
        for agencia, servicio, colaborador in opciones
    )
    return HistorialFiltros(opciones=[
        ActividadFiltro(agencia_id=agencia, servicio_id=servicio, colaborador_id=colaborador)
        for agencia, servicio, colaborador in alcance
    ])


@router.post("/etl/demo/preparar", status_code=status.HTTP_204_NO_CONTENT, tags=["etl"])
async def preparar(proyeccion: ProyeccionDemo = Depends(proyeccion_demo)) -> Response:
    await proyeccion.preparar()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/etl/demo/publicar", tags=["etl"])
async def publicar(cobertura: CoberturaDemo, proyeccion: ProyeccionDemo = Depends(proyeccion_demo)) -> dict:
    if cobertura.hasta > datetime.now(ZONA_AGENCIAS).date():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Solo se pueden publicar turnos hasta hoy")
    corte = await proyeccion.publicar(cobertura)
    return {"actualizadoEn": corte.actualizado_en}


@router.post("/etl/demo/reconstruir", tags=["etl"])
async def reconstruir(proyeccion: ProyeccionDemo = Depends(proyeccion_demo)) -> dict:
    corte = await proyeccion.reconstruir()
    if corte is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay un corte de demostración listo para reconstruir")
    return {"actualizadoEn": corte.actualizado_en}
