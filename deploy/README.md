# Operação do Trilha de Fé numa VM

Tudo o que existe **fora do contêiner** e precisa estar no servidor para o portal funcionar com segurança.
Perder o disco da VM não deve levar o procedimento junto: está tudo aqui.

| Arquivo | Vai para | Para quê |
|---|---|---|
| `portal-backup.sh` | `/usr/local/bin/` | Backup diário do banco (conferido) e espelho das devocionais |
| `portal-restore.sh` | `/usr/local/bin/` | Restaura o banco de um backup, gravando como o usuário do app |
| `cron.d/portal-devocional` | `/etc/cron.d/` | Agenda o backup às 03:30 |
| `logrotate.d/portal-backup` | `/etc/logrotate.d/` | Rotação semanal do log do backup |
| `Caddyfile.trilhadefe` | dentro de `/etc/caddy/Caddyfile` | Bloco do site, com limite de corpo por rota |
| `instalar-na-vm.sh` | roda uma vez | Instala os quatro primeiros itens (idempotente) |

## Instalar ou reinstalar

```bash
cd /opt/portal-devocional && sudo ./deploy/instalar-na-vm.sh
```

O script **não mexe no Caddy nem no firewall**. Esses dois passos são manuais, de propósito, porque a VM pode
hospedar outros sites:

1. **Caddy**: copie o bloco de `Caddyfile.trilhadefe` para o `/etc/caddy/Caddyfile`, depois
   `caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy`. O bloco limita o corpo das requisições
   a 1 MB, exceto em `/admin/publicar` (250 MB).
2. **Firewall**: só 22, 80 e 443 precisam estar abertas. O app escuta apenas em `127.0.0.1:8600`.

## Backup

Roda sozinho todo dia. Para rodar na hora: `sudo /usr/local/bin/portal-backup.sh`. Guarda em
`/opt/backups/portal-devocional/`:

- `portal-AAAA-MM-DD-HHMM.db.gz`: o banco, copiado com a API de backup do SQLite, **conferido** (integridade, as 8
  tabelas esperadas e a mesma contagem de membros do banco vivo) e mantido por 14 dias;
- `devocionais-espelho/`: espelho das devocionais, sem os slides (que se regeram) e **sem apagar** o que for removido
  na origem.

Se qualquer conferência falhar, o script termina com erro e escreve `FALHA` em `/var/log/portal-backup.log`.

**Esses arquivos ficam na mesma VM.** Para proteger de perda do disco, copie-os para fora, por exemplo, de outro
computador: `rsync -avz usuario@servidor:/opt/backups/portal-devocional/ ~/Backups/trilhadefe/`. Os backups
contêm hashes de senha e as anotações dos membros em texto simples: guarde a cópia externa num lugar cifrado.

## Restaurar

```bash
sudo /usr/local/bin/portal-restore.sh /opt/backups/portal-devocional/portal-AAAA-MM-DD-HHMM.db.gz
```

O script confere o backup **antes** de tocar em qualquer coisa (recusa arquivo vazio ou corrompido), guarda o
banco atual ao lado (`portal.db.antes-da-restauracao-...`), restaura **como o usuário do app** e reinicia o app.
Gravar o banco como root deixaria o app sem poder escrever nele (erro `attempt to write a readonly database`),
e esse caminho já foi ensaiado e corrigido.

**Ensaio sem risco**, num volume de teste, sem tocar no app em produção:

```bash
docker volume create portal_ensaio_db
ENSAIO=1 VOLUME=portal_ensaio_db sudo -E /usr/local/bin/portal-restore.sh /opt/backups/portal-devocional/portal-AAAA-MM-DD-HHMM.db.gz
docker volume rm portal_ensaio_db
```

## Atualizar o app

```bash
cd /opt/portal-devocional && git pull && docker compose up -d --build
```

As versões das bibliotecas estão fixadas em `requirements.lock` e a imagem base, pelo resumo (digest) no `Dockerfile`:
o mesmo código gera sempre a mesma imagem. Para atualizar de propósito, siga os comentários no topo de
`requirements.lock`.

## O contêiner

Sistema de arquivos somente leitura (o app grava só nos volumes e em `/tmp`), sem capabilities, sem ganhar
privilégio, 768 MB de memória, 256 processos, logs com rotação (3 × 10 MB) e verificação de saúde a cada 30 s
(`docker compose ps` mostra `healthy`).
