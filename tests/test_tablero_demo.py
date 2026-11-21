"""Conciliación del tablero mensual y publicación del corte de demostración."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.schemas_demanda import AgenciaTablero, CalendarioDemanda, CoberturaDemo, DiaDemanda, EventoAgencia, SolicitudTablero
from app.services.demanda_service import DemandaService
from app.services.proyeccion_demo import ProyeccionDemo, unir_intervalos
from app.services.resumen_demo import CorteDemo, InstantaneaDemo, TotalesDia
from app.services.tablero_service import TableroService, calendario_general

MOCOA = UUID("a0000000-0000-4000-8000-000000000001")
ORITO = UUID("a0000000-0000-4000-8000-000000000003")
SERVICIO = UUID("b0000000-0000-4000-8000-000000000001")
EVENTO = UUID("b0000000-0000-4000-8000-000000000002")


def test_tablero_concilia_pendientes_hoy_y_pronostico_general():
    hoy = date(2026, 4, 16)
    corte = CorteDemo(str(EVENTO), 1, datetime(2026, 4, 16, 14, tzinfo=timezone.utc), ((hoy - timedelta(days=84), hoy),))
    dias_mocoa = {hoy - timedelta(days=d): TotalesDia(atendidos=30) for d in range(1, 85)}
    dias_orito = {hoy - timedelta(days=d): TotalesDia(atendidos=10) for d in range(1, 85)}
    dias_mocoa[hoy] = TotalesDia(atendidos=2, cancelados=1, pendientes=3, espera_seg=120)
    dias_orito[hoy] = TotalesDia(atendidos=1, ausentes=1, pendientes=2, espera_seg=120)
    instantanea = InstantaneaDemo(corte, {MOCOA: dias_mocoa, ORITO: dias_orito})

    class Proyeccion:
        lecturas = 0

        async def leer(self, agencias, desde, hasta, servicio_id=None, colaborador_id=None, *, corte=None):
            self.lecturas += 1
            assert desde <= hoy <= hasta
            assert set(agencias) <= {MOCOA, ORITO}
            if colaborador_id is not None:
                assert servicio_id == SERVICIO and corte is instantanea.corte
                return InstantaneaDemo(corte, {MOCOA: {hoy: TotalesDia(atendidos=2)}})
            return instantanea

    class EventosAgencia:
        async def eventos(self, agencia, desde, hasta):
            return {hoy: [EventoAgencia(id=EVENTO, fecha=hoy, tipo="PAGO_NOMINA")]} if agencia == MOCOA else {}

    payload = {"mes": "2026-04", "agencias": [
        {"agenciaId": str(MOCOA), "nombre": "Mocoa", "departamento": "PUTUMAYO", "municipio": "Mocoa"},
        {"agenciaId": str(ORITO), "nombre": "Orito", "departamento": "PUTUMAYO", "municipio": "Orito"},
    ]}
    proyeccion = Proyeccion()
    tablero = TableroService(DemandaService(EventosAgencia(), demo=True), None, proyeccion)
    general = asyncio.run(tablero.consultar(SolicitudTablero.model_validate(payload), hoy=hoy))

    assert proyeccion.lecturas == 1
    assert general.actualizado_en == corte.actualizado_en
    assert general.calendario.agencia_id is None
    assert general.resumen.solicitados == sum(dia.turnos_registrados or 0 for dia in general.calendario.dias)
    assert general.resumen.solicitados == (general.resumen.atendidos + general.resumen.ausentes
                                           + general.resumen.cancelados + general.resumen.pendientes)
    assert general.resumen.solicitados == sum(agencia.solicitados for agencia in general.agencias)
    assert general.resumen.pendientes == 5
    assert general.calendario.dias[15].turnos_registrados == 10
    assert general.calendario.dias[15].eventos[0].agencia_id == MOCOA
    assert general.calendario.dias[16].festivos_putumayo and general.calendario.dias[16].turnos_estimados is None
    assert general.calendario.dias[17].fin_de_semana and general.calendario.dias[17].turnos_estimados is None

    detalles = [asyncio.run(tablero.consultar(SolicitudTablero.model_validate({
        **payload, "agencias": [agencia], "agenciaId": agencia["agenciaId"],
    }), hoy=hoy)) for agencia in payload["agencias"]]
    assert general.calendario.dias[15].turnos_estimados == sum(
        detalle.calendario.dias[15].turnos_estimados for detalle in detalles
    )
    colaborador = asyncio.run(tablero.consultar(SolicitudTablero.model_validate({
        **payload, "servicioId": str(SERVICIO), "colaboradorId": str(EVENTO),
    }), hoy=hoy))
    assert colaborador.resumen.solicitados == 2
    assert colaborador.resumen.pendientes == 0
    assert colaborador.calendario == general.calendario


def test_calendario_general_solo_exige_pronostico_de_agencias_habiles():
    hoy = date(2026, 10, 2)

    def dia(numero, habil, total):
        return DiaDemanda(fecha=date(2026, 10, numero), dia_habil=habil, fin_de_semana=numero == 3,
                          festivo_nacional=None, festivos_putumayo=(), vencimiento_credito=False,
                          eventos=[], turnos_registrados=None, turnos_estimados=total, nivel_demanda=None)

    agencias = [AgenciaTablero(agencia_id=MOCOA, nombre="Mocoa"), AgenciaTablero(agencia_id=ORITO, nombre="Orito")]
    mocoa = CalendarioDemanda(agencia_id=MOCOA, mes="2026-10", fuente="REAL", estado_pronostico="DISPONIBLE",
                              dias=[dia(2, False, None), dia(3, False, None), dia(5, True, 4)])
    orito = CalendarioDemanda(agencia_id=ORITO, mes="2026-10", fuente="REAL", estado_pronostico="HISTORIAL_INSUFICIENTE",
                              dias=[dia(2, True, 12), dia(3, False, None), dia(5, True, None)])
    general = calendario_general([mocoa, orito], agencias, hoy=hoy)

    assert general.dias[0].dia_habil and general.dias[0].turnos_estimados == 12
    assert not general.dias[1].dia_habil and general.dias[1].turnos_estimados is None
    assert general.dias[2].dia_habil and general.dias[2].turnos_estimados is None
    assert general.estado_pronostico == "INCOMPLETO"


def test_corte_se_conserva_si_falla_y_refresca_cambio_de_estado():
    hoy = datetime.now(timezone.utc).date() - timedelta(days=1)
    instante = datetime(hoy.year, hoy.month, hoy.day, 14, tzinfo=timezone.utc).timestamp()

    class Eventos:
        publicado = None
        pausada = False
        eventos = [(str(EVENTO), "SOLICITADO", instante, "PREFERENCIAL", "WEB")]

        async def corte(self):
            return self.publicado, self.pausada

        async def preparar(self):
            self.pausada = True

        async def preparar_indices(self):
            return None

        async def publicar(self, corte):
            self.publicado, self.pausada = corte, False

        async def dias_modificados(self, desde, hasta):
            return {hoy}

        async def eventos_por_fila(self, dias, corte):
            return {(str(MOCOA), str(SERVICIO)): self.eventos}

    class ClickHouse:
        falla = False
        calculados = {}

        async def preparar_tabla_demo(self):
            return None

        async def guardar_diarios_demo(self, corte, diarios):
            if self.falla:
                raise RuntimeError("carga interrumpida")
            self.calculados = diarios

    async def probar():
        eventos, clickhouse = Eventos(), ClickHouse()
        with ThreadPoolExecutor(1) as pool:
            proyeccion = ProyeccionDemo(eventos, clickhouse, pool)
            cobertura = CoberturaDemo(desde=hoy, hasta=hoy)
            await proyeccion.preparar()
            primero = await proyeccion.publicar(cobertura)
            base = clickhouse.calculados[str(MOCOA), hoy.isoformat(), str(SERVICIO), None]
            assert base.solicitados == base.pendientes == 1
            await proyeccion.preparar()
            clickhouse.falla = True
            with pytest.raises(RuntimeError):
                await proyeccion.publicar(cobertura)
            assert eventos.publicado == primero and eventos.pausada
            await proyeccion.refrescar()
            assert eventos.publicado == primero
            clickhouse.falla = False
            segundo = await proyeccion.publicar(cobertura)
            eventos.eventos += [
                (str(EVENTO), "LLAMADO", instante + 60, "PREFERENCIAL", "WEB"),
                (str(EVENTO), "EN_ATENCION", instante + 120, "PREFERENCIAL", "WEB"),
                (str(EVENTO), "ATENDIDO", instante + 300, "PREFERENCIAL", "WEB"),
            ]
            await proyeccion.refrescar()
            assert eventos.publicado.generacion == segundo.generacion and eventos.publicado.revision > segundo.revision
            atendido = clickhouse.calculados[str(MOCOA), hoy.isoformat(), str(SERVICIO), None]
            assert atendido.solicitados == atendido.atendidos == 1 and atendido.pendientes == 0
            assert atendido.valores().atencion_promedio_min == 3

    asyncio.run(probar())


def test_cobertura_conserva_huecos_en_el_historial():
    primeros = (date(2026, 4, 1), date(2026, 4, 10))
    contiguos = (date(2026, 4, 11), date(2026, 4, 20))
    separados = (date(2026, 5, 1), date(2026, 5, 7))
    assert unir_intervalos((separados, contiguos, primeros)) == ((primeros[0], contiguos[1]), separados)


def test_corte_anterior_conserva_totales_sin_inventar_desglose():
    dia = date(2026, 4, 16)
    corte = CorteDemo(str(EVENTO), 1, datetime(2026, 4, 16, 14, tzinfo=timezone.utc), ((dia, dia),), version=1)

    class Eventos:
        async def corte(self):
            return corte, False

    class ClickHouse:
        async def diarios_demo(self, publicado, agencias, desde, hasta, servicio_id, colaborador_id):
            assert publicado == corte and servicio_id is None and colaborador_id is None
            return {MOCOA: {dia: TotalesDia(atendidos=3)}}

    async def probar():
        with ThreadPoolExecutor(1) as pool:
            proyeccion = ProyeccionDemo(Eventos(), ClickHouse(), pool)
            historico = await proyeccion.leer([MOCOA], dia, dia)
            assert historico.total(MOCOA, dia, dia).atendidos == 3
            filtrado = await proyeccion.leer([MOCOA], dia, dia, servicio_id=SERVICIO, corte=historico.corte)
            assert filtrado.corte is None and not filtrado.cubre(dia)

    asyncio.run(probar())
