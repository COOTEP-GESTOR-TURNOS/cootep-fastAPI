"""Simulación y publicación interna del comando local demo.ps1."""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
from pymongo import MongoClient, UpdateOne

from app.config import ZONA_AGENCIAS, configuracion
from app.services.simulacion_demo import (
    DIAS_PRONOSTICO,
    CatalogoDemo,
    fechas,
    programar_eventos,
    selecciones_pendientes,
    simular_jornada,
)


def publicar(operacion: str, datos: dict | None = None) -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=600) as cliente:
        respuesta = cliente.post(
            f"/api/v1/etl/demo/{operacion}",
            json=datos,
            headers={"X-Service-Token": configuracion().service_token},
        )
        if respuesta.is_error:
            raise SystemExit(f"Analítica no pudo {operacion} la demostración (HTTP {respuesta.status_code}): {respuesta.text}")
        if respuesta.content:
            print(respuesta.text)


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
        coleccion = cliente[cfg.mongo_db]["demo_cobertura_historial"]
        for agencia in catalogo.agencias:
            coleccion.update_one({"_id": agencia.id}, {"$addToSet": {"intervalos": intervalo}}, upsert=True)


def simular(catalogo: CatalogoDemo, hoy: date, dias: int) -> None:
    desde = hoy - timedelta(days=dias)
    hasta_eventos = hoy + timedelta(days=DIAS_PRONOSTICO)
    cfg = configuracion()
    with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
        db = cliente[cfg.mongo_db]
        coleccion = db["eventos_agencia_demo"]
        operaciones = []
        for evento in programar_eventos(catalogo, desde, hasta_eventos):
            operaciones.append(UpdateOne(
                {"agenciaId": evento["agenciaId"], "$or": [
                    {"id": evento["id"]}, {"fecha": evento["fecha"], "tipo": evento["tipo"]},
                ]},
                {"$setOnInsert": evento}, upsert=True,
            ))
        if operaciones:
            coleccion.bulk_write(operaciones, ordered=False)
        calendario = leer_eventos(coleccion, desde, hasta_eventos)
        cobertura = leer_cobertura(db["demo_cobertura_historial"], [agencia.id for agencia in catalogo.agencias])

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modos = parser.add_subparsers(dest="modo", required=True)
    modos.add_parser("fecha")
    modos.add_parser("preparar")
    modos.add_parser("pendientes")
    modos_con_rango = ("historial", "marcar-historial", "publicar")
    for modo in modos_con_rango:
        con_rango = modos.add_parser(modo)
        con_rango.add_argument("--hoy", type=date.fromisoformat, required=True)
        con_rango.add_argument("--dias", type=int, required=True)
    opciones = parser.parse_args()
    ahora = datetime.now(ZONA_AGENCIAS)

    if opciones.modo in modos_con_rango and opciones.dias <= 0:
        parser.error("El historial requiere al menos un día")
    if opciones.modo == "fecha":
        print(ahora.date().isoformat())
    elif opciones.modo == "preparar":
        publicar("preparar")
    elif opciones.modo == "publicar":
        desde = opciones.hoy - timedelta(days=opciones.dias)
        publicar("publicar", {"desde": desde.isoformat(), "hasta": opciones.hoy.isoformat()})
    else:
        catalogo = CatalogoDemo.model_validate_json(sys.stdin.buffer.read())
        if opciones.modo == "historial":
            simular(catalogo, opciones.hoy, opciones.dias)
        elif opciones.modo == "marcar-historial":
            marcar_historial(catalogo, opciones.hoy, opciones.dias)
        else:
            cfg = configuracion()
            with MongoClient(cfg.mongo_url, serverSelectionTimeoutMS=5000) as cliente:
                hoy = ahora.date()
                eventos = leer_eventos(cliente[cfg.mongo_db]["eventos_agencia_demo"], hoy, hoy + timedelta(days=1))
            print(json.dumps(selecciones_pendientes(catalogo, ahora, eventos)))


if __name__ == "__main__":
    main()
