"""Modelos (la M de MVC): contratos de entrada y salida. En JSON los campos van en camelCase."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Esquema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class FranjaRecomendada(Esquema):
    franja: str = Field(examples=["10:00"])
    espera_mediana_min: float
    turnos: int


class Recomendacion(Esquema):
    agencia_id: UUID
    servicio_id: UUID
    fecha: date
    tipo_dia: str
    franjas: list[FranjaRecomendada]


class ParametrosEspera(Esquema):
    """t̄ y p de la fórmula de espera para una fila y una franja."""

    t_atencion_min: float
    p_preferencial: float
    muestras: int


class SolicitudEstimacion(Esquema):
    agencia_id: UUID
    servicio_id: UUID
    franja: str = Field(pattern=r"^\d{2}:(00|30)$")
    personas_delante: int = Field(ge=0)
    ventanillas: int = Field(ge=1)
    max_preferenciales_seguidos: int = Field(default=2, ge=1, le=10)
    tipo_atencion: Literal["GENERAL", "PREFERENCIAL"] = "GENERAL"


class Estimacion(Esquema):
    espera_min: float | None
    t_atencion_min: float | None
    p_preferencial: float | None


class ValoresIndicadores(Esquema):
    atendidos: int
    cancelados: int
    ausentes: int
    espera_promedio_min: float
    atencion_promedio_min: float
    proporcion_preferencial: float
    solicitados: int | None = None
    pendientes: int | None = None


class Indicadores(ValoresIndicadores):
    agencia_id: UUID
    desde: date
    hasta: date
    fuente: Literal["REAL", "SIMULADA"] = "REAL"
    actualizado_en: datetime | None = None


class ResultadoEtl(Esquema):
    eventos_leidos: int
    filas: int
    hechos_cargados: int
    procesos: int
    duracion_ms: int
