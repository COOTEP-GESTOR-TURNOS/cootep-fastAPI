"""Construcción de hechos a partir de eventos (la tarea que corre en procesos paralelos)."""

from datetime import datetime, timezone

import pytest

from app.services.hechos import construir_hechos

A, S = "a0000000-0000-4000-8000-000000000001", "b0000000-0000-4000-8000-000000000001"
COLABORADOR = "c0000000-0000-4000-8000-000000000001"
OTRO_COLABORADOR = "c0000000-0000-4000-8000-000000000002"
ASOCIADO = "d0000000-0000-4000-8000-000000000001"


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


@pytest.mark.parametrize(("estado", "tipo", "actor", "esperado"), [
    ("ATENDIDO", "TurnoAtendido", OTRO_COLABORADOR, OTRO_COLABORADOR),
    ("AUSENTE", "TurnoAusente", OTRO_COLABORADOR, OTRO_COLABORADOR),
    ("AUSENTE", "TurnoAusente", None, COLABORADOR),
    ("CANCELADO", "TurnoCancelado", ASOCIADO, COLABORADOR),
])
def test_responsable_de_un_turno_finalizado(estado, tipo, actor, esperado):
    eventos = [
        ("t1", "SOLICITADO", t(14, 0), "GENERAL", "WEB", "TurnoSolicitado", ASOCIADO),
        ("t1", "LLAMADO", t(14, 12), "GENERAL", "WEB", "TurnoLlamado", COLABORADOR),
        ("t1", "LLAMADO", t(14, 13), "GENERAL", "WEB", "TurnoConfirmado", ASOCIADO),
        ("t1", estado, t(14, 20), "GENERAL", "WEB", tipo, actor),
    ]
    [hecho] = construir_hechos(((A, S), eventos))
    assert hecho["colaborador_id"] == esperado
    assert hecho["espera_seg"] == 12 * 60


def test_pendiente_devuelto_a_la_fila_libera_responsable():
    eventos = [
        ("t1", "SOLICITADO", t(14, 0), "PREFERENCIAL", "WEB", "TurnoSolicitado", ASOCIADO),
        ("t1", "LLAMADO", t(14, 12), "PREFERENCIAL", "WEB", "TurnoLlamado", COLABORADOR),
    ]
    [llamado] = construir_hechos(((A, S), eventos), incluir_pendientes=True)
    assert llamado["colaborador_id"] == COLABORADOR

    eventos.append(("t1", "EN_ESPERA", t(14, 13), "GENERAL", "WEB", "TurnoEnEspera", COLABORADOR))
    [en_espera] = construir_hechos(((A, S), eventos), incluir_pendientes=True)
    assert en_espera["colaborador_id"] is None
    assert en_espera["estado_final"] == "PENDIENTE"

    eventos.append(("t1", "LLAMADO", t(14, 14), "GENERAL", "WEB", "TurnoLlamado", OTRO_COLABORADOR))
    [reasignado] = construir_hechos(((A, S), eventos), incluir_pendientes=True)
    assert reasignado["colaborador_id"] == OTRO_COLABORADOR


def test_cancelado_sin_llamada_se_mantiene_sin_asignacion():
    eventos = [
        ("t1", "SOLICITADO", t(14, 0), "GENERAL", "WEB", "TurnoSolicitado", ASOCIADO),
        ("t1", "CANCELADO", t(14, 20), "GENERAL", "WEB", "TurnoCancelado", ASOCIADO),
    ]
    [hecho] = construir_hechos(((A, S), eventos))
    assert hecho["colaborador_id"] is None
