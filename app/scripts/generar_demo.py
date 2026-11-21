"""Simulación de datos de demostración para el comando local demo.ps1.

Los turnos simulados van a las mismas colecciones que los reales (eventos_turno, eventos_agencia),
con la marca origen = SIMULADO. Así la demostración recorre el mismo camino que usaría el banco
(MongoDB → ETL → ClickHouse → indicadores y pronóstico) y se puede borrar con `limpiar`.
Solo corre con ENTORNO=desarrollo: en producción no se generan datos ficticios.
"""

import argparse
import codecs
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
from pymongo import MongoClient, UpdateOne

from app.config import ZONA_AGENCIAS, configuracion
from app.repositories.demanda_repository import ORIGEN_SIMULADO
from app.services.simulacion_demo import (
    DIAS_PRONOSTICO,
    CatalogoDemo,
    fechas,
    programar_eventos,
    selecciones_pendientes,
    simular_jornada,
)

COBERTURA = "demo_cobertura_historial"
# Marca del generador anterior (app.services.simulacion), retirado: sus turnos se borran al limpiar.
ASOCIADO_GENERADOR_ANTERIOR = "simulado"


def etl(reconstruir: bool = False) -> None:
    """Carga completa MongoDB → ClickHouse con la API interna (el historial importado es anterior a la marca del ETL)."""
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=900) as cliente:
        respuesta = cliente.post(
            "/api/v1/etl/ejecutar",
            params={"completa": "true", "reconstruir": str(reconstruir).lower()},
            headers={"X-Service-Token": configuracion().service_token},
        )
        if respuesta.is_error:
            raise SystemExit(f"La analítica no pudo cargar ClickHouse (HTTP {respuesta.status_code}): {respuesta.text}")
        print(f"ClickHouse actualizado: {respuesta.json()['hechosCargados']:,} turnos.", file=sys.stderr)


def leer_eventos(coleccion, desde: date, hasta: date) -> dict[tuple[str, date], set[str]]:
    eventos: dict[tuple[str, date], set[str]] = defaultdict(set)
    for doc in coleccion.find(
        {"fecha": {"$gte": desde.isoformat(), "$lt": hasta.isoformat()}},
        {"agenciaId": 1, "fecha": 1, "tipo": 1, "_id": 0},
    ):
        eventos[doc["agenciaId"], date.fromisoformat(doc["fecha"])].add(doc["tipo"])
    return eventos


def leer_cobertura(coleccion, agencias: list[str]) -> dict[str, list[tuple[date, date]]]:
    return {
        registro["_id"]: [(date.fromisoformat(inicio), date.fromisoformat(fin)) for inicio, fin in registro["intervalos"]]
        for registro in coleccion.find({"_id": {"$in": agencias}})
    }


def marcar_historial(catalogo: CatalogoDemo, hoy: date, dias: int) -> None:
    intervalo = [(hoy - timedelta(days=dias)).isoformat(), hoy.isoformat()]
    cfg = configuracion()
    with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
        coleccion = cliente[cfg.mongo_db][COBERTURA]
        for agencia in catalogo.agencias:
            coleccion.update_one({"_id": agencia.id}, {"$addToSet": {"intervalos": intervalo}}, upsert=True)


