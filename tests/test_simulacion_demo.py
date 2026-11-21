from datetime import date, datetime
from uuid import NAMESPACE_OID, uuid5

import pytest

from app.config import ZONA_AGENCIAS
from app.services.simulacion_demo import AgenciaDemo, CatalogoDemo, selecciones_pendientes, simular_jornada


def _id(nombre: str) -> str:
    return str(uuid5(NAMESPACE_OID, nombre))


@pytest.fixture
def agencia() -> AgenciaDemo:
    return AgenciaDemo.model_validate({
        "id": _id("agencia"), "codigo": "AG01", "nombre": "Agencia Mocoa", "municipio": "Mocoa",
        "departamento": "PUTUMAYO", "activa": True,
        "servicios": [{
            "id": _id("servicio"), "codigo": "CAJA", "prefijo": "C", "ventanillas": 2,
            "activa": True, "apertura": "08:00", "cierre": "16:00",
        }],
        "funcionarios": [{"id": _id("funcionario"), "servicioId": None}],
        "asociados": [_id(f"asociado-{i}") for i in range(1_000)],
    })


@pytest.mark.parametrize(("fecha", "tipos"), [
    (date(2026, 11, 21), ()),
    (date(2026, 11, 16), ()),
    (date(2026, 10, 2), ()),
    (date(2026, 11, 19), ("FESTIVO_LOCAL",)),
])
def test_simulacion_cierra_en_festivos_y_fines_de_semana(agencia, fecha, tipos):
    assert list(simular_jornada(agencia, fecha, 7, tipos)) == []


def test_turnos_coherentes_con_los_eventos_y_estables_al_repetir(agencia):
    fecha = date(2026, 11, 19)
    filas = list(simular_jornada(agencia, fecha, 7, ()))
    assert filas and filas == list(simular_jornada(agencia, fecha, 7, ()))
    assert len({fila["turno"]["id"] for fila in filas}) == len(filas)
    for fila in filas:
        turno, eventos = fila["turno"], fila["eventos"]
        assert turno["asociadoId"] in agencia.asociados
        assert eventos[0]["estadoNuevo"] == "SOLICITADO"
        assert eventos[-1]["estadoNuevo"] == turno["estado"]
        assert eventos[-1]["ocurridoEn"] == turno["finEn"]
        assert all(evento["turnoId"] == turno["id"] for evento in eventos)
        instantes = [evento["ocurridoEn"] for evento in eventos]
        assert instantes == sorted(instantes)
        assert datetime.fromisoformat(turno["finEn"]).astimezone(ZONA_AGENCIAS).hour <= 16


def test_pendientes_exigen_horario_real_y_dia_habil(agencia):
    catalogo = CatalogoDemo(semilla=7, agencias=[agencia])
    ahora = datetime(2026, 11, 20, 10, tzinfo=ZONA_AGENCIAS)
    elegibles = selecciones_pendientes(catalogo, ahora, {})
    assert elegibles["fecha"] == "2026-11-20"
    assert len(elegibles["agencias"][0]["asociados"]) >= 3
    assert seleccion_vacia(catalogo, ahora.replace(hour=16))
    assert seleccion_vacia(catalogo, ahora.replace(day=21))
    assert not selecciones_pendientes(catalogo, ahora, {(agencia.id, ahora.date()): {"FESTIVO_LOCAL"}})["agencias"]


def seleccion_vacia(catalogo: CatalogoDemo, ahora: datetime) -> bool:
    return selecciones_pendientes(catalogo, ahora, {})["agencias"] == []
