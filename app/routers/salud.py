from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["operación"])


@router.get("/salud")
async def salud() -> dict:
    """Healthcheck de Docker. No consulta los motores para no reiniciar el contenedor si uno falla."""
    return {"estado": "ok", "servicio": "analitica", "hora": datetime.now(timezone.utc).isoformat()}
