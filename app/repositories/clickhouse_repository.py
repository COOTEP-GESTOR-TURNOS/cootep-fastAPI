"""Acceso a ClickHouse por su interfaz HTTP (asíncrona con httpx). Las consultas usan parámetros
tipados ({nombre:Tipo}) en lugar de concatenar texto, así que no hay inyección de SQL."""

import json
from dataclasses import asdict
from datetime import date
from uuid import UUID

import httpx

from app.config import Configuracion
from app.services.resumen_demo import CorteDemo, DimensionDia, TotalesDia

COLUMNAS = (
    "turno_id, agencia_id, servicio_id, fecha, franja, dia_semana, tipo_dia, tipo_atencion, "
    "canal, estado_final, espera_seg, atencion_seg, solicitado_en, colaborador_id"
)
TAMANO_LOTE = 50_000
OpcionHistorica = tuple[UUID, UUID, UUID | None]

DIARIOS_DEMO_VIGENTES = """
    WITH ultimas AS (
        SELECT agencia_id, fecha, max(revision) AS ultima_revision
        FROM cootep.hechos_turno_demo_v2
        WHERE generacion = {generacion:UUID} AND revision <= {revision:UInt64}
          AND has(splitByChar(',', {agencias:String}), toString(agencia_id))
          AND fecha BETWEEN {desde:Date} AND {hasta:Date}
        GROUP BY agencia_id, fecha
    ), vigentes AS (
        SELECT demo.agencia_id AS agencia_id, demo.fecha AS fecha, demo.servicio_id AS servicio_id,
               demo.colaborador_id AS colaborador_id, demo.atendidos, demo.cancelados, demo.ausentes,
               demo.pendientes, demo.espera_seg, demo.atencion_seg, demo.preferenciales_finalizados
        FROM cootep.hechos_turno_demo_v2 AS demo INNER JOIN ultimas
          ON demo.agencia_id = ultimas.agencia_id AND demo.fecha = ultimas.fecha
          AND demo.revision = ultimas.ultima_revision
        WHERE demo.generacion = {generacion:UUID}
    )
"""


def _filtros(servicio_id: UUID | None, colaborador_id: UUID | None) -> tuple[str, dict[str, str]]:
    condiciones = []
    parametros = {}
    if servicio_id is not None:
        condiciones.append(" AND servicio_id = {servicio:UUID}")
        parametros["servicio"] = str(servicio_id)
    if colaborador_id is not None:
        condiciones.append(" AND colaborador_id = {colaborador:UUID}")
        parametros["colaborador"] = str(colaborador_id)
    return "".join(condiciones), parametros


def _parametros_periodo(agencias: list[UUID], desde: date, hasta: date) -> dict[str, str]:
    return {"agencias": ",".join(map(str, agencias)), "desde": desde.isoformat(), "hasta": hasta.isoformat()}


def _opciones(filas: list[dict]) -> list[OpcionHistorica]:
    return [
        (UUID(fila["agencia_id"]), UUID(fila["servicio_id"]), UUID(fila["colaborador_id"]) if fila["colaborador_id"] else None)
        for fila in filas
    ]


