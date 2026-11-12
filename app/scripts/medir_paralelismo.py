"""Mide la construcción de hechos en un proceso contra varios procesos (sección 10 del informe).

No necesita bases de datos: simula el historial en memoria con una semilla fija, así la medición
es reproducible. Verifica que ambos caminos produzcan exactamente los mismos hechos.

Uso:  python -m app.scripts.medir_paralelismo --dias 120 --procesos 4
"""

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date

from app.services.hechos import construir_hechos
from app.services.simulacion import AGENCIAS, SERVICIOS, a_compacto, simular_fila


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--dias", type=int, default=120)
    argumentos.add_argument("--procesos", type=int, default=4)
    argumentos.add_argument("--semilla", type=int, default=7)
    argumentos.add_argument("--repeticiones", type=int, default=3)
    opciones = argumentos.parse_args()

    inicio = time.perf_counter()
    lotes = []
    for agencia_id in AGENCIAS:
        for servicio_id in SERVICIOS:
            eventos = simular_fila(agencia_id, servicio_id, date(2026, 1, 5), opciones.dias, opciones.semilla)
            lotes.append(((agencia_id, servicio_id), [a_compacto(e) for e in eventos]))
    total = sum(len(eventos) for _, eventos in lotes)
    print(f"Historial simulado: {total:,} eventos en {len(lotes)} filas ({time.perf_counter() - inicio:.1f} s)")
    print(f"CPU disponibles: {os.cpu_count()} · procesos usados: {opciones.procesos}")

    tiempos_secuencial, tiempos_paralelo = [], []
    with ProcessPoolExecutor(max_workers=opciones.procesos) as pool:
        list(pool.map(abs, range(opciones.procesos)))  # calienta los procesos antes de medir
        for _ in range(opciones.repeticiones):
            t = time.perf_counter()
            secuencial = [construir_hechos(lote) for lote in lotes]
            tiempos_secuencial.append(time.perf_counter() - t)

            t = time.perf_counter()
            paralelo = list(pool.map(construir_hechos, lotes))
            tiempos_paralelo.append(time.perf_counter() - t)

            assert secuencial == paralelo, "Los dos caminos deben producir los mismos hechos"

    mejor_secuencial, mejor_paralelo = min(tiempos_secuencial), min(tiempos_paralelo)
    hechos = sum(len(h) for h in secuencial)
    print(f"Hechos construidos: {hechos:,} (idénticos en ambos caminos)")
    print(f"Secuencial: {mejor_secuencial:.2f} s · Paralelo: {mejor_paralelo:.2f} s · "
          f"Aceleración: {mejor_secuencial / mejor_paralelo:.2f}x (mejor de {opciones.repeticiones})")


if __name__ == "__main__":
    main()
