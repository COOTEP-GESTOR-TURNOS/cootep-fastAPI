"""Historial simulado y corte publicado para la proyección de demostración."""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import batched

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.demanda_repository import _fecha_bogota, _medianoche_utc
from app.repositories.eventos_repository import PROYECCION, compactar_evento
from app.services.hechos import EventoCompacto
from app.services.resumen_demo import CorteDemo

MARCA = "hechos_turno_demo"
NOMBRE_COLECCION = "eventos_turno_demo"


class EventosDemoRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._coleccion = db[NOMBRE_COLECCION]
        self._marcas = db["etl_marcas"]

    async def corte(self) -> tuple[CorteDemo | None, bool]:
        datos = await self._marcas.find_one({"_id": MARCA}) or {}
        corte = None
        if "generacion" in datos:
            corte = CorteDemo(
                datos["generacion"], datos["revision"], datos["hasta"].replace(tzinfo=timezone.utc),
                tuple((date.fromisoformat(desde), date.fromisoformat(hasta)) for desde, hasta in datos["intervalos"]),
                datos.get("version", 1),
            )
        return corte, datos.get("pausada", False)

    async def preparar(self) -> None:
        await self._marcas.update_one({"_id": MARCA}, {"$set": {"pausada": True}}, upsert=True)

    async def publicar(self, corte: CorteDemo) -> None:
        await self._marcas.update_one({"_id": MARCA}, {"$set": {
            "generacion": corte.generacion,
            "revision": corte.revision,
            "version": corte.version,
            "hasta": corte.actualizado_en,
            "intervalos": [(desde.isoformat(), hasta.isoformat()) for desde, hasta in corte.intervalos],
            "pausada": False,
        }}, upsert=True)

    async def dias_modificados(self, desde: datetime, hasta: datetime) -> set[date]:
        cambios = self._coleccion.aggregate([
            {"$match": {"publicadoEn": {"$gt": desde - timedelta(seconds=1), "$lte": hasta}}},
            {"$group": {"_id": "$turnoId"}},
            {"$lookup": {"from": NOMBRE_COLECCION, "localField": "_id", "foreignField": "turnoId",
                         "pipeline": [{"$match": {"estadoNuevo": "SOLICITADO"}}, {"$sort": {"ocurridoEn": 1}},
                                      {"$limit": 1}, {"$project": {"_id": 0, "ocurridoEn": 1}}],
                         "as": "solicitud"}},
            {"$unwind": "$solicitud"},
            {"$group": {"_id": {"$dateToString": {"date": "$solicitud.ocurridoEn", "format": "%Y-%m-%d",
                                                   "timezone": "America/Bogota"}}}},
        ])
        return {date.fromisoformat(cambio["_id"]) async for cambio in cambios}

    async def preparar_indices(self) -> None:
        await self._coleccion.create_index([("estadoNuevo", 1), ("ocurridoEn", 1)], name="ix_estado_ocurrido")

    async def eventos_por_fila(self, dias: set[date] | None, corte: datetime) -> dict[tuple[str, str], list[EventoCompacto]]:
        grupos: dict[tuple[str, str], list[EventoCompacto]] = defaultdict(list)
        # Solo se proyectan eventos publicados hasta el instante de esta revisión.
        filtro = {"$or": [{"publicadoEn": {"$lte": corte}}, {"publicadoEn": {"$exists": False}}]}
        if dias is None:
            await self._agrupar(grupos, filtro)
            return grupos

        for dia in sorted(dias):
            solicitudes = self._coleccion.find({
                **filtro, "estadoNuevo": "SOLICITADO",
                "ocurridoEn": {"$gte": _medianoche_utc(dia), "$lt": _medianoche_utc(dia + timedelta(days=1))},
            }, {"_id": 0, "turnoId": 1})
            turnos = {solicitud["turnoId"] async for solicitud in solicitudes}
            for lote in batched(turnos, 2_000):
                await self._agrupar(grupos, {**filtro, "turnoId": {"$in": list(lote)}})
        return grupos

    async def _agrupar(self, grupos: dict[tuple[str, str], list[EventoCompacto]], filtro: dict) -> None:
        async for evento in self._coleccion.find(filtro, PROYECCION, batch_size=5000):
            clave = (evento["agenciaId"], evento["servicioId"])
            grupos[clave].append(compactar_evento(evento))
