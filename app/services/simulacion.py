"""Historial simulado para la demostración y la medición de paralelismo (datos ficticios).

Simula, para cada agencia y servicio, llegadas por hora (más en horas pico, quincenas y lunes),
15 % de turnos preferenciales y una fila con ventanillas y la regla K. El resultado son eventos
con la misma forma que guarda el backend principal en MongoDB.
"""

import math
import random
import uuid
from collections import deque
from datetime import date, datetime, time, timedelta, timezone

from app.config import ZONA_AGENCIAS
from app.services.calendario import tipo_dia

AGENCIAS = [f"a0000000-0000-4000-8000-00000000000{i}" for i in range(1, 9)]
# servicio_id: (código, ventanillas, minutos medios de atención, llegadas base por hora)
SERVICIOS = {
    "b0000000-0000-4000-8000-000000000001": ("CAJA", 3, 4.0, 18),
    "b0000000-0000-4000-8000-000000000002": ("CRED", 1, 15.0, 3),
    "b0000000-0000-4000-8000-000000000003": ("AHOR", 1, 8.0, 5),
    "b0000000-0000-4000-8000-000000000004": ("ATEN", 2, 10.0, 7),
}
FACTOR_HORA = {8: 0.7, 9: 1.3, 10: 1.4, 11: 1.1, 12: 0.8, 13: 0.7, 14: 1.0, 15: 0.9}
PROPORCION_PREFERENCIAL = 0.15
PROB_CANCELA = 0.05
PROB_AUSENTE = 0.03
K = 2

NOMBRES = {
    "SOLICITADO": "TurnoSolicitado", "EN_ESPERA": "TurnoEnEspera", "PROXIMO": "TurnoProximo",
    "LLAMADO": "TurnoLlamado", "EN_ATENCION": "TurnoEnAtencion", "ATENDIDO": "TurnoAtendido",
    "CANCELADO": "TurnoCancelado", "AUSENTE": "TurnoAusente",
}


def _poisson(rng: random.Random, media: float) -> int:
    limite, k, p = math.exp(-media), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limite:
            return k
        k += 1


def simular_fila(agencia_id: str, servicio_id: str, inicio: date, dias: int, semilla: int) -> list[dict]:
    """Eventos de una fila durante varios días hábiles. Misma semilla, mismos eventos."""
    rng = random.Random(f"{semilla}:{agencia_id}:{servicio_id}")
    _, ventanillas, media_min, base = SERVICIOS[servicio_id]
    factor_agencia = 1.5 if agencia_id.endswith("1") or agencia_id.endswith("2") else 1.0  # Mocoa y Puerto Asís
    eventos: list[dict] = []

    for d in range(dias):
        dia = inicio + timedelta(days=d)
        if dia.isoweekday() > 5:
            continue
        factor_dia = 1.6 if tipo_dia(dia) == "QUINCENA" else (1.3 if dia.isoweekday() == 1 else 1.0)
        apertura = datetime.combine(dia, time(8, 0), tzinfo=ZONA_AGENCIAS)

        llegadas = []
        for hora, factor in FACTOR_HORA.items():
            for _ in range(_poisson(rng, base * factor * factor_dia * factor_agencia)):
                minuto = (hora - 8) * 60 + rng.uniform(0, 60)
                tipo = "PREFERENCIAL" if rng.random() < PROPORCION_PREFERENCIAL else "GENERAL"
                llegadas.append((minuto, tipo, str(uuid.UUID(int=rng.getrandbits(128), version=4)), rng.choice(("WEB", "MOVIL"))))
        llegadas.sort()

        libres = [0.0] * ventanillas
        preferenciales: deque = deque()
        generales: deque = deque()
        i, racha = 0, 0
        while i < len(llegadas) or preferenciales or generales:
            ventanilla = min(range(ventanillas), key=libres.__getitem__)
            ahora = libres[ventanilla]
            while i < len(llegadas) and llegadas[i][0] <= ahora:
                llegada = llegadas[i]
                i += 1
                if rng.random() < PROB_CANCELA:
                    _registrar(eventos, agencia_id, servicio_id, llegada, apertura,
                               [("SOLICITADO", 0), ("EN_ESPERA", 0.02), ("CANCELADO", rng.uniform(2, 15))])
                    continue
                (preferenciales if llegada[1] == "PREFERENCIAL" else generales).append(llegada)
            if not preferenciales and not generales:
                if i < len(llegadas):
                    libres[ventanilla] = llegadas[i][0]
                continue
            if preferenciales and (racha < K or not generales):
                llegada, racha = preferenciales.popleft(), racha + 1
            else:
                llegada, racha = generales.popleft(), 0
            espera = ahora - llegada[0]
            proximo = max(0.02, espera - 5)
            if rng.random() < PROB_AUSENTE:
                _registrar(eventos, agencia_id, servicio_id, llegada, apertura,
                           [("SOLICITADO", 0), ("EN_ESPERA", 0.02), ("PROXIMO", proximo), ("LLAMADO", espera), ("AUSENTE", espera + 3)])
                libres[ventanilla] = ahora + 3
                continue
            duracion = rng.lognormvariate(math.log(media_min), 0.5)
            _registrar(eventos, agencia_id, servicio_id, llegada, apertura,
                       [("SOLICITADO", 0), ("EN_ESPERA", 0.02), ("PROXIMO", proximo), ("LLAMADO", espera),
                        ("EN_ATENCION", espera + 0.5), ("ATENDIDO", espera + 0.5 + duracion)])
            libres[ventanilla] = ahora + 0.5 + duracion
    return eventos


def _registrar(eventos: list[dict], agencia_id: str, servicio_id: str, llegada: tuple, apertura: datetime,
               pasos: list[tuple[str, float]]) -> None:
    minuto, tipo, turno_id, canal = llegada
    anterior = None
    for estado, desfase in pasos:
        instante = (apertura + timedelta(minutes=minuto + desfase)).astimezone(timezone.utc)
        eventos.append({
            "eventoId": f"{turno_id}:{estado}",
            "tipo": NOMBRES[estado],
            "turnoId": turno_id,
            "agenciaId": agencia_id,
            "servicioId": servicio_id,
            "asociadoId": "simulado",
            "tipoAtencion": tipo,
            "estadoAnterior": anterior,
            "estadoNuevo": estado,
            "actorId": None,
            "canal": canal,
            "ocurridoEn": instante,
            "datos": {},
        })
        anterior = estado


def a_compacto(evento: dict) -> tuple[str, str, float, str, str]:
    return (evento["turnoId"], evento["estadoNuevo"], evento["ocurridoEn"].timestamp(), evento["tipoAtencion"], evento["canal"])
