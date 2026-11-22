#!/bin/bash
# Backup do Trilha de Fé: banco (consistente e conferido, com retenção) e espelho das devocionais.
# Instalado em /usr/local/bin pelo deploy/instalar-na-vm.sh e executado todo dia pelo cron.d.
# Falha com código diferente de zero e uma linha "FALHA" no log se qualquer conferência não passar.
set -euo pipefail
umask 077

APP=${APP:-/opt/portal-devocional}
DEST=${DEST:-/opt/backups/portal-devocional}
RETENCAO_DIAS=${RETENCAO_DIAS:-14}
STAMP=$(date +%F-%H%M)
ARQ="$DEST/portal-$STAMP.db"

falhou() { echo "$(date -Is) FALHA no backup: $1" >&2; rm -f "$ARQ" "$ARQ.gz"; exit 1; }
trap 'falhou "erro inesperado na linha $LINENO"' ERR

mkdir -p "$DEST/devocionais-espelho"
chmod 700 "$DEST"
cd "$APP"

# 1) Cópia consistente pela API de backup do SQLite (copiar o arquivo "no susto" pode pegar uma escrita pela metade).
#    O arquivo sai pela saída padrão do contêiner direto para o host: o /tmp do contêiner é em memória e o
#    "docker cp" não funciona com ele.
docker compose exec -T app python3 - > "$ARQ" <<'PY'
import os, sqlite3, sys
temporario = "/tmp/backup-%d.db" % os.getpid()
origem = sqlite3.connect("/srv/app/data/portal.db")
destino = sqlite3.connect(temporario)
origem.backup(destino)
destino.close(); origem.close()
with open(temporario, "rb") as f:
    sys.stdout.buffer.write(f.read())
os.remove(temporario)
PY
[ -s "$ARQ" ] || falhou "o arquivo de backup saiu vazio"

# 2) Conferência de verdade: integridade, todas as tabelas esperadas e a mesma contagem de membros do banco vivo.
#    ("integrity_check" sozinho devolve "ok" até para um arquivo vazio, por isso só ele não basta.)
MEMBROS_VIVO=$(docker compose exec -T app python3 -c "import sqlite3; print(sqlite3.connect('/srv/app/data/portal.db').execute('select count(*) from members').fetchone()[0])" | tr -d '\r')
python3 - "$ARQ" "$MEMBROS_VIVO" <<'PY' || falhou "a conferência do backup não passou"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
assert con.execute("pragma integrity_check").fetchone()[0] == "ok", "integrity_check falhou"
tabelas = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
faltam = {"members", "collections", "progress", "quiz_answers", "notes", "shares", "pin_resets", "eventos"} - tabelas
assert not faltam, f"faltam tabelas: {sorted(faltam)}"
membros = con.execute("select count(*) from members").fetchone()[0]
assert membros >= 1, "o backup não tem nenhum membro"
assert membros == int(sys.argv[2]), f"membros no backup ({membros}) diferem do banco vivo ({sys.argv[2]})"
PY
gzip -f "$ARQ"

# 3) Espelho das devocionais, SEM apagar o que for removido na origem (protege contra exclusão acidental).
#    Os slides em imagem são regerados sozinhos a partir do PDF, então ficam de fora.
rsync -a --exclude slides --exclude '.publicando-*' "$APP/devocionais/" "$DEST/devocionais-espelho/"

# 4) Retenção do banco.
find "$DEST" -maxdepth 1 -name 'portal-*.db.gz' -mtime +"$RETENCAO_DIAS" -delete

echo "$(date -Is) backup ok: portal-$STAMP.db.gz ($(du -h "$ARQ.gz" | cut -f1)), $MEMBROS_VIVO membro(s) conferidos, espelho de $(du -sh "$DEST/devocionais-espelho" | cut -f1)"
