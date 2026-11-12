from uuid import UUID

from app.config import Configuracion
from app.repositories.clickhouse_repository import ClickHouseRepository
from app.schemas import Estimacion, ParametrosEspera, SolicitudEstimacion


def estimar_espera_min(
    personas_delante: int,
    t_atencion_min: float,
    ventanillas: int,
    p_preferencial: float,
    k: int,
    es_preferencial: bool,
) -> float | None:
    """Espera ≈ n · t̄ / (v · (1 − p)). Por la regla K, p nunca supera K / (K + 1)."""
    if ventanillas <= 0 or t_atencion_min <= 0 or personas_delante < 0:
        return None
    k = max(1, k)
    p = 0.0 if es_preferencial else min(max(p_preferencial, 0.0), k / (k + 1))
    return round(personas_delante * t_atencion_min / (ventanillas * (1 - p)), 1)


class EstimacionService:
    def __init__(self, repositorio: ClickHouseRepository, configuracion: Configuracion) -> None:
        self._repo = repositorio
        self._minimo = configuracion.min_muestras_franja

    async def parametros(self, agencia_id: UUID, servicio_id: UUID, franja: str) -> ParametrosEspera | None:
        """t̄ y p de la franja; None si no hay historial suficiente."""
        fila = await self._repo.parametros_franja(agencia_id, servicio_id, franja)
        if fila is None or fila["muestras"] < self._minimo or fila["atencion_mediana_seg"] <= 0:
            return None
        return ParametrosEspera(
            t_atencion_min=round(fila["atencion_mediana_seg"] / 60, 2),
            p_preferencial=round(fila["p_preferencial"], 3),
            muestras=fila["muestras"],
        )

    async def estimar(self, solicitud: SolicitudEstimacion) -> Estimacion:
        parametros = await self.parametros(solicitud.agencia_id, solicitud.servicio_id, solicitud.franja)
        if parametros is None:
            return Estimacion(espera_min=None, t_atencion_min=None, p_preferencial=None)
        espera = estimar_espera_min(
            solicitud.personas_delante,
            parametros.t_atencion_min,
            solicitud.ventanillas,
            parametros.p_preferencial,
            solicitud.max_preferenciales_seguidos,
            solicitud.tipo_atencion == "PREFERENCIAL",
        )
        return Estimacion(
            espera_min=espera, t_atencion_min=parametros.t_atencion_min, p_preferencial=parametros.p_preferencial
        )
