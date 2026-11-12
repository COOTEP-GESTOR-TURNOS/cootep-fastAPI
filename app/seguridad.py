import hmac

from fastapi import Header, HTTPException, status

from app.config import configuracion


async def verificar_token_servicio(x_service_token: str | None = Header(default=None)) -> None:
    """Autenticación entre servicios: solo el backend principal conoce el token (comparación en tiempo constante)."""
    esperado = configuracion().service_token
    if not esperado or not x_service_token or not hmac.compare_digest(esperado.encode(), x_service_token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de servicio inválido")