class ClickHouseRepository:
    def __init__(self, cliente: httpx.AsyncClient, configuracion: Configuracion) -> None:
        self._cliente = cliente
        self._cabeceras = {
            "X-ClickHouse-User": configuracion.clickhouse_user,
            "X-ClickHouse-Key": configuracion.clickhouse_password,
        }

    async def _consultar(self, sql: str, parametros: dict[str, str]) -> list[dict]:
        respuesta = await self._cliente.post(
            "/",
            params={f"param_{clave}": valor for clave, valor in parametros.items()},
            content=f"{sql} FORMAT JSON",
            headers=self._cabeceras,
        )
        respuesta.raise_for_status()
        return respuesta.json()["data"]

    async def espera_por_franja(self, agencia_id: UUID, servicio_id: UUID, dia_semana: int, tipo_dia: str) -> list[dict]:
        filas = await self._consultar(
            """
            SELECT franja, quantileExact(0.5)(espera_seg) AS espera_mediana_seg, count() AS turnos
            FROM cootep.hechos_turno FINAL
            WHERE agencia_id = {agencia:UUID} AND servicio_id = {servicio:UUID}
              AND dia_semana = {dia:UInt8} AND tipo_dia = {tipo_dia:String} AND estado_final = 'ATENDIDO'
            GROUP BY franja
            ORDER BY franja
            """,
            {"agencia": str(agencia_id), "servicio": str(servicio_id), "dia": str(dia_semana), "tipo_dia": tipo_dia},
        )
        return [
            {"franja": f["franja"], "espera_mediana_seg": float(f["espera_mediana_seg"]), "turnos": int(f["turnos"])}
            for f in filas
        ]

    async def parametros_franja(self, agencia_id: UUID, servicio_id: UUID, franja: str) -> dict | None:
        filas = await self._consultar(
            """
            SELECT quantileExactIf(0.5)(atencion_seg, estado_final = 'ATENDIDO') AS atencion_mediana_seg,
                   ifNotFinite(avg(tipo_atencion = 'PREFERENCIAL'), 0) AS p_preferencial,
                   countIf(estado_final = 'ATENDIDO') AS muestras
            FROM cootep.hechos_turno FINAL
            WHERE agencia_id = {agencia:UUID} AND servicio_id = {servicio:UUID} AND franja = {franja:String}
            """,
            {"agencia": str(agencia_id), "servicio": str(servicio_id), "franja": franja},
        )
        if not filas:
            return None
        f = filas[0]
        return {
            "atencion_mediana_seg": float(f["atencion_mediana_seg"]),
            "p_preferencial": float(f["p_preferencial"]),
            "muestras": int(f["muestras"]),
        }

    async def indicadores(
        self, agencia_id: UUID, desde: date, hasta: date, servicio_id: UUID | None = None,
        colaborador_id: UUID | None = None,
    ) -> dict:
        condiciones, parametros = _filtros(servicio_id, colaborador_id)
        filas = await self._consultar(
            """
            SELECT countIf(estado_final = 'ATENDIDO') AS atendidos,
                   countIf(estado_final = 'CANCELADO') AS cancelados,
                   countIf(estado_final = 'AUSENTE') AS ausentes,
                   ifNotFinite(round(avgIf(espera_seg, estado_final = 'ATENDIDO') / 60, 1), 0) AS espera_promedio_min,
                   ifNotFinite(round(avgIf(atencion_seg, estado_final = 'ATENDIDO') / 60, 1), 0) AS atencion_promedio_min,
                   ifNotFinite(round(avg(tipo_atencion = 'PREFERENCIAL'), 3), 0) AS proporcion_preferencial
            FROM cootep.hechos_turno FINAL
            WHERE agencia_id = {agencia:UUID} AND fecha BETWEEN {desde:Date} AND {hasta:Date}
            """ + condiciones,
            {"agencia": str(agencia_id), "desde": desde.isoformat(), "hasta": hasta.isoformat(), **parametros},
        )
        f = filas[0]
        return {
            "atendidos": int(f["atendidos"]),
            "cancelados": int(f["cancelados"]),
            "ausentes": int(f["ausentes"]),
            "espera_promedio_min": float(f["espera_promedio_min"]),
            "atencion_promedio_min": float(f["atencion_promedio_min"]),
            "proporcion_preferencial": float(f["proporcion_preferencial"]),
        }

    async def opciones_historicas(
        self, agencias: list[UUID], desde: date, hasta: date, servicio_id: UUID | None = None,
    ) -> list[OpcionHistorica]:
        if not agencias or desde > hasta:
            return []
        condiciones, parametros = _filtros(servicio_id, None)
        filas = await self._consultar(
            """
            SELECT DISTINCT agencia_id, servicio_id, colaborador_id
            FROM cootep.hechos_turno FINAL
            WHERE has(splitByChar(',', {agencias:String}), toString(agencia_id))
              AND fecha BETWEEN {desde:Date} AND {hasta:Date}
            """ + condiciones,
            {**_parametros_periodo(agencias, desde, hasta), **parametros},
        )
        return _opciones(filas)

    async def preparar_responsables(self) -> None:
        respuesta = await self._cliente.post(
            "/", content="ALTER TABLE cootep.hechos_turno ADD COLUMN IF NOT EXISTS colaborador_id Nullable(UUID)",
            headers=self._cabeceras,
        )
        respuesta.raise_for_status()

    async def insertar_hechos(self, hechos: list[dict]) -> int:
        """Inserta en lotes con JSONEachRow. ReplacingMergeTree reemplaza los turnos repetidos."""
        for inicio in range(0, len(hechos), TAMANO_LOTE):
            lote = hechos[inicio : inicio + TAMANO_LOTE]
            respuesta = await self._cliente.post(
                "/",
                params={"query": f"INSERT INTO cootep.hechos_turno ({COLUMNAS}) FORMAT JSONEachRow"},
                content="\n".join(json.dumps(h) for h in lote),
                headers=self._cabeceras,
            )
            respuesta.raise_for_status()
        return len(hechos)

    async def preparar_tabla_demo(self) -> None:
        respuesta = await self._cliente.post(
            "/",
            content="""
                CREATE TABLE IF NOT EXISTS cootep.hechos_turno_demo_v2 (
                    generacion UUID,
                    revision UInt64,
                    agencia_id UUID,
                    fecha Date,
                    servicio_id UUID,
                    colaborador_id Nullable(UUID),
                    atendidos UInt64,
                    cancelados UInt64,
                    ausentes UInt64,
                    pendientes UInt64,
                    espera_seg UInt64,
                    atencion_seg UInt64,
                    preferenciales_finalizados UInt64
                ) ENGINE = MergeTree
                ORDER BY (generacion, agencia_id, fecha, revision, servicio_id)
            """,
            headers=self._cabeceras,
        )
        respuesta.raise_for_status()

    async def guardar_diarios_demo(self, corte: CorteDemo, diarios: dict[DimensionDia, TotalesDia]) -> None:
        filas = [
            {"generacion": corte.generacion, "revision": corte.revision, "agencia_id": agencia,
             "fecha": fecha, "servicio_id": servicio, "colaborador_id": colaborador, **asdict(totales)}
            for (agencia, fecha, servicio, colaborador), totales in diarios.items()
        ]
        if filas:
            respuesta = await self._cliente.post(
                "/",
                params={"query": "INSERT INTO cootep.hechos_turno_demo_v2 FORMAT JSONEachRow"},
                content="\n".join(json.dumps(fila) for fila in filas),
                headers=self._cabeceras,
            )
            respuesta.raise_for_status()

    async def diarios_demo(
        self, corte: CorteDemo, agencias: list[UUID], desde: date, hasta: date,
        servicio_id: UUID | None = None, colaborador_id: UUID | None = None,
    ) -> dict[UUID, dict[date, TotalesDia]]:
        if not agencias or desde > hasta:
            return {}
        filtros, parametros = _filtros(servicio_id, colaborador_id)
        parametros.update(self._parametros_demo(corte, agencias, desde, hasta))
        if corte.version == 1:
            return {} if filtros else await self._diarios_demo_legacy(parametros)
        filas = await self._consultar(
            DIARIOS_DEMO_VIGENTES + """
            SELECT agencia_id, fecha,
                   sum(atendidos) AS total_atendidos,
                   sum(cancelados) AS total_cancelados,
                   sum(ausentes) AS total_ausentes,
                   sum(pendientes) AS total_pendientes,
                   sum(espera_seg) AS total_espera_seg,
                   sum(atencion_seg) AS total_atencion_seg,
                   sum(preferenciales_finalizados) AS total_preferenciales_finalizados
            FROM vigentes WHERE 1
            """ + filtros + " GROUP BY agencia_id, fecha", parametros,
        )
        return self._totales_diarios(filas)

    async def opciones_historicas_demo(
        self, corte: CorteDemo, agencias: list[UUID], desde: date, hasta: date, servicio_id: UUID | None = None,
    ) -> list[OpcionHistorica]:
        if corte.version < 2 or not agencias or desde > hasta:
            return []
        filtros, parametros = _filtros(servicio_id, None)
        filas = await self._consultar(
            DIARIOS_DEMO_VIGENTES + "SELECT DISTINCT agencia_id, servicio_id, colaborador_id FROM vigentes WHERE 1" + filtros,
            {**self._parametros_demo(corte, agencias, desde, hasta), **parametros},
        )
        return _opciones(filas)

    @staticmethod
    def _parametros_demo(corte: CorteDemo, agencias: list[UUID], desde: date, hasta: date) -> dict[str, str]:
        return {**_parametros_periodo(agencias, desde, hasta), "generacion": corte.generacion, "revision": str(corte.revision)}

    async def _diarios_demo_legacy(self, parametros: dict[str, str]) -> dict[UUID, dict[date, TotalesDia]]:
        filas = await self._consultar(
            """
            SELECT agencia_id, fecha,
                   argMax(atendidos, revision) AS total_atendidos,
                   argMax(cancelados, revision) AS total_cancelados,
                   argMax(ausentes, revision) AS total_ausentes,
                   argMax(pendientes, revision) AS total_pendientes,
                   argMax(espera_seg, revision) AS total_espera_seg,
                   argMax(atencion_seg, revision) AS total_atencion_seg,
                   argMax(preferenciales_finalizados, revision) AS total_preferenciales_finalizados
            FROM cootep.hechos_turno_demo
            WHERE generacion = {generacion:UUID} AND revision <= {revision:UInt64}
              AND has(splitByChar(',', {agencias:String}), toString(agencia_id))
              AND fecha BETWEEN {desde:Date} AND {hasta:Date}
            GROUP BY agencia_id, fecha
            """,
            parametros,
        )
        return self._totales_diarios(filas)

    @staticmethod
    def _totales_diarios(filas: list[dict]) -> dict[UUID, dict[date, TotalesDia]]:
        diarios: dict[UUID, dict[date, TotalesDia]] = {}
        for fila in filas:
            diarios.setdefault(UUID(fila["agencia_id"]), {})[date.fromisoformat(fila["fecha"])] = TotalesDia(
                **{campo.removeprefix("total_"): int(valor) for campo, valor in fila.items() if campo.startswith("total_")}
            )
        return diarios
