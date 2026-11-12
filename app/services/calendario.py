"""Dimensiones de tiempo del modelo analítico: franja de 30 minutos y tipo de día."""

from datetime import date, datetime, timedelta

# Agregue aquí los festivos de Colombia del periodo simulado (AAAA-MM-DD).
FESTIVOS: frozenset[date] = frozenset()


def franja_de(momento: datetime) -> str:
    """Inicio de la franja de 30 minutos, por ejemplo 09:30."""
    return f"{momento.hour:02d}:{'00' if momento.minute < 30 else '30'}"


def tipo_dia(fecha: date) -> str:
    """QUINCENA cerca de los días de pago (1, 2, 15, 16 y fin de mes), FESTIVO o NORMAL."""
    if fecha in FESTIVOS:
        return "FESTIVO"
    ultimo_del_mes = (fecha + timedelta(days=1)).month != fecha.month
    if fecha.day in (1, 2, 15, 16) or fecha.day >= 30 or ultimo_del_mes:
        return "QUINCENA"
    return "NORMAL"
