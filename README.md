# Portal de Devocionais

App único (FastAPI + SQLite + HTML server-side) para publicar devocionais em família. Você publica, a família consome pelo celular como um PWA (instalável, sem precisar de app store).

## O que tem aqui

```
app/                 código do servidor (FastAPI)
  main.py            rotas
  db.py              SQLite — membros, progresso, respostas do quiz, anotações
  content.py         lê as devocionais da pasta devocionais/
  auth.py            login por PIN (cookie de sessão)
  templates/         as 9 telas (login, início, trilha, ouvir, ler, slides, quiz, praticar, admin)
  static/            CSS, manifest.json e ícones do PWA
devocionais/         uma pasta por devocional (seus arquivos ficam aqui)
Dockerfile
docker-compose.yml   app + Caddy (HTTPS automático)
Caddyfile            troque pelo seu domínio
.env.example         copie para .env e preencha
```

## Estrutura de uma devocional

Cada publicação cria `devocionais/<data>-<titulo-slug>/` com:

- `meta.json` — título, data, versículo, nomes dos arquivos de áudio/pdf
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
nano .env                    # preencha SESSION_SECRET (openssl rand -hex 32) e SEED_MEMBERS
nano Caddyfile               # troque "devocional.seudominio.com.br" pelo seu domínio real

# 3. Suba
docker compose up -d --build

# 4. Acompanhe o primeiro boot (emissão do certificado HTTPS pode levar uns segundos)
docker compose logs -f
```

Depois disso, `https://seu-dominio` já está no ar. A família acessa, faz login com nome + PIN, e no Safari/Chrome do celular pode usar "Adicionar à tela de início" para instalar como app.

### Publicando uma devocional nova

Entre com o membro marcado como admin (no `.env`, é quem tem `:admin` depois do PIN em `SEED_MEMBERS`) e acesse `/admin`. Preencha o formulário e envie os 5 arquivos — a devocional aparece na hora para todo mundo.

O formulário valida antes de gravar: data no formato AAAA-MM-DD, áudio em .mp3/.m4a/.aac/.ogg/.wav, apresentação em .pdf, `quiz.json` no formato esperado (toda pergunta com `id`, `pergunta`, `opcoes`, `correta` presente nas opções). Se algo estiver errado, nada é gravado e a tela mostra o motivo. Publicar com o mesmo título e data de uma devocional existente é recusado (apague a pasta antiga em `devocionais/` se quiser substituir).

### Backup

Dois lugares para copiar periodicamente:
- a pasta `devocionais/` (seus arquivos originais)
- o volume `db_data` (progresso e respostas): `docker compose exec app cat /srv/app/data/portal.db > backup-$(date +%F).db`

### Trocar PIN ou adicionar alguém

Edite `SEED_MEMBERS` no `.env` e rode `docker compose up -d --build` de novo — membros novos são criados; os que já existem (mesmo nome) não são duplicados nem têm o PIN sobrescrito automaticamente (isso evita apagar progresso por engano). Para trocar um PIN de alguém que já existe, por enquanto é mais simples editar direto no banco:

```bash
docker compose exec app python3 -c "
from app.db import session
with session() as c:
    c.execute(\"UPDATE members SET pin=? WHERE name=?\", ('novoPIN', 'Membro 2'))
"
```

## Rodando localmente (para testar antes de subir)

```bash
pip install -r requirements.txt
SESSION_SECRET=segredo-local-de-teste COOKIE_SECURE=0 SEED_MEMBERS="Membro 1:1111:admin,Membro 2:2222" \
  uvicorn app.main:app --reload --port 8000
```

Abra `http://localhost:8000` — já tem uma devocional de exemplo em `devocionais/` para testar a trilha inteira.

## Decisões tomadas (e por quê)

- **SQLite, não Postgres**: um arquivo só, zero servidor extra para administrar — adequado para 4 pessoas.
- **Conclusão automática dos passos**: ouvir conta ao enviar o formulário no fim do áudio (hoje, ao clicar "Próximo passo" — dá pra trocar por um evento `timeupdate` do `<audio>` se quiser que seja automático de verdade), ler conta ao abrir a tela, slides ao abrir, quiz ao responder a última pergunta, praticar ao clicar "Concluir". Nada disso exige um botão de "marcar como feito" à parte.
- **Sem LGPD/compliance formal**: como você pediu, não tem nada de consentimento, retenção de dados ou exportação — é uma família, 4 contas, dados ficam só no seu volume Docker.
- **Segurança proporcional**: PIN de 4 dígitos é fraco por natureza, então o login bloqueia o membro por 15 minutos após 5 erros seguidos (contador em memória; reinicia com o container). O app recusa iniciar sem `SESSION_SECRET`, o cookie de sessão só trafega em HTTPS, a resposta certa do quiz é conferida no servidor (nunca vem do formulário) e só os arquivos declarados no `meta.json` são servidos pela rota de mídia. Erros (404, 403, formulário incompleto) mostram uma página amigável em vez de JSON.
- **PWA simples**: dá para instalar na tela do celular e abre em tela cheia, mas não funciona 100% offline (só o CSS/ícones ficam em cache) — o conteúdo em si precisa de internet.
