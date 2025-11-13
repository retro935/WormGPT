# ---- Etapa base ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema para Pillow
RUN apt-get update && apt-get install -y \
    libjpeg-dev zlib1g-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8080

# Comando principal: inicia el keepalive
CMD ["python", "keepalive.py"]
