from datetime import timedelta, timezone
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Colombia usa UTC-5 todo el año (sin horario de verano), así que basta un desfase fijo.
ZONA_AGENCIAS = timezone(timedelta(hours=-5), "America/Bogota")


class Configuracion(BaseSettings):
    """Se lee de variables de entorno (en Docker, de infra/compose.apps.yml) o de un archivo .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_url: str = "mongodb://localhost:27017/cootep"
    mongo_db: str = "cootep"
    clickhouse_url: str = "http://localhost:8123"
    clickhouse_user: str = "cootep"
    clickhouse_password: str = ""
    service_token: str = ""
    # Procesos para el cálculo paralelo de hechos (ProcessPoolExecutor).
    procesos_estadisticas: int = 4
    # Turnos atendidos mínimos para confiar en la estadística de una franja.
    min_muestras_franja: int = 20
    # Carga incremental MongoDB → ClickHouse en segundo plano; 0 la apaga.
    etl_intervalo_seg: int = 60
    # El generador de datos de demostración se niega a correr salvo con ENTORNO=desarrollo.
    entorno: str = "produccion"


@lru_cache
def configuracion() -> Configuracion:
    return Configuracion()
