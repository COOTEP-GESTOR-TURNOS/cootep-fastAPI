"""Turnos ficticios reproducibles para el catálogo y los asociados de la instalación local."""

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping
from datetime import date, datetime, time, timedelta, timezone
from random import Random
from uuid import UUID, uuid5

from pydantic import BaseModel

from app.config import ZONA_AGENCIAS
from app.schemas_demanda import TipoEvento
from app.services.calendario_demanda import cierre_calendario, marcas_calendario
from app.services.simulacion import NOMBRES, PROB_AUSENTE, PROB_CANCELA, PROPORCION_PREFERENCIAL

ESPACIO_DEMO = UUID("ef927fbf-a04e-55e5-b6fc-7bb3f7e5a259")
MINUTO = timedelta(minutes=1)
DIAS_PRONOSTICO = 90
MEDIAS_SERVICIO = {"CAJA": 4, "CRED": 15, "AHOR": 8, "ATEN": 10}
PESO_SERVICIO = {"CAJA": 4, "CRED": 2, "AHOR": 2, "ATEN": 3}


class ServicioDemo(BaseModel):
    id: str
    codigo: str
    prefijo: str
    ventanillas: int
    activa: bool
    apertura: time
    cierre: time


class FuncionarioDemo(BaseModel):
    id: str
    servicioId: str | None


class AgenciaDemo(BaseModel):
    id: str
    codigo: str
    nombre: str
    municipio: str
    departamento: str | None = None
    activa: bool
    servicios: list[ServicioDemo]
    funcionarios: list[FuncionarioDemo]
    asociados: list[str]


class CatalogoDemo(BaseModel):
    semilla: int
    agencias: list[AgenciaDemo]


def fechas(desde: date, hasta: date) -> Iterator[date]:
    for dias in range((hasta - desde).days):
        yield desde + timedelta(days=dias)


def programar_eventos(catalogo: CatalogoDemo, desde: date, hasta: date) -> Iterator[dict[str, str]]:
    for indice, agencia in enumerate(catalogo.agencias):
        for fecha in fechas(desde, hasta):
            mes = fecha.year * 12 + fecha.month
            programacion: dict[TipoEvento, int] = {
                "FESTIVO_LOCAL": 13 + (mes * 7 + indice * 5) % 10,
                "PAGO_SUBSIDIO_FAMILIAR": 15 + (mes * 3 + indice * 7) % 10,
                "PAGO_NOMINA": 23 + (mes * 5 + indice * 3) % 6,
            }
            for tipo, dia in programacion.items():
                if fecha.day == dia:
                    yield {
                        "agenciaId": agencia.id, "fecha": fecha.isoformat(), "tipo": tipo,
                        "id": str(uuid5(ESPACIO_DEMO, f"evento:{agencia.id}:{fecha}:{tipo}")),
                    }


def jornada_cerrada(agencia: AgenciaDemo, fecha: date, tipos: Collection[str]) -> bool:
    return not agencia.activa or cierre_calendario(marcas_calendario(fecha, agencia.departamento, agencia.municipio), tipos)


def selecciones_pendientes(catalogo: CatalogoDemo, ahora: datetime, eventos: Mapping[tuple[str, date], Collection[str]]) -> dict:
    hoy = ahora.date()
    hora = ahora.time()
    abiertas = []
    for agencia in catalogo.agencias:
        if jornada_cerrada(agencia, hoy, eventos.get((agencia.id, hoy), ())):
            continue
        servicio = next((servicio for servicio in agencia.servicios if servicio.activa and servicio.apertura <= hora < servicio.cierre), None)
        if servicio is not None:
            abiertas.append({"agenciaId": agencia.id, "servicioId": servicio.id, "asociados": agencia.asociados})
    return {"fecha": hoy.isoformat(), "agencias": abiertas}


