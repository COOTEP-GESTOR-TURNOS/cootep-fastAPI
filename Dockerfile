# syntax=docker/dockerfile:1
# Compilación multietapa: las dependencias se instalan en una etapa y solo se copian a la final.

FROM python:3.12.8-slim AS dependencias
WORKDIR /construccion
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/instalado -r requirements.txt

FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=dependencias /instalado /usr/local
COPY app ./app
# Usuario sin privilegios
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
