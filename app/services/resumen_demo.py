"""Estadísticas suficientes para combinar días y agencias sin promediar promedios."""

from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import NamedTuple
from uuid import UUID

from app.schemas import ValoresIndicadores


@dataclass
class TotalesDia:
    atendidos: int = 0
    cancelados: int = 0
    ausentes: int = 0
    pendientes: int = 0
    espera_seg: int = 0
    atencion_seg: int = 0
    preferenciales_finalizados: int = 0

    @property
    def solicitados(self) -> int:
        return self.atendidos + self.cancelados + self.ausentes + self.pendientes

    def sumar(self, otros: "TotalesDia") -> None:
        for campo in fields(self):
            setattr(self, campo.name, getattr(self, campo.name) + getattr(otros, campo.name))

    def valores(self) -> ValoresIndicadores:
        finalizados = self.solicitados - self.pendientes
        return ValoresIndicadores(
            solicitados=self.solicitados,
            atendidos=self.atendidos,
            cancelados=self.cancelados,
            ausentes=self.ausentes,
            pendientes=self.pendientes,
            espera_promedio_min=round(self.espera_seg / (60 * self.atendidos), 1) if self.atendidos else 0,
            atencion_promedio_min=round(self.atencion_seg / (60 * self.atendidos), 1) if self.atendidos else 0,
            proporcion_preferencial=round(self.preferenciales_finalizados / finalizados, 3) if finalizados else 0,
        )


DimensionDia = tuple[str, str, str, str | None]


def agregar_hechos(hechos: list[dict]) -> dict[DimensionDia, TotalesDia]:
    diarios: dict[DimensionDia, TotalesDia] = {}
    for hecho in hechos:
        clave = (hecho["agencia_id"], hecho["fecha"], hecho["servicio_id"], hecho["colaborador_id"])
        total = diarios.setdefault(clave, TotalesDia())
        estado = hecho["estado_final"]
        if estado == "ATENDIDO":
            total.atendidos += 1
            total.espera_seg += hecho["espera_seg"]
            total.atencion_seg += hecho["atencion_seg"]
        elif estado == "CANCELADO":
            total.cancelados += 1
        elif estado == "AUSENTE":
            total.ausentes += 1
        else:
            total.pendientes += 1
        if estado != "PENDIENTE" and hecho["tipo_atencion"] == "PREFERENCIAL":
            total.preferenciales_finalizados += 1
    return diarios


class CorteDemo(NamedTuple):
    generacion: str
    revision: int
    actualizado_en: datetime
    intervalos: tuple[tuple[date, date], ...]
    version: int = 2

    def cubre(self, fecha: date) -> bool:
        return any(desde <= fecha <= hasta for desde, hasta in self.intervalos)


@dataclass
class InstantaneaDemo:
    corte: CorteDemo | None
    diarios: dict[UUID, dict[date, TotalesDia]]

    def cubre(self, fecha: date) -> bool:
        return self.corte is not None and self.corte.cubre(fecha)

    def total(self, agencia_id: UUID, desde: date, hasta: date) -> TotalesDia:
        total = TotalesDia()
        for fecha, valores in self.diarios.get(agencia_id, {}).items():
            if desde <= fecha <= hasta:
                total.sumar(valores)
        return total
