"""Acceso a ClickHouse por su interfaz HTTP (asíncrona con httpx). Las consultas usan parámetros
tipados ({nombre:Tipo}) en lugar de concatenar texto, así que no hay inyección de SQL."""

import json
from datetime import date
from uuid import UUID

import httpx

from app.config import Configuracion

COLUMNAS = (
    "turno_id, agencia_id, servicio_id, fecha, franja, dia_semana, tipo_dia, tipo_atencion, "
    "canal, estado_final, espera_seg, atencion_seg, solicitado_en, colaborador_id"
)
TAMANO_LOTE = 50_000
OpcionHistorica = tuple[UUID, UUID, UUID | None]

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
            SELECT quantileExactIf(0.5)(atencion_seg, estado_final = 'ATENDIDO' AND atencion_seg > 0) AS atencion_mediana_seg,
                   ifNotFinite(avg(tipo_atencion = 'PREFERENCIAL'), 0) AS p_preferencial,
                   countIf(estado_final = 'ATENDIDO' AND atencion_seg > 0) AS muestras
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
                   ifNotFinite(round(avgIf(atencion_seg, estado_final = 'ATENDIDO' AND atencion_seg > 0) / 60, 1), 0) AS atencion_promedio_min,
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

    async def vaciar_hechos(self) -> None:
        """ClickHouse es una copia analítica de MongoDB: tras limpiar la demostración se vacía y se recarga."""
        respuesta = await self._cliente.post("/", content="TRUNCATE TABLE IF EXISTS cootep.hechos_turno", headers=self._cabeceras)
        respuesta.raise_for_status()
