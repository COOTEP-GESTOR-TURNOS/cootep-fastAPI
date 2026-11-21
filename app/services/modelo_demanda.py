"""Regresión de Poisson para el número de turnos que pide una agencia en un día hábil.

Solo se entrena y se pronostica con días hábiles: los fines de semana, los festivos y los cierres
no atienden, así que no enseñan nada. Las variables son las que mueven la demanda y las mismas
que propone el generador de datos de prueba: el día de la semana, la cercanía a los vencimientos
de crédito del 5 y el 10, y los pagos de subsidio y de nómina registrados como eventos de la
agencia. El festivo local no es variable porque cierra el día.

No hay una variable por día del mes: con pocos meses de historia cada día del mes aparece una o
dos veces y el modelo aprendería ruido (se midió con `evaluar`).
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from statistics import mean, quantiles

from sklearn.linear_model import PoissonRegressor

from app.schemas_demanda import NivelDemanda, TipoEvento
from app.services.calendario_demanda import marcas_calendario

# El lunes queda en el intercepto; así las variables del día no son redundantes entre sí.
VARIABLES = ("martes", "miercoles", "jueves", "viernes", "cercania_5", "cercania_10", "pago_subsidio", "pago_nomina")
DIAS_PRUEBA = 20


def _variables(fecha: date, eventos: Collection[TipoEvento]) -> list[int]:
    marcas = marcas_calendario(fecha)
    return [
        *(int(fecha.weekday() == dia) for dia in range(1, 5)),
        marcas.cercania_5,
        marcas.cercania_10,
        int("PAGO_SUBSIDIO_FAMILIAR" in eventos),
        int("PAGO_NOMINA" in eventos),
    ]


def entrenar(historial: Mapping[date, int], eventos: Mapping[date, Collection[TipoEvento]]) -> PoissonRegressor:
    """`historial` trae solo días hábiles: turnos pedidos por fecha."""
    fechas = sorted(historial)
    variables = [_variables(fecha, eventos.get(fecha, ())) for fecha in fechas]
    modelo = PoissonRegressor(alpha=1e-3, solver="newton-cholesky", max_iter=300)
    return modelo.fit(variables, [historial[fecha] for fecha in fechas])


def pronosticar(
    modelo: PoissonRegressor, fechas: Iterable[date], eventos: Mapping[date, Collection[TipoEvento]],
) -> dict[date, int]:
    fechas = list(fechas)
    if not fechas:
        return {}
    variables = [_variables(fecha, eventos.get(fecha, ())) for fecha in fechas]
    return {fecha: round(float(valor)) for fecha, valor in zip(fechas, modelo.predict(variables), strict=True)}


@dataclass(frozen=True)
class Evaluacion:
    """Error absoluto medio, en turnos por día, sobre los últimos días hábiles del historial."""

    dias_prueba: int
    error_modelo: float
    error_referencia: float

    @property
    def mejora(self) -> float:
        """Fracción del error de la referencia que el modelo evita (0,5 = la mitad)."""
        return 1 - self.error_modelo / self.error_referencia if self.error_referencia else 0.0


def evaluar(
    historial: Mapping[date, int], eventos: Mapping[date, Collection[TipoEvento]], dias_prueba: int = DIAS_PRUEBA,
) -> Evaluacion | None:
    """Entrena sin los últimos `dias_prueba` días hábiles y los pronostica.

    La referencia es lo que haría alguien sin modelo: el promedio de ese día de la semana.
    """
    fechas = sorted(historial)
    if len(fechas) < 2 * dias_prueba:
        return None
    entrenamiento = {fecha: historial[fecha] for fecha in fechas[:-dias_prueba]}
    prueba = fechas[-dias_prueba:]
    pronostico = pronosticar(entrenar(entrenamiento, eventos), prueba, eventos)
    general = mean(entrenamiento.values())
    por_dia = {}
    for dia in range(5):
        conteos = [total for fecha, total in entrenamiento.items() if fecha.weekday() == dia]
        por_dia[dia] = mean(conteos) if conteos else general
    return Evaluacion(
        dias_prueba=dias_prueba,
        error_modelo=round(mean(abs(pronostico[fecha] - historial[fecha]) for fecha in prueba), 1),
        error_referencia=round(mean(abs(por_dia[fecha.weekday()] - historial[fecha]) for fecha in prueba), 1),
    )


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
