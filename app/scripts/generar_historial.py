"""Carga en MongoDB un historial simulado de turnos (datos ficticios).

Uso:  python -m app.scripts.generar_historial --dias 60 --semilla 7
Después ejecute la carga a ClickHouse: POST /api/v1/etl/ejecutar?completa=true
"""

import argparse
from datetime import date, timedelta

from pymongo import MongoClient
from pymongo.errors import BulkWriteError

from app.config import configuracion
from app.services.simulacion import AGENCIAS, SERVICIOS, simular_fila


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--dias", type=int, default=60)
    argumentos.add_argument("--semilla", type=int, default=7)
    opciones = argumentos.parse_args()

    cfg = configuracion()
    coleccion = MongoClient(cfg.mongo_url)[cfg.mongo_db]["eventos_turno"]
    inicio = date.today() - timedelta(days=opciones.dias)
    total = 0
    for agencia_id in AGENCIAS:
        for servicio_id in SERVICIOS:
            eventos = simular_fila(agencia_id, servicio_id, inicio, opciones.dias, opciones.semilla)
            try:
                coleccion.insert_many(eventos, ordered=False)
            except BulkWriteError as error:
                # Volver a correr el script con la misma semilla no duplica: eventoId es único.
                if any(e["code"] != 11000 for e in error.details.get("writeErrors", [])):
                    raise
            total += len(eventos)
            print(f"{agencia_id[-1]}/{SERVICIOS[servicio_id][0]}: {len(eventos)} eventos")
    print(f"Listo: {total} eventos desde {inicio}")


if __name__ == "__main__":
    main()
