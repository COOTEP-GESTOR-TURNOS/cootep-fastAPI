"""Cobertura temporal, pronóstico y contrato interno de demanda."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.config import configuracion
from app.dependencias import repositorio_demanda, servicio_demanda
from app.main import app
from app.repositories.demanda_repository import DemandaRepository, _fecha_bogota, _medianoche_utc
from app.schemas_demanda import EventoAgencia
from app.services.calendario_demanda import marcas_calendario
from app.services.demanda_service import DemandaService
from app.services.modelo_demanda import nivel_demanda

AGENCIA = UUID("a0000000-0000-4000-8000-000000000001")
EVENTO = UUID("b0000000-0000-4000-8000-000000000001")
API = f"/api/v1/analitica/agencias/{AGENCIA}"


class RepositorioFalso:
    def __init__(self, historial, eventos=None, origen="REAL"):
        self.datos = historial
        self.eventos_por_fecha = eventos or {}
        self.origen = origen
        self.rangos = []

    async def fuente(self, agencia_id):
        return self.origen

    async def primer_dia(self, agencia_id, hasta, servicio_id=None):
        return min((fecha for fecha in self.datos if fecha < hasta), default=None)

    async def historial(self, agencia_id, desde, hasta, servicio_id=None):
        self.rangos.append((desde, hasta))
        return {fecha: total for fecha, total in self.datos.items() if desde <= fecha < hasta}

    async def eventos(self, agencia_id, desde, hasta):
        return {fecha: eventos for fecha, eventos in self.eventos_por_fecha.items() if desde <= fecha <= hasta}


def test_mes_pasado_exhibe_historial_anterior_a_la_ventana_de_entrenamiento():
    fecha_evento = date(2024, 2, 5)
    evento = EventoAgencia(id=EVENTO, fecha=fecha_evento, tipo="PAGO_NOMINA")
    repo = RepositorioFalso({fecha_evento: 12}, {fecha_evento: [evento]})
    servicio = DemandaService(repo)
    resultado = asyncio.run(servicio.calendario(AGENCIA, "2024-02", None, None, hoy=date(2026, 1, 15)))

    assert resultado.estado_pronostico == "SOLO_CALENDARIO"
    assert len(resultado.dias) == 29
    assert resultado.dias[0].turnos_registrados is None
    assert resultado.dias[4].turnos_registrados == 12
    assert resultado.dias[4].eventos == [evento]
    assert resultado.dias[4].vencimiento_credito is True
    assert resultado.dias[5].turnos_registrados == 0
    assert all(dia.turnos_estimados is None for dia in resultado.dias)
    assert repo.rangos == [(date(2024, 2, 1), date(2024, 3, 1))]


def test_pronostica_desde_hoy_y_termina_el_dia_90():
    hoy = date(2026, 1, 2)
    datos = {hoy - timedelta(days=dia): 30 + dia % 7 for dia in range(1, 80)}
    repo = RepositorioFalso({**datos, hoy: 1_000})
    servicio = DemandaService(repo)
    actual = asyncio.run(servicio.calendario(AGENCIA, "2026-01", None, None, hoy=hoy))
    final = asyncio.run(servicio.calendario(AGENCIA, "2026-04", None, None, hoy=hoy))

    assert actual.fuente == "REAL"
    assert actual.estado_pronostico == "DISPONIBLE"
    assert actual.dias[0].turnos_registrados == datos[date(2026, 1, 1)]
    assert actual.dias[1].turnos_registrados == 1_000
    assert 20 <= actual.dias[1].turnos_estimados <= 50
    assert actual.dias[1].nivel_demanda in ("ALTA", "MEDIA", "BAJA")
    assert not actual.dias[2].dia_habil and actual.dias[2].turnos_estimados is None
    assert final.dias[0].turnos_estimados is not None
    assert final.dias[5].dia_habil and final.dias[5].turnos_estimados is None
    assert final.dias[5].nivel_demanda is None
    assert repo.rangos == [(hoy - timedelta(days=365), hoy + timedelta(days=1))] * 2


def test_dias_no_habiles_preservan_registros_y_omiten_pronosticos():
    hoy = date(2026, 10, 1)
    cierre_local = date(2026, 10, 5)
    datos = {hoy - timedelta(days=dia): 25 + dia % 7 for dia in range(1, 85)}
    datos[date(2026, 9, 27)] = 14  # domingo histórico
    evento = EventoAgencia(id=EVENTO, fecha=cierre_local, tipo="FESTIVO_LOCAL")
    servicio = DemandaService(RepositorioFalso(datos, {cierre_local: [evento]}))

    septiembre = asyncio.run(servicio.calendario(AGENCIA, "2026-09", "PUTUMAYO", "Mocoa", hoy=hoy))
    octubre = asyncio.run(servicio.calendario(AGENCIA, "2026-10", "PUTUMAYO", "Mocoa", hoy=hoy))
    inactivo = asyncio.run(servicio.calendario(AGENCIA, "2026-10", "PUTUMAYO", "Mocoa", hoy=hoy,
                                               servicio_id=EVENTO, servicio_activo=False))

    domingo = septiembre.dias[26]
    assert not domingo.dia_habil and domingo.turnos_registrados == 14
    assert domingo.turnos_estimados is domingo.nivel_demanda is None
    for cierre in (date(2026, 10, 2), date(2026, 10, 3), cierre_local, date(2026, 10, 12)):
        dia = octubre.dias[cierre.day - 1]
        assert not dia.dia_habil and dia.turnos_estimados is None and dia.nivel_demanda is None
    assert octubre.dias[5].dia_habil and octubre.dias[5].turnos_estimados is not None
    assert inactivo.estado_pronostico == "SOLO_CALENDARIO"
    assert all(not dia.dia_habil and dia.turnos_estimados is None for dia in inactivo.dias)


def test_historial_insuficiente_preserva_conteos_y_marcas():
    hoy = date(2026, 7, 10)
    repo = RepositorioFalso({date(2026, 7, 8): 8})
    servicio = DemandaService(repo)
    resultado = asyncio.run(servicio.calendario(AGENCIA, "2026-07", "PUTUMAYO", "Mocoa", hoy=hoy))

    assert resultado.estado_pronostico == "HISTORIAL_INSUFICIENTE"
    assert resultado.dias[7].turnos_registrados == 8
    assert resultado.dias[8].turnos_registrados == 0
    assert resultado.dias[9].vencimiento_credito is True
    assert resultado.dias[12].festivo_nacional is not None
    assert all(dia.turnos_estimados is None for dia in resultado.dias)


def test_vencimientos_fines_de_semana_y_festivos_nacionales():
    marcas = marcas_calendario(date(2026, 7, 5))
    assert marcas.fin_de_semana and marcas.vencimiento_credito
    assert marcas.cercania_5 == 3 and marcas.cercania_10 == 0
    assert marcas_calendario(date(2026, 4, 2)).festivo_nacional == "Jueves Santo"
    assert marcas_calendario(date(2026, 4, 3)).festivo_nacional == "Viernes Santo"
    assert "Chiquinquirá" in marcas_calendario(date(2026, 7, 13)).festivo_nacional
    assert "Chiquinquirá" in marcas_calendario(date(2027, 7, 12)).festivo_nacional
    assert marcas_calendario(date(2026, 7, 9)).festivo_nacional is None


def test_niveles_distinguen_cero_y_tratan_en_medio_los_empates():
    assert nivel_demanda(0, (0, 0)) == "BAJA"
    assert nivel_demanda(19, (20, 50)) == "BAJA"
    assert nivel_demanda(20, (20, 50)) == "MEDIA"
    assert nivel_demanda(50, (20, 50)) == "MEDIA"
    assert nivel_demanda(51, (20, 50)) == "ALTA"


def test_agregacion_cuenta_solicitudes_distintas_y_usa_medianoche_bogota():
    class Coleccion:
        def aggregate(self, etapas, **opciones):
            filtro = etapas[0]["$match"]
            assert filtro["agenciaId"] == str(AGENCIA)
            assert filtro["estadoNuevo"] == "SOLICITADO"
            assert filtro["servicioId"] == str(EVENTO)
            assert filtro["ocurridoEn"] == {
                "$gte": datetime(2026, 7, 8, 5, tzinfo=timezone.utc),
                "$lt": datetime(2026, 7, 9, 5, tzinfo=timezone.utc),
            }
            assert etapas[1] == {"$group": {"_id": "$turnoId", "instante": {"$min": "$ocurridoEn"}}}
            assert etapas[2]["$group"]["_id"]["$dateToString"]["timezone"] == "America/Bogota"

            async def registros():
                yield {"_id": "2026-07-08", "turnos": 1}

            return registros()

    repo = DemandaRepository({"eventos_turno": Coleccion(), "eventos_agencia": None})
    conteos = asyncio.run(repo.historial(AGENCIA, date(2026, 7, 8), date(2026, 7, 9), EVENTO))
    assert conteos == {date(2026, 7, 8): 1}
    assert _fecha_bogota(datetime(2026, 7, 9, 4, 59)) == date(2026, 7, 8)
    assert _fecha_bogota(_medianoche_utc(date(2026, 7, 9))) == date(2026, 7, 9)


def test_la_fuente_es_simulada_si_la_agencia_tiene_datos_de_demostracion():
    hoy = date(2026, 10, 1)
    datos = {hoy - timedelta(days=dia): 25 for dia in range(1, 85)}
    simulado = asyncio.run(DemandaService(RepositorioFalso(datos, origen="SIMULADA")).calendario(
        AGENCIA, "2026-10", "PUTUMAYO", "Mocoa", hoy=hoy))
    real = asyncio.run(DemandaService(RepositorioFalso(datos)).calendario(AGENCIA, "2026-10", "PUTUMAYO", "Mocoa", hoy=hoy))
    assert (simulado.fuente, real.fuente) == ("SIMULADA", "REAL")
    assert simulado.dias == real.dias  # los simulados se leen igual que los reales


def test_el_repositorio_reconoce_la_marca_de_origen():
    class Coleccion:
        def __init__(self, documento):
            self.documento = documento
            self.filtros = []

        async def find_one(self, filtro, proyeccion=None):
            self.filtros.append(filtro)
            return self.documento

    con_demo = Coleccion({"_id": 1})
    assert asyncio.run(DemandaRepository({"eventos_turno": con_demo, "eventos_agencia": None}).fuente(AGENCIA)) == "SIMULADA"
    assert con_demo.filtros == [{"origen": "SIMULADO", "agenciaId": str(AGENCIA)}]
    sin_demo = DemandaRepository({"eventos_turno": Coleccion(None), "eventos_agencia": None})
    assert asyncio.run(sin_demo.fuente(AGENCIA)) == "REAL"


def test_api_demanda_valida_token_formatos_y_errores_de_eventos(monkeypatch):
    class RepositorioEventosFalso(RepositorioFalso):
        async def crear_evento(self, agencia_id, solicitud):
            if solicitud.tipo == "PAGO_NOMINA":
                raise DuplicateKeyError("evento existente")
            return EventoAgencia(id=EVENTO, fecha=solicitud.fecha, tipo=solicitud.tipo)

        async def editar_evento(self, agencia_id, evento_id, cambios):
            return None

        async def eliminar_evento(self, agencia_id, evento_id):
            return evento_id == EVENTO

    monkeypatch.setenv("SERVICE_TOKEN", "token-demanda")
    configuracion.cache_clear()
    repo = RepositorioEventosFalso({})
    app.dependency_overrides[repositorio_demanda] = lambda: repo
    app.dependency_overrides[servicio_demanda] = lambda: DemandaService(repo)
    cliente = TestClient(app)
    headers = {"X-Service-Token": "token-demanda"}
    evento = {"fecha": "2026-07-13", "tipo": "FESTIVO_LOCAL"}
    try:
        assert cliente.get(f"{API}/demanda?mes=2026-07").status_code == 401
        assert cliente.get(f"{API}/demanda?mes=2026-13", headers=headers).status_code == 400
        consulta = cliente.get(f"{API}/demanda?mes=2026-07", headers=headers)
        assert consulta.status_code == 200
        assert consulta.json()["agenciaId"] == str(AGENCIA)
        assert consulta.json()["dias"][12]["festivoNacional"]
        assert consulta.json()["dias"][12]["festivosPutumayo"] == []
        creado = cliente.post(f"{API}/eventos", json=evento, headers=headers)
        assert creado.status_code == 201
        assert creado.json() == {"id": str(EVENTO), **evento}
        assert cliente.post(f"{API}/eventos", json={**evento, "tipo": "PAGO_NOMINA"}, headers=headers).status_code == 409
        assert cliente.post(f"{API}/eventos", json={**evento, "fecha": "2026-07-13T00:00:00"}, headers=headers).status_code == 400
        assert cliente.patch(f"{API}/eventos/{EVENTO}", json={}, headers=headers).status_code == 400
        assert cliente.patch(f"{API}/eventos/{EVENTO}", json={"fecha": None}, headers=headers).status_code == 400
        assert cliente.patch(f"{API}/eventos/{EVENTO}", json={"tipo": "PAGO_NOMINA"}, headers=headers).status_code == 404
        assert cliente.delete(f"{API}/eventos/{EVENTO}", headers=headers).status_code == 204
    finally:
        app.dependency_overrides.clear()
        configuracion.cache_clear()
