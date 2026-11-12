"""Transformación de eventos en hechos analíticos. Es trabajo de CPU puro y sin estado:
se ejecuta en procesos paralelos (ProcessPoolExecutor), una fila de agencia y servicio por tarea.

Cada evento llega como tupla compacta (turnoId, estadoNuevo, instante_epoch, tipoAtencion, canal)
para que el envío a otros procesos (pickle) cueste poco.
"""

from collections import defaultdict
from datetime import datetime
from operator import itemgetter

from app.config import ZONA_AGENCIAS
from app.services.calendario import franja_de, tipo_dia

FINALES = ("ATENDIDO", "CANCELADO", "AUSENTE")

EventoCompacto = tuple[str, str, float, str, str]
Lote = tuple[tuple[str, str], list[EventoCompacto]]


def construir_hechos(lote: Lote) -> list[dict]:
    """Agrupa los eventos por turno y calcula espera y atención de cada turno finalizado.

    Los turnos sin estado final se ignoran: se cargarán cuando terminen (la carga es idempotente).
    """
    (agencia_id, servicio_id), eventos = lote
    por_turno: dict[str, list[EventoCompacto]] = defaultdict(list)
    for evento in eventos:
        por_turno[evento[0]].append(evento)

    hechos: list[dict] = []
    for turno_id, eventos_turno in por_turno.items():
        eventos_turno.sort(key=itemgetter(2))
        momentos = {estado: instante for _, estado, instante, _, _ in eventos_turno}
        final = next((estado for estado in FINALES if estado in momentos), None)
        if final is None or "SOLICITADO" not in momentos:
            continue

        solicitado = momentos["SOLICITADO"]
        llamado = momentos.get("LLAMADO")
        inicio = momentos.get("EN_ATENCION")
        fin = momentos[final]
        espera = (llamado if llamado is not None else fin) - solicitado
        atencion = fin - inicio if (inicio is not None and final == "ATENDIDO") else 0.0
        local = datetime.fromtimestamp(solicitado, ZONA_AGENCIAS)
        ultimo = eventos_turno[-1]

        hechos.append(
            {
                "turno_id": turno_id,
                "agencia_id": agencia_id,
                "servicio_id": servicio_id,
                "fecha": local.date().isoformat(),
                "franja": franja_de(local),
                "dia_semana": local.isoweekday(),
                "tipo_dia": tipo_dia(local.date()),
                "tipo_atencion": ultimo[3],
                "canal": eventos_turno[0][4],
                "estado_final": final,
                "espera_seg": max(0, int(espera)),
                "atencion_seg": max(0, int(atencion)),
                "solicitado_en": local.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return hechos
