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
| `POST /api/v1/analitica/tablero` | Resumen mensual y calendario, con filtros por agencia, servicio y colaborador |
| `GET /api/v1/analitica/agencias/{agenciaId}/demanda?mes=AAAA-MM` | Calendario mensual con conteos históricos y pronósticos |
| `POST /api/v1/analitica/agencias/{agenciaId}/eventos` | Registrar un evento de agencia |
| `PATCH`, `DELETE /api/v1/analitica/agencias/{agenciaId}/eventos/{eventoId}` | Editar o eliminar un evento |
| `POST /api/v1/etl/ejecutar?completa=false&reconstruir=false` | Carga los eventos de MongoDB a ClickHouse; `reconstruir` vacía antes `hechos_turno` |
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
```

## Calendario de demanda

El tablero muestra turnos solicitados por agencia y día de Bogotá. Los días pasados presentan el
conteo histórico, incluidos los turnos que seguían abiertos. El pronóstico cubre 90 días desde hoy
y requiere 60 días de cobertura y al menos 20 días con solicitudes. Los niveles se calculan con los
percentiles 33 y 67 de los volúmenes positivos de cada agencia.

Los vencimientos de crédito se marcan el 5 y el 10 de cada mes. El modelo (regresión de Poisson,
`app/services/modelo_demanda.py`) aprende solo de los días hábiles con estas variables: día de la
semana, cercanía al 5 y al 10, y los pagos de subsidio y de nómina registrados como eventos de la
agencia. No tiene una variable por día del mes: con pocos meses de historia aprendería ruido.
`evaluar` mide el error del pronóstico en los últimos días hábiles contra el promedio de cada día de
la semana; con los datos del generador el modelo se equivoca casi lo mismo que un pronóstico perfecto.

Los filtros por agencia y servicio se aplican al calendario. El colaborador filtra las métricas de
atención según el responsable de cada turno. Los días no hábiles conservan el histórico y sus
marcas de calendario. El pronóstico general suma únicamente las agencias hábiles del ámbito.

El calendario regional incluye los días cívicos de Putumayo del 5 y 6 de enero de 2026 y el 17 de
abril de 2026. Mocoa tiene además el 3 de octubre de 2025 y el 2 de octubre de 2026. Las fechas se
aplican según la sede de la agencia y según el año de cada declaratoria. Para añadir una fecha
oficial nueva, actualizar `app/services/calendario_putumayo.py` con su fecha y ámbito, y conservar la
referencia al acto. El calendario permite registrar otras fechas de interés como eventos de agencia.
[Decreto 003 de 2026](https://gacetaputumayo.gov.co/download?codigo=5734) · [Día cívico del 17 de abril de 2026](https://miputumayo.com.co/2026/04/16/putumayo-declara-dia-civico-este-17-de-abril-por-la-paz-con-la-naturaleza/) · [Día cívico del 3 de octubre de 2025](https://miputumayo.com.co/2025/10/01/alcaldia-de-mocoa-declara-dia-civico-por-aniversario-462-de-la-ciudad/) · [Decreto 239 de 2026](https://gacetaputumayo.gov.co/download?codigo=6046&descargar=1)

### Demostración y mantenimiento

Los datos de demostración se guardan en las mismas colecciones que los reales (`eventos_turno`,
`eventos_agencia`) con la marca `origen: "SIMULADO"`, y pasan por el mismo ETL. El calendario y el
tablero dicen `fuente: SIMULADA` cuando la agencia tiene datos marcados. El comando
`scripts/demo.ps1` del repositorio de infraestructura los genera y los borra (`limpiar`); el
generador (`app.scripts.generar_demo`) solo corre con `ENTORNO=desarrollo`.

Al arrancar, el servicio crea los índices de MongoDB y la columna `colaborador_id` de ClickHouse
(los volúmenes ya inicializados no vuelven a correr los scripts de `infra/init`) y hace una carga
incremental a ClickHouse cada `ETL_INTERVALO_SEG` segundos (60 por defecto; 0 la apaga).
