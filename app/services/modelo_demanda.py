"""Regresión de Poisson para conteos diarios de una agencia."""

from collections.abc import Collection, Iterable, Mapping
from datetime import date
from statistics import quantiles

from sklearn.linear_model import PoissonRegressor

from app.schemas_demanda import NivelDemanda, TipoEvento
from app.services.calendario_demanda import marcas_calendario


def _variables(
    fecha: date,
    eventos: Collection[TipoEvento],
    departamento: str | None,
    municipio: str | None,
) -> list[int]:
    marcas = marcas_calendario(fecha, departamento, municipio)
    return [
        *(int(fecha.weekday() == dia) for dia in range(7)),
        *(int(fecha.day == dia) for dia in range(1, 32)),
        marcas.cercania_5,
        marcas.cercania_10,
        int(marcas.fin_de_semana),
        int(bool(marcas.festivo_nacional)),
        int(bool(marcas.festivos_putumayo) or "FESTIVO_LOCAL" in eventos),
        int("PAGO_SUBSIDIO_FAMILIAR" in eventos),
        int("PAGO_NOMINA" in eventos),
    ]


def entrenar(
    historial: Mapping[date, int],
    eventos: Mapping[date, Collection[TipoEvento]],
    departamento: str | None = None,
    municipio: str | None = None,
) -> PoissonRegressor:
    fechas = sorted(historial)
    variables = [_variables(fecha, eventos.get(fecha, ()), departamento, municipio) for fecha in fechas]
    modelo = PoissonRegressor(alpha=0.1, solver="newton-cholesky")
    return modelo.fit(variables, [historial[fecha] for fecha in fechas])


def pronosticar(
    modelo: PoissonRegressor,
    fechas: Iterable[date],
    eventos: Mapping[date, Collection[TipoEvento]],
    departamento: str | None = None,
    municipio: str | None = None,
) -> dict[date, int]:
    fechas = list(fechas)
    if not fechas:
        return {}
    variables = [_variables(fecha, eventos.get(fecha, ()), departamento, municipio) for fecha in fechas]
    return {fecha: round(float(valor)) for fecha, valor in zip(fechas, modelo.predict(variables), strict=True)}


def umbrales_demanda(positivos: Collection[int]) -> tuple[float, float]:
    percentiles = quantiles(positivos, n=100, method="inclusive")
    return percentiles[32], percentiles[66]


def nivel_demanda(total: int, umbrales: tuple[float, float]) -> NivelDemanda:
    bajo, alto = umbrales
    if total == 0 or total < bajo:
        return "BAJA"
    if total > alto:
        return "ALTA"
    return "MEDIA"