def simular_jornada(agencia: AgenciaDemo, fecha: date, semilla: int, tipos: Collection[str]) -> Iterator[dict]:
    servicios = [servicio for servicio in agencia.servicios if servicio.activa and servicio.apertura < servicio.cierre]
    if not servicios or jornada_cerrada(agencia, fecha, tipos):
        return

    rng = Random(f"turnos:{semilla}:{agencia.id}:{fecha}")
    marcas = marcas_calendario(fecha, agencia.departamento, agencia.municipio)
    volumen = Random(f"volumen:{semilla}:{agencia.id}").uniform(18, 34)
    volumen *= (1.2, 1.0, 1.0, 1.05, 1.1)[fecha.weekday()]
    volumen *= 1 + 0.12 * marcas.cercania_5 + 0.10 * marcas.cercania_10
    volumen *= 1.45 if "PAGO_SUBSIDIO_FAMILIAR" in tipos else 1
    volumen *= 1.35 if "PAGO_NOMINA" in tipos else 1
    cantidad = max(1, round(rng.normalvariate(volumen, volumen ** 0.5)))
    asociados = rng.sample(agencia.asociados, min(cantidad, len(agencia.asociados)))
    servicios_elegidos = rng.choices(servicios, [PESO_SERVICIO.get(servicio.codigo, 1) for servicio in servicios], k=len(asociados))
    por_servicio: dict[str, list[str]] = defaultdict(list)
    for servicio, asociado in zip(servicios_elegidos, asociados, strict=True):
        por_servicio[servicio.id].append(asociado)

    for servicio in servicios:
        inicio = datetime.combine(fecha, servicio.apertura, ZONA_AGENCIAS)
        cierre = datetime.combine(fecha, servicio.cierre, ZONA_AGENCIAS)
        lapso = cierre - inicio
        funcionarios = [f.id for f in agencia.funcionarios if f.servicioId in (None, servicio.id)]
        if not funcionarios:
            raise ValueError(f"El servicio {servicio.id} de {agencia.id} requiere un funcionario de demostración")
        llegadas = [(inicio + lapso * (0.8 * rng.betavariate(1.5, 2)), asociado) for asociado in por_servicio[servicio.id]]
        libres = [inicio] * min(servicio.ventanillas, len(funcionarios))
        for numero, (solicitado, asociado) in enumerate(sorted(llegadas), start=1):
            ventanilla = min(range(len(libres)), key=libres.__getitem__)
            espera = min(MINUTO * rng.uniform(1, 14), lapso / 8)
            llamado = max(libres[ventanilla], solicitado + espera)
            duracion = MINUTO * rng.lognormvariate(0, 0.35) * MEDIAS_SERVICIO.get(servicio.codigo, 8)
            atencion = min(duracion, lapso / 10)
            turno, fin_ventanilla = _simular_turno(
                agencia, servicio, asociado, funcionarios[ventanilla], numero,
                solicitado, llamado, atencion, cierre, rng,
            )
            if fin_ventanilla is not None:
                libres[ventanilla] = fin_ventanilla
            yield turno


def _iso(instante: datetime) -> str:
    return instante.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _simular_turno(
    agencia: AgenciaDemo, servicio: ServicioDemo, asociado: str, funcionario: str, numero: int,
    solicitado: datetime, llamado: datetime, duracion: timedelta, cierre: datetime, rng: Random,
) -> tuple[dict, datetime | None]:
    fecha = solicitado.date().isoformat()
    turno_id = str(uuid5(ESPACIO_DEMO, f"turno:{agencia.id}:{servicio.id}:{fecha}:{numero}"))
    turno = {
        "id": turno_id, "agenciaId": agencia.id, "servicioId": servicio.id, "asociadoId": asociado,
        "fecha": fecha, "numero": numero, "codigo": f"{servicio.prefijo}-{numero:03d}",
        "tipoAtencion": "PREFERENCIAL" if rng.random() < PROPORCION_PREFERENCIAL else "GENERAL",
        "canal": rng.choice(("WEB", "MOVIL")), "solicitadoEn": _iso(solicitado),
        "proximoEn": None, "confirmadoEn": None, "llamadoEn": None, "inicioAtencionEn": None,
        "atendidoPor": None, "verificadoPor": None, "motivo": None, "idempotencyKey": None,
    }
    pasos = [("SOLICITADO", asociado, solicitado), ("EN_ESPERA", None, solicitado)]
    fin_ventanilla = None
    if llamado + duracion >= cierre:
        turno["motivo"] = "CIERRE_JORNADA"
        pasos.append(("CANCELADO", None, cierre))
    elif rng.random() < PROB_CANCELA:
        turno["motivo"] = "CANCELADO_POR_ASOCIADO"
        pasos.append(("CANCELADO", asociado, solicitado + (llamado - solicitado) / 2))
    else:
        proximo = max(solicitado, llamado - MINUTO * 2)
        turno.update(proximoEn=_iso(proximo), llamadoEn=_iso(llamado), atendidoPor=funcionario)
        pasos.extend([("PROXIMO", None, proximo), ("LLAMADO", funcionario, llamado)])
        if rng.random() < PROB_AUSENTE:
            turno["motivo"] = "NO_SE_PRESENTO"
            fin_ventanilla = min(llamado + MINUTO * 3, cierre)
            pasos.append(("AUSENTE", funcionario, fin_ventanilla))
        else:
            inicio = llamado + min(timedelta(seconds=15), (cierre - llamado - duracion) / 2)
            fin_ventanilla = inicio + duracion
            turno.update(inicioAtencionEn=_iso(inicio), verificadoPor=funcionario if turno["tipoAtencion"] == "PREFERENCIAL" else None)
            pasos.extend([("EN_ATENCION", funcionario, inicio), ("ATENDIDO", funcionario, fin_ventanilla)])

    turno.update(estado=pasos[-1][0], finEn=_iso(pasos[-1][2]))
    campos_evento = {clave: turno[clave] for clave in ("agenciaId", "servicioId", "asociadoId", "tipoAtencion", "canal")}
    eventos = []
    anterior = None
    for estado, actor_id, instante in pasos:
        eventos.append({
            **campos_evento, "eventoId": f"{turno_id}:{estado}", "tipo": NOMBRES[estado], "turnoId": turno_id,
            "estadoAnterior": anterior, "estadoNuevo": estado, "actorId": actor_id, "ocurridoEn": _iso(instante), "datos": {},
        })
        anterior = estado
    return {"turno": turno, "eventos": eventos}, fin_ventanilla
