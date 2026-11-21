"""Días cívicos de Putumayo publicados para fechas específicas."""

from datetime import date


DIAS_CIVICOS_DEPARTAMENTALES = {
    # Decretos departamentales 003 y 074 de 2026.
    date(2026, 1, 5): "Día cívico por los carnavales de Putumayo",
    date(2026, 1, 6): "Día cívico por los carnavales de Putumayo",
    date(2026, 4, 17): "Día Cívico de la Paz con la Naturaleza en Putumayo",
}

DIAS_CIVICOS_MUNICIPALES = {
    "MOCOA": {
        # Decretos departamentales 286 de 2025 y 239 de 2026.
        date(2025, 10, 3): "Día cívico por el aniversario de Mocoa",
        date(2026, 10, 2): "Día cívico por el aniversario de Mocoa",
    },
}


def festivos_putumayo(fecha: date, departamento: str | None, municipio: str | None) -> tuple[str, ...]:
    """Devuelve las marcas oficiales correspondientes a la ubicación de la agencia."""
    if (departamento or "").strip().upper() != "PUTUMAYO":
        return ()

    departamental = DIAS_CIVICOS_DEPARTAMENTALES.get(fecha)
    municipal = DIAS_CIVICOS_MUNICIPALES.get((municipio or "").strip().upper(), {}).get(fecha)
    return tuple(nombre for nombre in (departamental, municipal) if nombre)
