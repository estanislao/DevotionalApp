#!/bin/bash
# Instala (ou reinstala, sem efeito colateral) a operação do Trilha de Fé numa VM: scripts de backup e
# restauração, agendamento diário e rotação do log. Rode como root, a partir da pasta do repositório.
# NÃO mexe no Caddy nem no firewall: veja deploy/README.md para esses dois passos.
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = "0" ] || { echo "rode como root" >&2; exit 1; }

install -m 0750 -o root -g root portal-backup.sh  /usr/local/bin/portal-backup.sh
install -m 0750 -o root -g root portal-restore.sh /usr/local/bin/portal-restore.sh
install -m 0644 -o root -g root cron.d/portal-devocional  /etc/cron.d/portal-devocional
install -m 0644 -o root -g root logrotate.d/portal-backup /etc/logrotate.d/portal-backup
mkdir -p /opt/backups/portal-devocional && chmod 700 /opt/backups /opt/backups/portal-devocional

logrotate -d /etc/logrotate.d/portal-backup >/dev/null 2>&1 && echo "logrotate: configuração válida"
bash -n /usr/local/bin/portal-backup.sh && bash -n /usr/local/bin/portal-restore.sh && echo "scripts: sintaxe ok"
echo "instalado. Para testar já: /usr/local/bin/portal-backup.sh"
