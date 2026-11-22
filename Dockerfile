# Imagem base fixada pelo resumo (digest): o build de amanhã usa exatamente a mesma base de hoje.
# Para atualizar de propósito: docker pull python:3.12-slim e troque o digest abaixo.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
