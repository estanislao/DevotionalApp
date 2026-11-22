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
| `instalar-na-vm.sh` | roda uma vez | Instala os quatro primeiros itens e cria a chave de cifra se faltar (idempotente) |
| `copiar-backup-para-fora.sh` | roda em OUTRO computador | Traz os backups cifrados para fora da VM |
| `exemplo-launchd-macos.plist` | opcional, no Mac | Agenda a cópia diária (não é instalado sozinho) |

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

Roda sozinho todo dia (03:30). Para rodar na hora: `sudo /usr/local/bin/portal-backup.sh`. Guarda em
`/opt/backups/portal-devocional/`:

- `portal-AAAA-MM-DD-HHMM.db.gz.enc`: o banco, copiado com a API de backup do SQLite, **conferido** (integridade, as 8
  tabelas esperadas e a mesma contagem de membros do banco vivo), **cifrado** (AES-256, PBKDF2 de 600 mil
  iterações) e mantido por 14 dias. A cifra é conferida na hora: o script decifra o arquivo de volta e compara
  byte a byte com o original antes de apagar a versão sem cifra;
- `devocionais-espelho/`: espelho das devocionais, sem os slides (que se regeram) e **sem apagar** o que for removido
  na origem. É o conteúdo da própria família, por isso não é cifrado.

Se qualquer conferência falhar, o script termina com erro e escreve `FALHA` em `/var/log/portal-backup.log`.

### A chave de cifra (a única coisa que você precisa guardar à parte)

O instalador cria `/root/.portal-backup.key` (modo 600) **uma vez** e nunca a troca. **Sem ela os backups não abrem,
e se a VM se perder a chave se perde junto.** Guarde uma cópia fora da VM, num gerenciador de senhas:

```bash
ssh diana-server cat /root/.portal-backup.key        # copie o resultado para o gerenciador de senhas
```

### Cópia para fora da VM

Num computador seu (não na VM):

```bash
./deploy/copiar-backup-para-fora.sh diana-server ~/Backups/trilhadefe
```

Traz só os bancos **cifrados** e o espelho das devocionais; um backup em texto simples nunca é copiado, e a chave
nunca passa por esse caminho. Os arquivos que a VM apagar pela retenção continuam na sua cópia. Para agendar no
macOS, veja `exemplo-launchd-macos.plist` (não é instalado sozinho). Para abrir um backup cifrado fora da VM:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:/caminho/da/chave -in portal-AAAA-MM-DD-HHMM.db.gz.enc | gunzip > portal.db
```

## Restaurar

```bash
sudo /usr/local/bin/portal-restore.sh /opt/backups/portal-devocional/portal-AAAA-MM-DD-HHMM.db.gz.enc
```

O script abre o backup (com a chave em `/root/.portal-backup.key`, ou a de `CHAVE=`) e o confere **antes** de tocar
em qualquer coisa. Recusa arquivo vazio, corrompido, **adulterado** ou aberto com a chave errada, e nesses casos
não restaura nada. Depois guarda o banco atual ao lado (`portal.db.antes-da-restauracao-...`), restaura **como o
usuário do app** e reinicia o app. Gravar o banco como root deixaria o app sem poder escrever nele (erro
`attempt to write a readonly database`); esse caminho já foi ensaiado e corrigido.

**Ensaio sem risco**, num volume de teste, sem tocar no app em produção:

```bash
docker volume create portal_ensaio_db
ENSAIO=1 VOLUME=portal_ensaio_db sudo -E /usr/local/bin/portal-restore.sh /opt/backups/portal-devocional/portal-AAAA-MM-DD-HHMM.db.gz.enc
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
