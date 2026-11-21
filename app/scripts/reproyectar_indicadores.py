"""Reconstruye las métricas por servicio y responsable en una instalación existente."""

import httpx

from app.config import configuracion


def main() -> None:
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1/etl/",
        timeout=600,
        headers={"X-Service-Token": configuracion().service_token},
    ) as cliente:
        operativo = cliente.post("ejecutar", params={"completa": "true"})
        operativo.raise_for_status()
        print(f"Historial operativo reconstruido. Turnos cargados {operativo.json()['hechosCargados']}.")

        demo = cliente.post("demo/reconstruir")
        if demo.status_code == 409:
            print(demo.json()["detail"])
            return
        demo.raise_for_status()
        print(f"Historial de demostración publicado. Corte {demo.json()['actualizadoEn']}.")


if __name__ == "__main__":
    main()
