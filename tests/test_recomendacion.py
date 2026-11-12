"""PR-11: la recomendación ordena las franjas por espera y descarta las que tienen pocos datos."""

import asyncio
from datetime import date, datetime
from uuid import UUID

from app.config import ZONA_AGENCIAS, Configuracion
from app.services.calendario import tipo_dia
from app.services.recomendacion_service import RecomendacionService, ordenar_franjas

FILAS = [
    {"franja": "08:00", "espera_mediana_seg": 600.0, "turnos": 80},
    {"franja": "09:30", "espera_mediana_seg": 1800.0, "turnos": 140},
    {"franja": "10:30", "espera_mediana_seg": 240.0, "turnos": 60},
    {"franja": "11:00", "espera_mediana_seg": 120.0, "turnos": 5},  # pocos datos
    {"franja": "14:30", "espera_mediana_seg": 300.0, "turnos": 70},
]


def test_ordena_por_menor_espera_y_descarta_pocos_datos():
    franjas = ordenar_franjas(FILAS, minimo_turnos=20)
    assert [f.franja for f in franjas] == ["10:30", "14:30", "08:00"]
    assert franjas[0].espera_mediana_min == 4.0


def test_si_es_hoy_no_recomienda_franjas_pasadas():
    franjas = ordenar_franjas(FILAS, minimo_turnos=20, desde="11:00")
    assert [f.franja for f in franjas] == ["14:30"]


class RepositorioFalso:
    def __init__(self):
        self.consultas = []

    async def espera_por_franja(self, agencia_id, servicio_id, dia_semana, tipo):
        self.consultas.append((dia_semana, tipo))
        return FILAS


def test_el_servicio_consulta_por_dia_de_semana_y_tipo_de_dia():
    repositorio = RepositorioFalso()
    servicio = RecomendacionService(repositorio, Configuracion(min_muestras_franja=20))
    fecha = date(2026, 11, 16)  # lunes de quincena
    ahora = datetime(2026, 11, 12, 9, 0, tzinfo=ZONA_AGENCIAS)
    agencia = UUID("a0000000-0000-4000-8000-000000000001")
    servicio_id = UUID("b0000000-0000-4000-8000-000000000001")

    resultado = asyncio.run(servicio.recomendar(agencia, servicio_id, fecha, ahora))

    assert repositorio.consultas == [(1, "QUINCENA")]
    assert resultado.tipo_dia == "QUINCENA"
    assert len(resultado.franjas) == 3


def test_tipo_de_dia():
    assert tipo_dia(date(2026, 11, 15)) == "QUINCENA"
    assert tipo_dia(date(2026, 11, 20)) == "NORMAL"
    assert tipo_dia(date(2026, 2, 28)) == "QUINCENA"  # último día del mes
