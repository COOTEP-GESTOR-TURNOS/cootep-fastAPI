"""Lectura de los eventos de turnos en MongoDB (asíncrona con Motor)."""

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.hechos import EventoCompacto

PROYECCION = {"_id": 0, "turnoId": 1, "agenciaId": 1, "servicioId": 1, "estadoNuevo": 1, "ocurridoEn": 1,
              "tipoAtencion": 1, "canal": 1}
MARCA = "hechos_turno"


class EventosRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._eventos = db["eventos_turno"]
        self._marcas = db["etl_marcas"]

    async def eventos_por_fila(self, desde: datetime | None) -> dict[tuple[str, str], list[EventoCompacto]]:
        """Eventos agrupados por fila (agencia, servicio) como tuplas compactas.

        Un turno vive dentro de un día de atención, así que en la carga incremental se relee desde
        24 h antes de la marca: los turnos que cruzan la marca quedan completos.
        """
        filtro = {"ocurridoEn": {"$gt": desde - timedelta(hours=24)}} if desde else {}
        grupos: dict[tuple[str, str], list[EventoCompacto]] = defaultdict(list)
        async for e in self._eventos.find(filtro, PROYECCION, batch_size=5000):
            clave = (sys.intern(e["agenciaId"]), sys.intern(e["servicioId"]))
            grupos[clave].append(
                (
                    sys.intern(e["turnoId"]),
                    e["estadoNuevo"],
                    e["ocurridoEn"].replace(tzinfo=timezone.utc).timestamp(),
                    e.get("tipoAtencion", "GENERAL"),
                    e.get("canal", "WEB"),
                )
            )
        return grupos

    async def ultima_marca(self) -> datetime | None:
        documento = await self._marcas.find_one({"_id": MARCA})
        return documento["hasta"].replace(tzinfo=timezone.utc) if documento else None

    async def guardar_marca(self, hasta: datetime) -> None:
        await self._marcas.update_one({"_id": MARCA}, {"$set": {"hasta": hasta}}, upsert=True)
