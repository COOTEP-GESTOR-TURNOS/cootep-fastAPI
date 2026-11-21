"""API interna de demanda. NestJS valida el acceso del usuario a la agencia."""

from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import ZONA_AGENCIAS
from app.dependencias import repositorio_demanda, servicio_demanda
from app.repositories.demanda_repository import DemandaRepository
from app.schemas_demanda import CalendarioDemanda, CambiosEvento, EventoAgencia, NuevoEvento
from app.seguridad import verificar_token_servicio
from app.services.demanda_service import DemandaService

router = APIRouter(
    prefix="/api/v1/analitica/agencias/{agencia_id}", tags=["demanda"],
    dependencies=[Depends(verificar_token_servicio)],
)


@contextmanager
def _traducir_errores_mongo():
    try:
        yield
    except DuplicateKeyError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un evento de ese tipo para esta fecha") from error
    except PyMongoError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "El historial de demanda no está disponible") from error


@router.get("/demanda", response_model=CalendarioDemanda)
async def calendario(
    agencia_id: UUID,
    mes: str = Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
    departamento: str | None = Query(default=None),
    municipio: str | None = Query(default=None),
    activa: bool = Query(default=True),
    servicio_id: UUID | None = Query(default=None, alias="servicioId"),
    servicio_activo: bool = Query(default=True, alias="servicioActivo"),
    servicio: DemandaService = Depends(servicio_demanda),
) -> CalendarioDemanda:
    if mes.startswith("0000"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Año inválido")
    with _traducir_errores_mongo():
        return await servicio.calendario(
            agencia_id, mes, departamento, municipio, hoy=datetime.now(ZONA_AGENCIAS).date(), activa=activa,
            servicio_id=servicio_id, servicio_activo=servicio_activo,
        )


@router.post("/eventos", response_model=EventoAgencia, status_code=status.HTTP_201_CREATED)
async def crear_evento(
    agencia_id: UUID, solicitud: NuevoEvento, repositorio: DemandaRepository = Depends(repositorio_demanda),
) -> EventoAgencia:
    with _traducir_errores_mongo():
        return await repositorio.crear_evento(agencia_id, solicitud)


@router.patch("/eventos/{evento_id}", response_model=EventoAgencia)
async def editar_evento(
    agencia_id: UUID, evento_id: UUID, cambios: CambiosEvento,
    repositorio: DemandaRepository = Depends(repositorio_demanda),
) -> EventoAgencia:
    with _traducir_errores_mongo():
        evento = await repositorio.editar_evento(agencia_id, evento_id, cambios)
    if evento is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No se encontró el evento en esta agencia")
    return evento


@router.delete("/eventos/{evento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_evento(
    agencia_id: UUID, evento_id: UUID, repositorio: DemandaRepository = Depends(repositorio_demanda),
) -> Response:
    with _traducir_errores_mongo():
        eliminado = await repositorio.eliminar_evento(agencia_id, evento_id)
    if not eliminado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No se encontró el evento en esta agencia")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
