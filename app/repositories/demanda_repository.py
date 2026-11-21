"""Conteos de solicitudes y eventos del calendario, aislados por agencia."""

from collections import defaultdict
from datetime import date, datetime, time, timezone
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.config import ZONA_AGENCIAS
from app.schemas_demanda import CambiosEvento, EventoAgencia, NuevoEvento


def _medianoche_utc(fecha: date) -> datetime:
    return datetime.combine(fecha, time.min, tzinfo=ZONA_AGENCIAS).astimezone(timezone.utc)


def _fecha_bogota(instante: datetime) -> date:
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(ZONA_AGENCIAS).date()


class DemandaRepository:
    def __init__(self, db: AsyncIOMotorDatabase, demo: bool) -> None:
        self._solicitudes = db["eventos_turno"]
        self._eventos = db["eventos_agencia_demo" if demo else "eventos_agencia"]

    async def primer_dia(self, agencia_id: UUID, hasta: date, servicio_id: UUID | None = None) -> date | None:
        """Primera fecha registrada antes del límite exclusivo."""
        filtro = {"agenciaId": str(agencia_id), "estadoNuevo": "SOLICITADO", "ocurridoEn": {"$lt": _medianoche_utc(hasta)}}
        if servicio_id is not None:
            filtro["servicioId"] = str(servicio_id)
        registro = await self._solicitudes.find_one(
            filtro,
            {"_id": 0, "ocurridoEn": 1}, sort=[("ocurridoEn", 1)],
        )
        return _fecha_bogota(registro["ocurridoEn"]) if registro else None

    async def historial(self, agencia_id: UUID, desde: date, hasta: date, servicio_id: UUID | None = None) -> dict[date, int]:
        """Conteos desde inclusive hasta exclusivo. En producción se cuenta cada turno una vez."""
        filtro = {
            "agenciaId": str(agencia_id), "estadoNuevo": "SOLICITADO",
            "ocurridoEn": {"$gte": _medianoche_utc(desde), "$lt": _medianoche_utc(hasta)},
        }
        if servicio_id is not None:
            filtro["servicioId"] = str(servicio_id)
        cursor = self._solicitudes.aggregate([
            {"$match": filtro},
            {"$group": {"_id": "$turnoId", "instante": {"$min": "$ocurridoEn"}}},
            {"$group": {"_id": {"$dateToString": {
                "format": "%Y-%m-%d", "date": "$instante", "timezone": "America/Bogota",
            }}, "turnos": {"$sum": 1}}},
        ], maxTimeMS=4_000)
        return {date.fromisoformat(registro["_id"]): registro["turnos"] async for registro in cursor}

    async def eventos(self, agencia_id: UUID, desde: date, hasta: date) -> dict[date, list[EventoAgencia]]:
        cursor = self._eventos.find(
            {"agenciaId": str(agencia_id), "fecha": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}},
            {"_id": 0, "id": 1, "fecha": 1, "tipo": 1},
        ).sort([("fecha", 1), ("tipo", 1)])
        por_fecha: dict[date, list[EventoAgencia]] = defaultdict(list)
        async for registro in cursor:
            evento = EventoAgencia.model_validate(registro)
            por_fecha[evento.fecha].append(evento)
        return por_fecha

    async def crear_evento(self, agencia_id: UUID, solicitud: NuevoEvento) -> EventoAgencia:
        evento = EventoAgencia(id=uuid4(), fecha=solicitud.fecha, tipo=solicitud.tipo)
        await self._eventos.insert_one({"agenciaId": str(agencia_id), **evento.model_dump(mode="json")})
        return evento

    async def editar_evento(self, agencia_id: UUID, evento_id: UUID, cambios: CambiosEvento) -> EventoAgencia | None:
        registro = await self._eventos.find_one_and_update(
            {"agenciaId": str(agencia_id), "id": str(evento_id)},
            {"$set": cambios.model_dump(mode="json", exclude_unset=True)},
            projection={"_id": 0, "id": 1, "fecha": 1, "tipo": 1},
            return_document=ReturnDocument.AFTER,
        )
        return EventoAgencia.model_validate(registro) if registro else None

    async def eliminar_evento(self, agencia_id: UUID, evento_id: UUID) -> bool:
        resultado = await self._eventos.delete_one({"agenciaId": str(agencia_id), "id": str(evento_id)})
        return resultado.deleted_count == 1
