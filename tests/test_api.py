"""PR-12: la API responde salud sin token y exige el token de servicio en lo demás."""

from fastapi.testclient import TestClient

from app.config import configuracion
from app.dependencias import servicio_recomendacion
from app.main import app
from app.schemas import Recomendacion

cliente = TestClient(app)  # sin "with": no abre conexiones reales a los motores


def test_salud_responde_sin_token():
    respuesta = cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


def test_sin_token_de_servicio_responde_401_en_formato_rfc_9457():
    respuesta = cliente.get("/api/v1/recomendaciones", params={"agenciaId": "x", "servicioId": "y", "fecha": "2026-11-20"})
    assert respuesta.status_code == 401
    assert respuesta.headers["content-type"].startswith("application/problem+json")


def test_con_token_usa_el_servicio_y_devuelve_camel_case(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "token-de-prueba")
    configuracion.cache_clear()

    class ServicioFalso:
        async def recomendar(self, agencia_id, servicio_id, fecha):
            return Recomendacion(agencia_id=agencia_id, servicio_id=servicio_id, fecha=fecha, tipo_dia="NORMAL", franjas=[])

    app.dependency_overrides[servicio_recomendacion] = lambda: ServicioFalso()
    try:
        respuesta = cliente.get(
            "/api/v1/recomendaciones",
            params={"agenciaId": "a0000000-0000-4000-8000-000000000001",
                    "servicioId": "b0000000-0000-4000-8000-000000000001", "fecha": "2026-11-20"},
            headers={"X-Service-Token": "token-de-prueba"},
        )
    finally:
        app.dependency_overrides.clear()
        configuracion.cache_clear()

    assert respuesta.status_code == 200
    assert respuesta.json()["tipoDia"] == "NORMAL"
