# analitica · COOTEP Turno

Servicio de analítica en **Python 3.12 y FastAPI, arquitectura MVC**. Servicio interno: no publica
puertos y solo lo llama el backend principal con la cabecera `X-Service-Token`.

| Capa | Carpeta | Contenido |
| --- | --- | --- |
| Controladores | `app/routers` | Recomendaciones, estimaciones, indicadores, carga (ETL), salud |
| Servicios | `app/services` | `RecomendacionService`, `EstimacionService`, `EtlService`, `hechos.py`, `simulacion.py` |
| Modelos | `app/schemas.py` | Contratos Pydantic (JSON en camelCase) |
| Repositorios | `app/repositories` | ClickHouse por HTTP (httpx) y MongoDB (Motor), ambos asíncronos |

## API

| Método y ruta | Uso |
| --- | --- |
| `GET /api/v1/recomendaciones?agenciaId&servicioId&fecha` | Las 3 franjas con menor espera mediana |
| `GET /api/v1/estimaciones/parametros?agenciaId&servicioId&franja` | t̄ y p de la franja (los usa el backend principal) |
| `POST /api/v1/estimaciones` | Espera estimada completa |
| `GET /api/v1/indicadores?agenciaId&desde&hasta` | Atendidos, cancelados, ausentes y promedios |
| `POST /api/v1/etl/ejecutar?completa=false` | Carga los eventos de MongoDB a ClickHouse |
| `GET /salud` | Healthcheck (sin token) |

Contrato OpenAPI en `/openapi.json`. La recomendación es estadística (mediana por franja, día de
la semana y tipo de día); no usa machine learning.

## Concurrencia y paralelismo (sección 6 del informe)

- **E/S:** MongoDB (Motor) y ClickHouse (httpx) son asíncronos; no bloquean el bucle de eventos.
- **Cálculo:** la construcción de hechos corre en un `ProcessPoolExecutor` (una tarea por fila),
  lanzado con `run_in_executor`. El pool se crea al iniciar y se cierra al apagar (`app/main.py`).
- **Medición reproducible**, sin bases de datos:

```bash
python -m app.scripts.medir_paralelismo --dias 120 --procesos 4
```

## Ejecutar y probar

```bash
python -m venv .venv && source .venv/Scripts/activate   # en Linux o macOS: .venv/bin/activate
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload --port 8000
python -m app.scripts.generar_historial --dias 60          # historial simulado en MongoDB
```
