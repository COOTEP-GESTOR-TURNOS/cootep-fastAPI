from datetime import date, datetime
from uuid import UUID

from app.config import ZONA_AGENCIAS, Configuracion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas import FranjaRecomendada, Recomendacion
from app.services.calendario import franja_de, tipo_dia


def ordenar_franjas(filas: list[dict], minimo_turnos: int, cantidad: int = 3, desde: str | None = None) -> list[FranjaRecomendada]:
    """Franjas con menor espera mediana; descarta las que tienen pocos datos o ya pasaron."""
    candidatas = [f for f in filas if f["turnos"] >= minimo_turnos and (desde is None or f["franja"] >= desde)]
    candidatas.sort(key=lambda f: (f["espera_mediana_seg"], -f["turnos"]))
    return [
        FranjaRecomendada(
            franja=f["franja"], espera_mediana_min=round(f["espera_mediana_seg"] / 60, 1), turnos=f["turnos"]
        )
        for f in candidatas[:cantidad]
    ]


class RecomendacionService:
    """¿A qué hora conviene ir? Estadística por franja en ClickHouse, sin machine learning."""

    def __init__(self, repositorio: ClickHouseRepository, configuracion: Configuracion) -> None:
        self._repo = repositorio
        self._minimo = configuracion.min_muestras_franja

    async def recomendar(self, agencia_id: UUID, servicio_id: UUID, fecha: date, ahora: datetime | None = None) -> Recomendacion:
        tipo = tipo_dia(fecha)
        filas = await self._repo.espera_por_franja(agencia_id, servicio_id, fecha.isoweekday(), tipo)
        ahora_local = (ahora or datetime.now(ZONA_AGENCIAS)).astimezone(ZONA_AGENCIAS)
        desde = franja_de(ahora_local) if fecha == ahora_local.date() else None
        return Recomendacion(
            agencia_id=agencia_id,
            servicio_id=servicio_id,
            fecha=fecha,
            tipo_dia=tipo,
            franjas=ordenar_franjas(filas, self._minimo, 3, desde),
        )
