"""Transformación de eventos en hechos analíticos. Es trabajo de CPU puro y sin estado:
se ejecuta en procesos paralelos (ProcessPoolExecutor), una fila de agencia y servicio por tarea.

Cada evento llega como tupla compacta con tipo y actor para atribuir la atención.
"""

from collections import defaultdict
from datetime import datetime
from operator import itemgetter
from uuid import UUID

from app.config import ZONA_AGENCIAS
from app.services.calendario import franja_de, tipo_dia

FINALES = ("ATENDIDO", "CANCELADO", "AUSENTE")
# Atención que nadie finalizó y cerró el backend: cuenta como atendida, pero su duración no es real.
CIERRE_POR_SISTEMA = "TurnoCerradoPorSistema"
TIPOS_CON_RESPONSABLE = {"TurnoLlamado", "TurnoEnAtencion", "TurnoAtendido", "TurnoAusente"}

EventoCompacto = tuple[str, str, float, str, str] | tuple[str, str, float, str, str, str | None, str | None]
Lote = tuple[tuple[str, str], list[EventoCompacto]]


def _actor_valido(actor: str | None) -> str | None:
    if isinstance(actor, str):
        try:
            return str(UUID(actor))
        except ValueError:
            pass
    return None


def construir_hechos(lote: Lote, incluir_pendientes: bool = False) -> list[dict]:
    """Agrupa los eventos por turno y calcula tiempos, resultado y responsable.

    El ETL operativo omite los turnos abiertos y la proyección demo también los cuenta.
    """
    (agencia_id, servicio_id), eventos = lote
    por_turno: dict[str, list[EventoCompacto]] = defaultdict(list)
    for evento in eventos:
        por_turno[evento[0]].append(evento)

    hechos: list[dict] = []
    for turno_id, eventos_turno in por_turno.items():
        eventos_turno.sort(key=itemgetter(2))
        momentos = {}
        final = None
        colaborador = None
        cerrado_por_sistema = False
        for evento in eventos_turno:
            estado, instante = evento[1:3]
            tipo, actor = evento[5:] if len(evento) == 7 else (None, None)
            if tipo == "TurnoConfirmado":
                continue
            if tipo == CIERRE_POR_SISTEMA:
                cerrado_por_sistema = True
            momentos[estado] = instante
            if estado == "EN_ESPERA":
                colaborador = None
            elif tipo in TIPOS_CON_RESPONSABLE:
                colaborador = _actor_valido(actor) or colaborador
            if estado in FINALES:
                final = estado
        if "SOLICITADO" not in momentos or (final is None and not incluir_pendientes):
            continue

        solicitado = momentos["SOLICITADO"]
        llamado = momentos.get("LLAMADO")
        inicio = momentos.get("EN_ATENCION")
        fin = momentos[final] if final else solicitado
        espera = (llamado if llamado is not None else fin) - solicitado
        atencion_medida = inicio is not None and final == "ATENDIDO" and not cerrado_por_sistema
        atencion = fin - inicio if atencion_medida else 0.0  # 0 = sin medir: los promedios lo omiten
        local = datetime.fromtimestamp(solicitado, ZONA_AGENCIAS)
        ultimo = eventos_turno[-1]

        hechos.append(
            {
                "turno_id": turno_id,
                "agencia_id": agencia_id,
                "servicio_id": servicio_id,
                "colaborador_id": colaborador,
                "fecha": local.date().isoformat(),
                "franja": franja_de(local),
                "dia_semana": local.isoweekday(),
                "tipo_dia": tipo_dia(local.date()),
                "tipo_atencion": ultimo[3],
                "canal": eventos_turno[0][4],
                "estado_final": final or "PENDIENTE",
                "espera_seg": max(0, int(espera)),
                "atencion_seg": max(0, int(atencion)),
                "solicitado_en": local.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return hechos
