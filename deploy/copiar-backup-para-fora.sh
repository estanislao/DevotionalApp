#!/bin/bash
# Copia os backups da VM para ESTE computador (rode num computador seu, não na VM).
#   Uso:  ./copiar-backup-para-fora.sh [host-ssh] [pasta-de-destino]
#   Ex.:  ./copiar-backup-para-fora.sh diana-server ~/Backups/trilhadefe
#
# Só trazem-se os bancos CIFRADOS (portal-*.db.gz.enc) e o espelho das devocionais (conteúdo da própria família).
# Um backup em texto simples NUNCA é copiado, e a chave de cifra NUNCA passa por aqui: guarde-a à parte
# (gerenciador de senhas). Sem a chave, os .enc não abrem; sem os .enc, a chave não serve de nada.
# Os arquivos antigos que a VM apagar (retenção de 14 dias) continuam aqui, de propósito.
set -euo pipefail
HOST=${1:-diana-server}
DEST=${2:-$HOME/Backups/trilhadefe}
ORIGEM=/opt/backups/portal-devocional/

mkdir -p "$DEST"; chmod 700 "$DEST"
rsync -az --partial \
  --include='portal-*.db.gz.enc' --include='devocionais-espelho/' --include='devocionais-espelho/**' --exclude='*' \
  "$HOST:$ORIGEM" "$DEST/"

N=$(ls -1 "$DEST"/portal-*.db.gz.enc 2>/dev/null | wc -l | tr -d ' ')
ULTIMO=$(ls -1t "$DEST"/portal-*.db.gz.enc 2>/dev/null | head -1 || true)
[ -n "$ULTIMO" ] || { echo "nenhum backup cifrado veio da VM (a chave já foi criada e o backup já rodou?)" >&2; exit 1; }
echo "ok: $N backup(s) cifrado(s) em $DEST; o mais recente é $(basename "$ULTIMO") ($(du -h "$ULTIMO" | cut -f1))"
echo "espelho das devocionais: $(du -sh "$DEST/devocionais-espelho" 2>/dev/null | cut -f1)"

# Para abrir um backup cifrado aqui (precisa da chave, guardada à parte):
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:/caminho/da/chave -in ARQUIVO.db.gz.enc | gunzip > portal.db
