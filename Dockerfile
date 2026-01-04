FROM python:3.11-slim

# Instalar dependencias para Chromium
# Se instalan chromium y chromium-driver para que Selenium pueda controlarlos.
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar archivos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Exponer puerto para la web
EXPOSE 5000

CMD ["python", "app.py"]
