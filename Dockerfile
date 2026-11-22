FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# devocionais e o banco SQLite ficam fora da imagem, em volumes (veja docker-compose.yml)
RUN useradd --system --uid 1000 --create-home portal \
    && mkdir -p /srv/devocionais /srv/app/data \
    && chown -R portal:portal /srv

USER portal

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
