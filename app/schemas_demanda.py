"""Contratos REST para el calendario y los eventos de demanda."""

import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, ConfigDict, Field, model_validator

from app.schemas import Esquema, ValoresIndicadores

TipoEvento = Literal["FESTIVO_LOCAL", "PAGO_SUBSIDIO_FAMILIAR", "PAGO_NOMINA"]
NivelDemanda = Literal["BAJA", "MEDIA", "ALTA"]
EstadoPronostico = Literal["DISPONIBLE", "HISTORIAL_INSUFICIENTE", "SOLO_CALENDARIO", "INCOMPLETO"]
FuenteDemanda = Literal["REAL", "SIMULADA"]


def _fecha_civil(valor: object) -> date:
    if type(valor) is date:
        return valor
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", valor):
        raise ValueError("Use una fecha con formato AAAA-MM-DD")
    return date.fromisoformat(valor)


FechaCivil = Annotated[date, BeforeValidator(_fecha_civil)]


class NuevoEvento(Esquema):
    model_config = ConfigDict(extra="forbid")

    fecha: FechaCivil
    tipo: TipoEvento


class CambiosEvento(Esquema):
    model_config = ConfigDict(extra="forbid")

    fecha: FechaCivil | None = None
    tipo: TipoEvento | None = None

    @model_validator(mode="after")
    def validar_cambios(self) -> "CambiosEvento":
        if not self.model_fields_set or any(getattr(self, campo) is None for campo in self.model_fields_set):
            raise ValueError("Indique una fecha o un tipo de evento válido")
        return self


class EventoAgencia(Esquema):
    id: UUID
    fecha: date
    tipo: TipoEvento


class EventoCalendario(EventoAgencia):
    agencia_id: UUID
    agencia_nombre: str


class DiaDemanda(Esquema):
    fecha: date
    dia_habil: bool
    fin_de_semana: bool
    festivo_nacional: str | None
    festivos_putumayo: tuple[str, ...]
    vencimiento_credito: bool
    eventos: list[EventoCalendario | EventoAgencia]
    turnos_registrados: int | None
    turnos_estimados: int | None
    nivel_demanda: NivelDemanda | None


class CalendarioDemanda(Esquema):
    agencia_id: UUID | None
    mes: str
    fuente: FuenteDemanda
    estado_pronostico: EstadoPronostico
    dias: list[DiaDemanda]


class AgenciaTablero(Esquema):
    agencia_id: UUID
    nombre: str
    departamento: str | None = None
    municipio: str | None = None
    activa: bool = True
    servicio_activo: bool = True


MesCivil = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class SolicitudTablero(Esquema):
    mes: MesCivil
    agencias: list[AgenciaTablero]
    agencia_id: UUID | None = None
    servicio_id: UUID | None = None
    colaborador_id: UUID | None = None

    @model_validator(mode="after")
    def validar_ambito(self) -> "SolicitudTablero":
        ids = {agencia.agencia_id for agencia in self.agencias}
        if self.mes.startswith("0000") or not ids or len(ids) != len(self.agencias):
            raise ValueError("Indique un mes y una lista de agencias sin duplicados")
        if self.agencia_id is not None and ids != {self.agencia_id}:
            raise ValueError("La agencia no corresponde al ámbito del tablero")
        return self


class IndicadoresAgencia(ValoresIndicadores):
    agencia_id: UUID
    nombre: str
    disponible: bool


class TableroDemanda(Esquema):
    fuente: FuenteDemanda
    actualizado_en: datetime | None
    desde: date
    hasta: date
    resumen: ValoresIndicadores
    agencias: list[IndicadoresAgencia]
    calendario: CalendarioDemanda


class CoberturaDemo(Esquema):
    desde: FechaCivil
    hasta: FechaCivil

    @model_validator(mode="after")
    def validar_rango(self) -> "CoberturaDemo":
        if self.desde > self.hasta:
            raise ValueError("La fecha inicial debe ser anterior o igual a la final")
        return self


class ConsultaFiltros(CoberturaDemo):
    agencia_ids: Annotated[list[UUID], Field(min_length=1)]
    servicio_id: UUID | None = None


class ActividadFiltro(Esquema):
    agencia_id: UUID
    servicio_id: UUID
    colaborador_id: UUID | None


class HistorialFiltros(Esquema):
    opciones: list[ActividadFiltro]
