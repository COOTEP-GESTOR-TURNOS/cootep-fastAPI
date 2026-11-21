"""Recarga completa de los indicadores en una instalación existente.

La columna colaborador_id se crea sola al arrancar la analítica, pero los turnos que ya estaban
cargados en ClickHouse quedan sin responsable hasta esta recarga, que los reconstruye desde MongoDB.
"""

import httpx

from app.config import configuracion


def main() -> None:
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1/etl/",
        timeout=900,
        headers={"X-Service-Token": configuracion().service_token},
    ) as cliente:
        respuesta = cliente.post("ejecutar", params={"completa": "true"})
        respuesta.raise_for_status()
        print(f"Historial reconstruido. Turnos cargados {respuesta.json()['hechosCargados']}.")


if __name__ == "__main__":
    main()
