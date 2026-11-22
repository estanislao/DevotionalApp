# Portal de Devocionais

App único (FastAPI + SQLite + HTML server-side) para publicar devocionais em família. Você publica, a família consome pelo celular como um PWA (instalável, sem precisar de app store). Cada pessoa cria a própria conta (e-mail + senha), você aprova em `/admin/membros`, e as devocionais ficam organizadas em coleções que só o admin cria.

## O que tem aqui

```
app/                 código do servidor (FastAPI)
  main.py            rotas
  db.py              SQLite — membros, progresso, respostas do quiz, anotações
  content.py         lê as devocionais da pasta devocionais/
  auth.py            login por e-mail + senha (PBKDF2), cookie de sessão, bloqueio por tentativas
  templates/         telas: login, cadastro, esqueci/redefinir senha, início (abas Coleções e Últimas),
                     coleção, trilha, ouvir, ler, slides, quiz, praticar, admin, membros, coleções, erro
  static/            CSS, manifest.json e ícones do PWA
devocionais/         uma pasta por devocional (seus arquivos ficam aqui)
Dockerfile
docker-compose.yml   app + Caddy (HTTPS automático)
Caddyfile            troque pelo seu domínio
.env.example         copie para .env e preencha
```

## Estrutura de uma devocional

Cada publicação cria `devocionais/<data>-<titulo-slug>/` com:

- `meta.json` — título, data, versículo, `colecao_id` (opcional), nomes dos arquivos de áudio/pdf
- `texto.md` — o texto da leitura
- `audio.mp3` (ou .m4a) — o áudio semelhante a podcast
- `apresentacao.pdf` — os slides
- `quiz.json` — lista de perguntas: `{"id","pergunta","opcoes","correta","explicacao"}`
- `licoes.md` — lista de lições práticas (uma por linha com `-`)

Você nunca mexe nisso na mão — o formulário em `/admin` cria tudo isso pra você ao publicar.

## Subir na VM da DigitalOcean

Pré-requisito: Docker e Docker Compose instalados na VM, e um domínio (ou subdomínio) apontando o DNS (registro A) para o IP da VM — necessário para o Caddy emitir HTTPS sozinho.

```bash
# 1. Envie esta pasta para a VM (rsync, scp ou git clone do seu repositório)
rsync -avz --exclude 'app/data' ./ usuario@seu-ip:/opt/portal-devocional/

# 2. Na VM:
cd /opt/portal-devocional
cp .env.example .env
nano .env                    # preencha SESSION_SECRET (openssl rand -hex 32), ADMIN_EMAIL e ADMIN_PASSWORD
# Caddyfile já está com trilhadefe.app; edite só se o domínio mudar

# 3. O app roda como usuário comum (uid 1000) e precisa gravar em devocionais/
sudo chown -R 1000:1000 devocionais

# 4. Suba
docker compose --profile standalone up -d --build

# 5. Acompanhe o primeiro boot (emissão do certificado HTTPS pode levar uns segundos)
docker compose --profile standalone logs -f
```

Depois disso, `https://seu-dominio` já está no ar.

### VM que já tem Caddy (ou outro proxy) nas portas 80/443

Não suba o serviço `caddy` do compose (basta omitir `--profile standalone`). O app fica escutando só em `127.0.0.1:8600` e o proxy existente aponta para ele. No Caddy nativo, acrescente ao `/etc/caddy/Caddyfile`:

```text
trilhadefe.app {
    encode zstd gzip
    request_body {
        max_size 250MB
    }
    reverse_proxy 127.0.0.1:8600
}

www.trilhadefe.app {
    redir https://trilhadefe.app{uri} permanent
}
```

e recarregue com `caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy`. Para subir o app: `docker compose up -d --build`. Para trocar a porta local, defina `APP_PORT` no `.env`.

### Publicando uma devocional nova

Entre como admin e acesse `/admin`. Escolha a coleção (opcional; crie antes em `/admin/colecoes`), preencha o formulário e envie os 5 arquivos — a devocional aparece na hora para todo mundo.

O formulário valida antes de gravar: data no formato AAAA-MM-DD, áudio em .mp3/.m4a/.aac/.ogg/.wav, apresentação em .pdf, `quiz.json` no formato esperado (toda pergunta com `id`, `pergunta`, `opcoes`, `correta` presente nas opções). Se algo estiver errado, nada é gravado e a tela mostra o motivo. Publicar com o mesmo título e data de uma devocional existente é recusado (apague a pasta antiga em `devocionais/` se quiser substituir).

### Backup

Dois lugares para copiar periodicamente:
- a pasta `devocionais/` (seus arquivos originais)
- o volume `db_data` (progresso e respostas): `docker compose exec app cat /srv/app/data/portal.db > backup-$(date +%F).db`

