from datetime import date

import pytest

from app.services.calendario_putumayo import festivos_putumayo


@pytest.mark.parametrize("fecha", [date(2026, 1, 5), date(2026, 1, 6), date(2026, 4, 17)])
def test_dia_civico_departamental(fecha):
    assert festivos_putumayo(fecha, "PUTUMAYO", "Mocoa")
    assert festivos_putumayo(fecha, "PUTUMAYO", "Puerto Asís")
    assert festivos_putumayo(fecha, "Nariño", "Pasto") == ()
    assert festivos_putumayo(fecha, None, None) == ()


@pytest.mark.parametrize("fecha", [date(2025, 10, 3), date(2026, 10, 2)])
def test_dia_civico_de_mocoa(fecha):
    assert festivos_putumayo(fecha, "Putumayo", " Mocoa ") == ("Día cívico por el aniversario de Mocoa",)
    assert festivos_putumayo(fecha, "PUTUMAYO", "Orito") == ()
    assert festivos_putumayo(fecha, "PUTUMAYO", None) == ()


def test_las_declaratorias_son_para_fechas_especificas():
    assert festivos_putumayo(date(2027, 1, 5), "PUTUMAYO", "Mocoa") == ()
    assert festivos_putumayo(date(2026, 10, 3), "PUTUMAYO", "Mocoa") == ()
