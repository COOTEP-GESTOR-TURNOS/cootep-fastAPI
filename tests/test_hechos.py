"""Construcción de hechos a partir de eventos (la tarea que corre en procesos paralelos)."""

from datetime import datetime, timezone

from app.services.hechos import construir_hechos

A, S = "a0000000-0000-4000-8000-000000000001", "b0000000-0000-4000-8000-000000000001"


def t(hora: int, minuto: int) -> float:
    # 14:00 UTC = 09:00 en Colombia
    return datetime(2026, 11, 20, hora, minuto, tzinfo=timezone.utc).timestamp()


def test_turno_atendido_calcula_espera_y_atencion():
    eventos = [
        ("t1", "SOLICITADO", t(14, 0), "GENERAL", "MOVIL"),
        ("t1", "EN_ESPERA", t(14, 0), "GENERAL", "MOVIL"),
        ("t1", "LLAMADO", t(14, 12), "GENERAL", "MOVIL"),
        ("t1", "EN_ATENCION", t(14, 13), "GENERAL", "MOVIL"),
        ("t1", "ATENDIDO", t(14, 20), "GENERAL", "MOVIL"),
    ]
    [hecho] = construir_hechos(((A, S), eventos))
    assert hecho["espera_seg"] == 12 * 60
    assert hecho["atencion_seg"] == 7 * 60
    assert hecho["franja"] == "09:00"
    assert hecho["canal"] == "MOVIL"
    assert hecho["estado_final"] == "ATENDIDO"


def test_turno_cancelado_no_tiene_atencion():
    eventos = [("t2", "SOLICITADO", t(15, 40), "PREFERENCIAL", "WEB"), ("t2", "CANCELADO", t(15, 50), "PREFERENCIAL", "WEB")]
    [hecho] = construir_hechos(((A, S), eventos))
    assert hecho["estado_final"] == "CANCELADO"
    assert hecho["espera_seg"] == 600
    assert hecho["atencion_seg"] == 0
    assert hecho["franja"] == "10:30"


def test_turno_sin_finalizar_se_ignora():
    eventos = [("t3", "SOLICITADO", t(16, 0), "GENERAL", "WEB"), ("t3", "EN_ESPERA", t(16, 0), "GENERAL", "WEB")]
    assert construir_hechos(((A, S), eventos)) == []