### Contas, aprovação e senhas

- **Cadastro**: qualquer pessoa com o link cria conta em `/cadastro` (nome, e-mail, senha). A conta nasce *pendente* e não consegue entrar até você aprovar.
- **Aprovar, recusar, excluir, redefinir senha**: tudo em `/admin/membros`. O admin não consegue excluir a própria conta. Se o e-mail estiver configurado, você recebe um aviso a cada inscrição nova e a pessoa recebe um aviso ao ser aprovada.
- **Esqueci minha senha**: com `BREVO_API_KEY` e `MAIL_FROM` no `.env`, a tela de login mostra o link. A pessoa digita o e-mail e recebe um link de 30 minutos (um a cada 5 minutos) para criar senha nova. A resposta é a mesma exista a conta ou não.
- **Coleções**: criadas e editadas só pelo admin em `/admin/colecoes` (nome e descrição). Não dá para excluir coleção que ainda tem devocionais. No início, a aba Coleções lista todas e a aba Últimas mostra as 10 mais recentes.
- **Força bruta**: 5 senhas erradas seguidas bloqueiam o e-mail por 15 minutos (contador em memória, zera ao reiniciar).

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes usam um banco e uma pasta de devocionais temporários; não tocam em dados reais.

## Rodando localmente (para testar antes de subir)

```bash
pip install -r requirements.txt
SESSION_SECRET=segredo-local-de-teste COOKIE_SECURE=0 ADMIN_EMAIL=admin@exemplo.com ADMIN_PASSWORD=senha-admin \
  uvicorn app.main:app --reload --port 8000
```

Abra `http://localhost:8000`, entre como admin e publique uma devocional em `/admin` para testar a trilha. O conteúdo de `devocionais/` não vai para o git.

## Decisões tomadas (e por quê)

- **SQLite, não Postgres**: um arquivo só, zero servidor extra para administrar — adequado para 4 pessoas.
- **Slides como imagens, não PDF embutido**: ao publicar, cada página do PDF vira um JPEG de 1600 px (`pypdfium2`), mostrado em carrossel com toque para ampliar; o PDF embutido ficava ilegível no celular e no iPhone mostrava só a primeira página. Se a conversão falhar, a tela volta ao PDF embutido. Envie sempre PDF: exporte o PowerPoint ou Google Slides antes.
- **Três atividades, não cinco passos**: o progresso conta Conteúdo (basta um entre Slides, Ouvir ou Ler, nessa ordem), Quiz e Praticar. Slides e Ler contam ao abrir a tela; Ouvir conta quando o áudio termina (ou ao tocar em "Ir para o Quiz"); Quiz ao responder todas as perguntas; Praticar ao clicar "Concluir". Nada disso exige um botão de "marcar como feito".
- **Palavras da família**: qualquer membro deixa uma mensagem curta de ânimo (até 280 caracteres, 3 por dia) que aparece para todos em carrossel no início. Quem escreveu ou o admin pode apagar.
- **Sem LGPD/compliance formal**: como você pediu, não tem nada de consentimento, retenção de dados ou exportação — é uma família, 4 contas, dados ficam só no seu volume Docker.
- **Segurança proporcional**: bloqueio por 15 minutos após 5 senhas erradas (contador em memória; reinicia com o container). O app recusa iniciar sem `SESSION_SECRET`, o cookie de sessão só trafega em HTTPS, a resposta certa do quiz é conferida no servidor (nunca vem do formulário) e só os arquivos declarados no `meta.json` são servidos pela rota de mídia. Erros (404, 403, formulário incompleto) mostram uma página amigável em vez de JSON.
- **Login por e-mail + senha com cadastro aberto e aprovação manual** (substituiu o PIN com membros fixos da primeira versão): quem entra escolhe o próprio nome; o admin é o filtro. Senhas com PBKDF2 da biblioteca padrão. E-mails pela API HTTP da Brevo via `urllib`, sem dependência nova.
- **Controles contra abuso**: cabeçalhos de segurança em todas as respostas (CSP, HSTS, nosniff, SAMEORIGIN, Referrer-Policy, Permissions-Policy); cadastro com limite de 5 por hora por IP, campo isca e teto de 100 pendentes; login e recuperação de senha também limitados por IP. Os contadores ficam em memória e zeram ao reiniciar o container.
- **Backup**: o servidor faz backup diário do banco (com 14 dias de retenção) e espelha as devocionais; veja a seção Backup. Falta ainda uma cópia fora da VM.
- **PWA simples**: dá para instalar na tela do celular e abre em tela cheia, mas não funciona 100% offline (só o CSS/ícones ficam em cache) — o conteúdo em si precisa de internet.
