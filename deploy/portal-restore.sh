#!/bin/bash
# Restaura o banco do Trilha de Fé a partir de um backup .db.gz.
#   Uso:      portal-restore.sh /opt/backups/portal-devocional/portal-AAAA-MM-DD-HHMM.db.gz
#   Ensaio:   ENSAIO=1 VOLUME=um_volume_de_teste portal-restore.sh ARQUIVO.db.gz   (não mexe no app em produção)
#
# O arquivo é gravado COMO O USUÁRIO DO APP (uid 1000). Gravar como root deixaria o banco somente leitura
# para o aplicativo, e o erro só apareceria na primeira partilha ou resposta de quiz.
set -euo pipefail

ARQ=${1:?uso: portal-restore.sh ARQUIVO.db.gz}
APP=${APP:-/opt/portal-devocional}
VOLUME=${VOLUME:-portal-devocional_db_data}
IMAGEM=${IMAGEM:-portal-devocional-app}
ENSAIO=${ENSAIO:-0}

[ -f "$ARQ" ] || { echo "arquivo não encontrado: $ARQ" >&2; exit 1; }
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
gunzip -c "$ARQ" > "$TMP"

# 1) Confere o backup ANTES de tocar em qualquer coisa.
python3 - "$TMP" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
assert con.execute("pragma integrity_check").fetchone()[0] == "ok", "backup corrompido"
tabelas = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
assert {"members", "progress", "quiz_answers"} <= tabelas, "backup sem as tabelas principais"
assert con.execute("select count(*) from members").fetchone()[0] >= 1, "backup sem membros"
print("backup conferido")
PY
chmod 644 "$TMP"    # o contêiner (uid 1000) precisa conseguir ler o arquivo temporário

cd "$APP"
[ "$ENSAIO" = "1" ] || docker compose stop app

# 2) Guarda o banco atual ao lado e grava o restaurado, como o usuário do app (o padrão da imagem).
docker run --rm --entrypoint sh -v "$VOLUME":/dados -v "$TMP":/novo.db:ro "$IMAGEM" -c '
  [ -f /dados/portal.db ] && cp /dados/portal.db "/dados/portal.db.antes-da-restauracao-$(date +%s)" || true
  cp /novo.db /dados/portal.db
  ls -l /dados/portal.db'

[ "$ENSAIO" = "1" ] || { docker compose start app; echo "app reiniciado; confira https://SEU-DOMINIO/login"; }
echo "restauração concluída"