def simular(catalogo: CatalogoDemo, hoy: date, dias: int) -> None:
    """Programa los eventos de cada agencia (historial y los próximos 90 días) y escribe los turnos en JSONL."""
    desde = hoy - timedelta(days=dias)
    hasta_eventos = hoy + timedelta(days=DIAS_PRONOSTICO)
    cfg = configuracion()
    with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
        db = cliente[cfg.mongo_db]
        coleccion = db["eventos_agencia"]
        operaciones = []
        for evento in programar_eventos(catalogo, desde, hasta_eventos):
            # Un evento real del mismo día y tipo se conserva: el simulado no lo reemplaza.
            operaciones.append(UpdateOne(
                {"agenciaId": evento["agenciaId"], "$or": [
                    {"id": evento["id"]}, {"fecha": evento["fecha"], "tipo": evento["tipo"]},
                ]},
                {"$setOnInsert": evento}, upsert=True,
            ))
        if operaciones:
            coleccion.bulk_write(operaciones, ordered=False)
        calendario = leer_eventos(coleccion, desde, hasta_eventos)
        cobertura = leer_cobertura(db[COBERTURA], [agencia.id for agencia in catalogo.agencias])

    total = 0
    for agencia in catalogo.agencias:
        intervalos = cobertura.get(agencia.id, [])
        for fecha in fechas(desde, hoy):
            if any(inicio <= fecha < fin for inicio, fin in intervalos):
                continue
            for turno in simular_jornada(agencia, fecha, catalogo.semilla, calendario.get((agencia.id, fecha), ())):
                print(json.dumps(turno, separators=(",", ":")))
                total += 1
    print(f"Simulados {total:,} turnos históricos para {len(catalogo.agencias)} agencias.", file=sys.stderr)


def limpiar() -> None:
    """Borra de MongoDB lo simulado (y el historial del generador anterior) y reconstruye ClickHouse.

    Las cuentas, los turnos de PostgreSQL y los eventos de los turnos pendientes de la demostración
    los borra antes el backend (`demo.js limpiar`), que conoce a los participantes.
    """
    cfg = configuracion()
    with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
        db = cliente[cfg.mongo_db]
        turnos = db["eventos_turno"].delete_many(
            {"$or": [{"origen": ORIGEN_SIMULADO}, {"asociadoId": ASOCIADO_GENERADOR_ANTERIOR}]})
        eventos = db["eventos_agencia"].delete_many({"origen": ORIGEN_SIMULADO})
        db[COBERTURA].drop()
    print(f"MongoDB: {turnos.deleted_count:,} eventos de turnos y {eventos.deleted_count:,} eventos de agencia simulados borrados.",
          file=sys.stderr)
    etl(reconstruir=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modos = parser.add_subparsers(dest="modo", required=True)
    for modo in ("fecha", "pendientes", "cargar", "limpiar"):
        modos.add_parser(modo)
    modos_con_rango = ("historial", "marcar-historial")
    for modo in modos_con_rango:
        con_rango = modos.add_parser(modo)
        con_rango.add_argument("--hoy", type=date.fromisoformat, required=True)
        con_rango.add_argument("--dias", type=int, required=True)
    opciones = parser.parse_args()

    if configuracion().entorno != "desarrollo":
        raise SystemExit("Los datos de demostración solo se generan con ENTORNO=desarrollo (nunca en producción).")
    if opciones.modo in modos_con_rango and opciones.dias <= 0:
        parser.error("El historial requiere al menos un día")
    ahora = datetime.now(ZONA_AGENCIAS)
    if opciones.modo == "fecha":
        print(ahora.date().isoformat())
    elif opciones.modo == "cargar":
        etl()
    elif opciones.modo == "limpiar":
        limpiar()
    else:
        # Windows PowerShell 5.1 antepone una marca BOM al pasar archivos por tubería.
        catalogo = CatalogoDemo.model_validate_json(sys.stdin.buffer.read().removeprefix(codecs.BOM_UTF8))
        if opciones.modo == "historial":
            simular(catalogo, opciones.hoy, opciones.dias)
        elif opciones.modo == "marcar-historial":
            marcar_historial(catalogo, opciones.hoy, opciones.dias)
        else:
            cfg = configuracion()
            with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
                hoy = ahora.date()
                eventos = leer_eventos(cliente[cfg.mongo_db]["eventos_agencia"], hoy, hoy + timedelta(days=1))
            print(json.dumps(selecciones_pendientes(catalogo, ahora, eventos)))


if __name__ == "__main__":
    main()
