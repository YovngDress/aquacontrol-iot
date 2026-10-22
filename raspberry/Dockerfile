# Solo para pruebas locales con docker compose (sin Raspberry Pi física).
# En la Raspberry Pi real se corre directo con Python + systemd (ver README.md).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plc_simulado.py gateway.py ./
CMD ["python", "gateway.py"]
