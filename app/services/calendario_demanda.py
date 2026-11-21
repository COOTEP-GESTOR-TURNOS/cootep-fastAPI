"""Marcas del calendario y vencimientos para el pronóstico de demanda."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from holidays import HolidayBase, country_holidays

from app.services.calendario_putumayo import festivos_putumayo


@dataclass(frozen=True)
class MarcasCalendario:
    fin_de_semana: bool
    festivo_nacional: str | None
    festivos_putumayo: tuple[str, ...]
    vencimiento_credito: bool
    cercania_5: int
    cercania_10: int


@lru_cache(maxsize=16)
def _festivos_colombia(anio: int) -> HolidayBase:
    return country_holidays("CO", years=anio, language="es", expand=False)


def marcas_calendario(
    fecha: date, departamento: str | None = None, municipio: str | None = None,
) -> MarcasCalendario:
    return MarcasCalendario(
        fin_de_semana=fecha.weekday() >= 5,
        festivo_nacional=_festivos_colombia(fecha.year).get(fecha),
        festivos_putumayo=festivos_putumayo(fecha, departamento, municipio),
        vencimiento_credito=fecha.day in (5, 10),
        cercania_5=max(0, 3 - abs(fecha.day - 5)),
        cercania_10=max(0, 3 - abs(fecha.day - 10)),
    )


def cierre_calendario(marcas: MarcasCalendario, tipos: Collection[str]) -> bool:
    return bool(marcas.fin_de_semana or marcas.festivo_nacional or marcas.festivos_putumayo or "FESTIVO_LOCAL" in tipos)
