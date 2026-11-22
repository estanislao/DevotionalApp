"""Portal de devocionais — app único (FastAPI).

Rotas principais:
  /                         início (abas Coleção e Últimas)
  /login, /cadastro         entrar com e-mail + senha; criar conta (aprovada pelo admin)
  /esqueci, /redefinir/..   recuperar senha por e-mail
  /colecao/{id}             devocionais de uma coleção
  /d/{slug}                 trilha da devocional (5 passos)
  /d/{slug}/ouvir|ler|slides|quiz|praticar
  /admin                    publicar nova devocional
  /admin/membros            inscrições pendentes, membros ativos
  /admin/colecoes           criar e editar coleções
"""
import os
import re
import hmac
import html as _html
import json
import unicodedata
import secrets
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates

from . import db, content, auth, mailer

BASE_DIR = Path(__file__).parent
DEVOCIONAIS_DIR = BASE_DIR.parent / "devocionais"

# Limites de upload (bytes). Áudio de podcast de 30 min em 64 kbps ≈ 15 MB.
LIMITE_AUDIO = 200 * 1024 * 1024
LIMITE_PDF = 50 * 1024 * 1024
LIMITE_TEXTO = 2 * 1024 * 1024
EXT_AUDIO = {".mp3", ".m4a", ".aac", ".ogg", ".wav"}
EXT_PDF = {".pdf"}
TIPOS_MIDIA = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".ogg": "audio/ogg",
               ".wav": "audio/wav", ".pdf": "application/pdf"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
EMAIL_MAX = 254
RESET_VALIDADE = timedelta(minutes=30)
RESET_INTERVALO = timedelta(minutes=5)   # um e-mail por conta a cada 5 min
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_NOME = "Trilha de Fé"
PARTILHA_MAX = 280
# Cadastro por convite: sem CADASTRO_CODIGO no .env as inscrições ficam fechadas.
CADASTRO_CODIGO = os.environ.get("CADASTRO_CODIGO", "").strip()
CONVITE_ERROS_POR_IP = 10     # tentativas de código errado por hora
CORPO_MAX_PADRAO = 1 * 1024 * 1024            # 1 MB em qualquer rota
CORPO_MAX_PUBLICAR = 260 * 1024 * 1024       # só /admin/publicar aceita arquivos grandes
# Limites contra abuso (por IP; contadores em memória, zeram ao reiniciar)
CADASTRO_POR_IP = 5            # inscrições por hora
CADASTRO_PENDENTES_MAX = 100   # acima disso, inscrições ficam fechadas até o admin limpar a fila
CADASTRO_AVISO_MAX = 20        # só avisa o admin por e-mail enquanto houver até N pendentes
LOGIN_FALHAS_POR_IP = 20       # senhas erradas por 15 minutos
ESQUECI_POR_IP = 10            # pedidos de recuperação por hora

# Cabeçalhos de segurança em todas as respostas. 'unsafe-inline' é necessário porque as telas
# usam scripts e estilos inline; o restante da política continua restringindo origens.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; media-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
       "base-uri 'self'; form-action 'self'; object-src 'none'")
CABECALHOS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",   # SAMEORIGIN e não DENY: o PDF de reserva usa iframe da mesma origem
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
PARTILHAS_POR_DIA = 3
PARTILHAS_NO_INICIO = 15


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def ler_admin_env() -> dict | None:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    senha = os.environ.get("ADMIN_PASSWORD", "")
    nome = os.environ.get("ADMIN_NAME", "Administrador").strip() or "Administrador"
    if not senha:
        return None   # sem ADMIN_PASSWORD o admin não é criado (nem alterado): normal depois do primeiro boot
    if not EMAIL_RE.match(email) or len(senha) < auth.SENHA_MIN:
        raise RuntimeError("ADMIN_EMAIL precisa ser um e-mail válido e ADMIN_PASSWORD ter pelo menos "
                           f"{auth.SENHA_MIN} caracteres (veja .env.example).")
    return {"name": nome, "email": email, "password_hash": auth.hash_senha(senha)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db(ler_admin_env())
    DEVOCIONAIS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=APP_NOME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def origem_invalida(request: Request) -> bool:
    """Recusa POST que veio de outro site (defesa em profundidade além do SameSite=Lax do cookie)."""
    site = request.headers.get("sec-fetch-site", "")
    origem = request.headers.get("origin", "")
    if site == "cross-site":
        return True
    if not origem:
        return False          # clientes que não mandam Origin (curl, testes); o navegador sempre manda em POST
    if origem == "null":
        return site not in ("same-origin", "none")
    permitidos = {request.headers.get("host", "")}
    if PUBLIC_URL:
        permitidos.add(urlsplit(PUBLIC_URL).netloc)
    return urlsplit(origem).netloc not in permitidos


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    tamanho = request.headers.get("content-length", "")
    limite = CORPO_MAX_PUBLICAR if request.url.path == "/admin/publicar" else CORPO_MAX_PADRAO
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and origem_invalida(request):
        resp = Response("Origem não permitida.", status_code=403, media_type="text/plain; charset=utf-8")
    elif request.method in ("POST", "PUT", "PATCH") and tamanho.isdigit() and int(tamanho) > limite:
        resp = Response("Corpo da requisição grande demais.", status_code=413, media_type="text/plain; charset=utf-8")
    else:
        resp = await call_next(request)
    for nome, valor in CABECALHOS.items():
        resp.headers.setdefault(nome, valor)
    if auth.COOKIE_SECURE:   # HTTPS em produção; no teste local (http) o HSTS não se aplica
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


# ---------- Páginas de erro amigáveis (em vez de JSON) ----------

def pagina_erro(request: Request, status: int, titulo: str, mensagem: str, extra_url: str = "", extra_texto: str = ""):
    logado = auth.current_member(request) is not None
    return templates.TemplateResponse(request, "erro.html", {
        "titulo": titulo, "mensagem": mensagem,
        "voltar_url": "/" if logado else "/login",
        "voltar_texto": "Voltar ao início" if logado else "Ir para o login",
        "extra_url": extra_url, "extra_texto": extra_texto,
    }, status_code=status)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in (301, 302, 303, 307, 308) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    if exc.status_code == 404:
        return pagina_erro(request, 404, "Não encontrado", exc.detail or "Essa página ou devocional não existe.")
    if exc.status_code == 403:
        return pagina_erro(request, 403, "Sem permissão", exc.detail or "Você não pode acessar esta página.")
    return pagina_erro(request, exc.status_code, "Algo deu errado", str(exc.detail or "Tente novamente."))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return pagina_erro(request, 400, "Dados incompletos", "O formulário veio incompleto. Volte e tente de novo.")


# ---------- Helpers ----------

def progress_for(conn, member_id: int, slug: str) -> set[str]:
    rows = conn.execute(
        "SELECT step FROM progress WHERE member_id=? AND devotional_slug=?",
        (member_id, slug),
    ).fetchall()
    return {r["step"] for r in rows}


def mark_step(conn, member_id: int, slug: str, step: str):
    conn.execute(
        "INSERT OR IGNORE INTO progress (member_id, devotional_slug, step, completed_at) VALUES (?,?,?,?)",
        (member_id, slug, step, agora()),
    )


def devo_ou_404(slug: str) -> dict:
    devo = content.get_devotional(slug)
    if not devo:
        raise HTTPException(404, "Devocional não encontrada.")
    return devo


def colecoes_por_id(conn) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM collections ORDER BY name").fetchall()}


def anexar_colecao(devs: list[dict], mapa: dict[int, dict]) -> list[dict]:
    for d in devs:
        c = mapa.get(d.get("colecao_id"))
        d["colecao_nome"] = c["name"] if c else None
    return devs


def primeiro_nome(nome: str) -> str:
    return nome.strip().split(" ")[0] if nome else ""


def enviar_email_silencioso(para: str, assunto: str, texto: str, html: str) -> None:
    """Para avisos que não podem travar o fluxo (ex.: admin avisado de inscrição nova)."""
    try:
        mailer.enviar(para, assunto, texto, html)
        print(f"[email] enviado para {para}: {assunto}", flush=True)
    except RuntimeError as e:
        print(f"[email] FALHA para {para}: {e}", flush=True)


def esc(valor: str) -> str:
    """Escapa texto vindo do usuário antes de entrar num e-mail em HTML."""
    return _html.escape(str(valor), quote=True)


def botao_email(link: str, rotulo: str) -> str:
    return (f'<a href="{esc(link)}" style="display:inline-block;padding:12px 20px;background:#2E3192;color:#fff;'
            f'border-radius:10px;text-decoration:none;font-weight:600">{esc(rotulo)}</a>')


# ---------- Login / cadastro / logout ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    erro = request.query_params.get("erro")
    mensagens = {
        "1": "E-mail ou senha incorretos. Se você acabou de criar uma senha, use o mesmo e-mail que recebeu o link.",
        "pendente": "Sua inscrição ainda não foi aprovada pelo administrador.",
        "bloqueado": "Muitas tentativas erradas. Espere alguns minutos e tente de novo.",
    }
    ok = request.query_params.get("ok")
    ok_msgs = {
        "senha": "Senha redefinida. Entre com a nova.",
        "cadastro": "Inscrição enviada! Você poderá entrar assim que o administrador aprovar. Se você já tinha conta, use 'Recuperar senha'.",
    }
    return templates.TemplateResponse(request, "login.html", {
        "erro_msg": mensagens.get(erro), "ok_msg": ok_msgs.get(ok),
        "email_habilitado": mailer.email_habilitado(),
        "email": request.query_params.get("email", ""),
    })


def sessao_cookie(resp, member_id: int, version: int = 0):
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.make_session_cookie(member_id, version),
        max_age=auth.MAX_AGE,
        httponly=True,
        secure=auth.COOKIE_SECURE,
        samesite="lax",
    )
    return resp


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), senha: str = Form(...)):
    email = email.strip().lower()[:EMAIL_MAX]
    ip = ip_cliente(request)
    if auth.login_bloqueado(email, ip):
        return RedirectResponse("/login?erro=bloqueado", status_code=303)
    with db.session() as conn:
        row = conn.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
    # Sempre calcula um hash, mesmo sem conta: o tempo de resposta não revela quais e-mails existem.
    confere = auth.verificar_senha(senha, (row["password_hash"] if row else None) or auth.hash_falso())
    if not row or not confere:
        auth.login_falhou(email, ip)
        return RedirectResponse(f"/login?erro=1&email={quote(email)}", status_code=303)
    if row["status"] != "ativo":
        return RedirectResponse("/login?erro=pendente", status_code=303)
    auth.login_ok(email, ip)
    if auth.precisa_rehash(row["password_hash"]):   # hash antigo (menos iterações): atualiza agora que temos a senha
        with db.session() as conn:
            conn.execute("UPDATE members SET password_hash=? WHERE id=?", (auth.hash_senha(senha), row["id"]))
    return sessao_cookie(RedirectResponse("/", status_code=303), row["id"], row["session_version"])


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


def convite_valido(codigo: str) -> bool:
    return bool(CADASTRO_CODIGO) and hmac.compare_digest(codigo.encode("utf-8"), CADASTRO_CODIGO.encode("utf-8"))


def pagina_cadastro(request: Request, aberto: bool, convite: str = "", status: int = 200, **extra):
    return templates.TemplateResponse(request, "cadastro.html", {
        "aberto": aberto, "convite": convite if aberto else "",
        "erro_msg": extra.get("erro_msg"), "nome": extra.get("nome", ""), "email": extra.get("email", ""),
        "senha_min": auth.SENHA_MIN,
    }, status_code=status)


@app.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    codigo = request.query_params.get("c", "")
    aberto = convite_valido(codigo)
    return pagina_cadastro(request, aberto, codigo,
                           erro_msg=request.query_params.get("erro") if aberto else None,
                           nome=request.query_params.get("nome", ""), email=request.query_params.get("email", ""))


@app.post("/cadastro")
def cadastro_submit(request: Request, nome: str = Form(...), email: str = Form(...), senha: str = Form(...),
                    senha2: str = Form(...), site: str = Form(""), convite: str = Form("")):
    nome, email = nome.strip()[:60], email.strip().lower()
    if site.strip():   # campo isca, invisível para pessoas: só robôs preenchem. Finge sucesso e não cria nada.
        return RedirectResponse("/login?ok=cadastro", status_code=303)
    ip = ip_cliente(request)
    if not convite_valido(convite):
        auth.registrar_evento(f"convite:{ip}")   # quem chuta códigos é freado (a resposta é a mesma de sempre)
        return pagina_cadastro(request, False, status=403)
    if auth.eventos_recentes(f"convite:{ip}", 3600) >= CONVITE_ERROS_POR_IP:
        return pagina_cadastro(request, False, status=403)

    def volta(msg: str):
        return RedirectResponse(f"/cadastro?c={quote(convite)}&erro={quote(msg)}&nome={quote(nome)}&email={quote(email)}", status_code=303)

    if len(nome) < 2:
        return volta("Digite seu nome.")
    if len(email) > EMAIL_MAX or not EMAIL_RE.match(email):
        return volta("E-mail inválido.")
    erro_senha = auth.validar_senha(senha)
    if erro_senha:
        return volta(erro_senha)
    if senha != senha2:
        return volta("As duas senhas não são iguais.")
    if not auth.permitir(f"cadastro:{ip}", CADASTRO_POR_IP, 3600):
        return volta("Muitas inscrições deste endereço. Tente de novo mais tarde.")
    novo_hash = auth.hash_senha(senha)    # sempre calcula, exista ou não a conta: mesmo tempo de resposta
    with db.session() as conn:
        pendentes = conn.execute("SELECT COUNT(*) FROM members WHERE status='pendente'").fetchone()[0]
        if pendentes >= CADASTRO_PENDENTES_MAX:
            return volta("As inscrições estão temporariamente fechadas. Fale com o administrador.")
        if conn.execute("SELECT 1 FROM members WHERE email=?", (email,)).fetchone():
            return RedirectResponse("/login?ok=cadastro", status_code=303)   # resposta neutra: não revela que a conta existe
        conn.execute(
            "INSERT INTO members (name, email, password_hash, pin, is_admin, status, created_at) VALUES (?,?,?,'',0,'pendente',?)",
            (nome, email, novo_hash, agora()),
        )
        admins = [r["email"] for r in conn.execute(
            "SELECT email FROM members WHERE is_admin=1 AND email IS NOT NULL").fetchall()]
    if mailer.email_habilitado() and pendentes < CADASTRO_AVISO_MAX:
        link = f"{PUBLIC_URL}/admin/membros" if PUBLIC_URL else "/admin/membros"
        for adm in admins:
            enviar_email_silencioso(
                adm, f"{APP_NOME}: nova inscrição de {nome}",
                f"{nome} ({email}) pediu para entrar no {APP_NOME}.\n\nAprove ou recuse em: {link}",
                f"<p><strong>{esc(nome)}</strong> ({esc(email)}) pediu para entrar no {APP_NOME}.</p>"
                f"<p>{botao_email(link, 'Ver inscrições')}</p>",
            )
    return RedirectResponse("/login?ok=cadastro", status_code=303)


# ---------- Esqueci minha senha ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@app.get("/esqueci", response_class=HTMLResponse)
def esqueci_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "esqueci.html", {
        "email_habilitado": mailer.email_habilitado(),
        "erro_msg": request.query_params.get("erro"),
        "email": request.query_params.get("email", ""),
    })


def mascarar_email(email: str) -> str:
    nome, _, dominio = email.partition("@")
    return f"{nome[:1]}***@{dominio}" if dominio else email


@app.get("/esqueci/enviado", response_class=HTMLResponse)
def esqueci_enviado(request: Request):
    # Recebe o e-mail JÁ mascarado (a***@dominio.com); aceita só caracteres de e-mail, para não exibir texto livre.
    recebido = re.sub(r"[^A-Za-z0-9*@._+\-]", "", request.query_params.get("e", ""))[:80]
    return templates.TemplateResponse(request, "esqueci_enviado.html", {"mascarado": recebido or "o seu e-mail"})


@app.post("/esqueci")
def esqueci_enviar(request: Request, background: BackgroundTasks, email: str = Form(...)):
    email = email.strip().lower()
    if not mailer.email_habilitado():
        return RedirectResponse(f"/esqueci?erro={quote('Envio de e-mail não está configurado. Fale com o administrador.')}", status_code=303)
    # Resposta é a mesma exista a conta ou não (não revela quem está cadastrado).
    if len(email) > EMAIL_MAX or not EMAIL_RE.match(email):
        return RedirectResponse(f"/esqueci?erro={quote('Digite um e-mail válido.')}&email={quote(email[:EMAIL_MAX])}", status_code=303)
    enviado = RedirectResponse(f"/esqueci/enviado?e={quote(mascarar_email(email))}", status_code=303)
    if not auth.permitir(f"esqueci:{ip_cliente(request)}", ESQUECI_POR_IP, 3600):
        return enviado   # mesma resposta de sempre: não revela que o limite foi atingido
    with db.session() as conn:
        row = conn.execute("SELECT id, name, email FROM members WHERE email=? AND status='ativo'", (email,)).fetchone()
        if not row:
            return enviado
        ultimo = conn.execute(
            "SELECT created_at FROM pin_resets WHERE member_id=? ORDER BY id DESC LIMIT 1", (row["id"],)
        ).fetchone()
        if ultimo and datetime.fromisoformat(ultimo["created_at"]) + RESET_INTERVALO > datetime.now(timezone.utc):
            return enviado
        token = secrets.token_urlsafe(32)
        agora_dt = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO pin_resets (member_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
            (row["id"], _hash_token(token), agora_dt.isoformat(), (agora_dt + RESET_VALIDADE).isoformat()),
        )
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    link = f"{base}/redefinir/{token}"
    texto = (f"Olá, {primeiro_nome(row['name'])}.\n\nPara criar uma nova senha no {APP_NOME}, abra este link "
             f"(vale por 30 minutos):\n\n{link}\n\nSe não foi você que pediu, ignore este e-mail.")
    html = (f"<p>Olá, {esc(primeiro_nome(row['name']))}.</p><p>Para criar uma nova senha no {APP_NOME}, toque no botão "
            f"abaixo (vale por 30 minutos):</p><p>{botao_email(link, 'Criar nova senha')}</p>"
            f'<p style="color:#666;font-size:13px">Se não foi você que pediu, ignore este e-mail.<br>Link: {esc(link)}</p>')
    # Envio em segundo plano: a resposta sai logo, com ou sem conta, e o tempo não revela quem tem cadastro.
    background.add_task(enviar_email_silencioso, row["email"], f"Sua nova senha no {APP_NOME}", texto, html)
    return enviado


def _reset_valido(conn, token: str):
    row = conn.execute("SELECT * FROM pin_resets WHERE token_hash=?", (_hash_token(token),)).fetchone()
    if not row or row["used_at"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row


LINK_VENCIDO = ("Link inválido ou vencido",
                "Esse link já foi usado ou passou dos 30 minutos. Peça um novo e use sempre o e-mail mais recente.",
                "/esqueci", "Pedir um novo link")


@app.get("/redefinir/{token}", response_class=HTMLResponse)
def redefinir_form(request: Request, token: str):
    with db.session() as conn:
        reset = _reset_valido(conn, token)
        if not reset:
            return pagina_erro(request, 400, *LINK_VENCIDO)
        membro = conn.execute("SELECT name FROM members WHERE id=?", (reset["member_id"],)).fetchone()
    return templates.TemplateResponse(request, "redefinir.html", {
        "token": token, "nome": primeiro_nome(membro["name"]),
        "erro_msg": request.query_params.get("erro"), "senha_min": auth.SENHA_MIN,
    })


def trocar_senha(conn, member_id: int, senha: str) -> int:
    """Grava a senha nova, derruba as sessões antigas e invalida os links de recuperação pendentes.
    Devolve a nova versão de sessão."""
    conn.execute("UPDATE members SET password_hash=?, session_version=session_version+1 WHERE id=?",
                 (auth.hash_senha(senha), member_id))
    conn.execute("UPDATE pin_resets SET used_at=? WHERE member_id=? AND used_at IS NULL", (agora(), member_id))
    return conn.execute("SELECT session_version FROM members WHERE id=?", (member_id,)).fetchone()[0]


@app.post("/redefinir/{token}")
def redefinir_salvar(request: Request, token: str, senha: str = Form(...), senha2: str = Form(...)):
    erro = auth.validar_senha(senha) or (None if senha == senha2 else "As duas senhas não são iguais.")
    if erro:
        return RedirectResponse(f"/redefinir/{token}?erro={quote(erro)}", status_code=303)
    with db.session() as conn:
        reset = _reset_valido(conn, token)
        if not reset:
            return pagina_erro(request, 400, *LINK_VENCIDO)
        versao = trocar_senha(conn, reset["member_id"], senha)
        membro = conn.execute("SELECT id, email FROM members WHERE id=?", (reset["member_id"],)).fetchone()
    if membro["email"]:
        auth.limpar_login(membro["email"])
    # Quem abriu o link do e-mail já provou que é dono da conta: entra direto, sem digitar de novo.
    return sessao_cookie(RedirectResponse("/", status_code=303), membro["id"], versao)


@app.get("/conta/senha", response_class=HTMLResponse)
def conta_senha_form(request: Request):
    auth.require_member(request)
    return templates.TemplateResponse(request, "conta_senha.html", {"senha_min": auth.SENHA_MIN, "erro_msg": None, "ok": False})


@app.post("/conta/senha", response_class=HTMLResponse)
def conta_senha_salvar(request: Request, atual: str = Form(...), senha: str = Form(...), senha2: str = Form(...)):
    member = auth.require_member(request)

    def tela(erro=None, ok=False, status=200):
        return templates.TemplateResponse(request, "conta_senha.html",
                                          {"senha_min": auth.SENHA_MIN, "erro_msg": erro, "ok": ok}, status_code=status)

    if not auth.permitir(f"conta:{member['id']}", 5, 900):
        return tela("Muitas tentativas. Espere alguns minutos.", status=429)
    if not auth.verificar_senha(atual, member["password_hash"]):
        return tela("A senha atual não confere.", status=400)
    erro = auth.validar_senha(senha) or (None if senha == senha2 else "As duas senhas não são iguais.")
    if erro:
        return tela(erro, status=400)
    with db.session() as conn:
        versao = trocar_senha(conn, member["id"], senha)
    resp = tela(ok=True)
    return sessao_cookie(resp, member["id"], versao)   # esta sessão continua valendo; as outras caem


# ---------- Início ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    member = auth.current_member(request)
    if not member:
        return RedirectResponse("/login")

    with db.session() as conn:
        mapa = colecoes_por_id(conn)
        devotionals = anexar_colecao(content.list_devotionals(), mapa)
        destaque = devotionals[0] if devotionals else None
        ultimas = devotionals[:10]
        contagem = content.count_by_collection()
        colecoes = [dict(c, total=contagem.get(c["id"], 0)) for c in mapa.values()]
        avulsas = sum(1 for d in devotionals if d.get("colecao_id") not in mapa)

        members = conn.execute("SELECT id, name FROM members WHERE status='ativo' ORDER BY id").fetchall()
        familia = []
        for m in members:
            done = content.atividades_feitas(progress_for(conn, m["id"], destaque["slug"])) if destaque else 0
            familia.append({"name": primeiro_nome(m["name"]), "done": done, "total": content.TOTAL_ATIVIDADES})
        meu_progresso = content.atividades_feitas(progress_for(conn, member["id"], destaque["slug"])) if destaque else 0
        partilhas = [dict(r, autor=primeiro_nome(r["name"])) for r in conn.execute(
            """SELECT s.id, s.text, s.created_at, s.member_id, m.name FROM shares s
               JOIN members m ON m.id = s.member_id ORDER BY s.id DESC LIMIT ?""", (PARTILHAS_NO_INICIO,)).fetchall()]
        pendentes = conn.execute("SELECT COUNT(*) FROM members WHERE status='pendente'").fetchone()[0] if member["is_admin"] else 0

    return templates.TemplateResponse(request, "index.html", {
        "member": member, "destaque": destaque, "ultimas": ultimas, "colecoes": colecoes, "avulsas": avulsas,
        "familia": familia, "meu_progresso": meu_progresso, "total_passos": content.TOTAL_ATIVIDADES,
        "aba": request.query_params.get("aba", "colecao"), "pendentes": pendentes,
        "partilhas": partilhas, "partilha_max": PARTILHA_MAX,
        "partilha_erro": request.query_params.get("partilha_erro"),
    })


# ---- Partilhas: algo encorajador que qualquer membro deixa para todos ----

@app.post("/partilhas")
def partilha_criar(request: Request, texto: str = Form("")):
    member = auth.require_member(request)
    texto = " ".join(texto.split())
    if len(texto) < 3:
        return RedirectResponse(f"/?partilha_erro={quote('Escreva algo antes de compartilhar.')}#partilhas", status_code=303)
    if len(texto) > PARTILHA_MAX:
        return RedirectResponse(f"/?partilha_erro={quote(f'Use até {PARTILHA_MAX} caracteres.')}#partilhas", status_code=303)
    with db.session() as conn:
        recentes = conn.execute(
            "SELECT COUNT(*) FROM shares WHERE member_id=? AND created_at > ?",
            (member["id"], (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()),
        ).fetchone()[0]
        if recentes >= PARTILHAS_POR_DIA:
            return RedirectResponse(f"/?partilha_erro={quote(f'Você já compartilhou {PARTILHAS_POR_DIA} vezes hoje. Volte amanhã.')}#partilhas", status_code=303)
        conn.execute("INSERT INTO shares (member_id, text, created_at) VALUES (?,?,?)", (member["id"], texto, agora()))
    return RedirectResponse("/#partilhas", status_code=303)


@app.post("/partilhas/{share_id}/excluir")
def partilha_excluir(request: Request, share_id: int):
    member = auth.require_member(request)
    with db.session() as conn:
        row = conn.execute("SELECT member_id FROM shares WHERE id=?", (share_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Partilha não encontrada.")
        if row["member_id"] != member["id"] and not member["is_admin"]:
            raise HTTPException(403, "Só quem escreveu ou o administrador pode apagar.")
        conn.execute("DELETE FROM shares WHERE id=?", (share_id,))
    return RedirectResponse("/#partilhas", status_code=303)


@app.get("/colecao/{colecao_id}", response_class=HTMLResponse)
def ver_colecao(request: Request, colecao_id: int):
    member = auth.require_member(request)
    with db.session() as conn:
        col = conn.execute("SELECT * FROM collections WHERE id=?", (colecao_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Coleção não encontrada.")
        devs = content.list_by_collection(colecao_id)
        feitos = {}
        for d in devs:
            feitos[d["slug"]] = content.atividades_feitas(progress_for(conn, member["id"], d["slug"]))
    return templates.TemplateResponse(request, "colecao.html", {
        "colecao": dict(col), "devs": devs, "feitos": feitos, "total_passos": content.TOTAL_ATIVIDADES,
    })


# ---------- Trilha da devocional ----------

@app.get("/d/{slug}", response_class=HTMLResponse)
def devocional_trilha(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    with db.session() as conn:
        feitos = progress_for(conn, member["id"], slug)
        anexar_colecao([devo], colecoes_por_id(conn))
    return templates.TemplateResponse(request, "devocional.html", {"devo": devo, "feitos": feitos, "steps": content.STEPS,
    })


@app.get("/d/{slug}/ouvir", response_class=HTMLResponse)
def passo_ouvir(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    tem_audio = content.has_file(slug, devo.get("audio", "audio.m4a"))
    return templates.TemplateResponse(request, "ouvir.html", {"devo": devo, "tem_audio": tem_audio,
    })


@app.post("/d/{slug}/ouvir/concluir")
def concluir_ouvir(request: Request, slug: str):
    member = auth.require_member(request)
    devo_ou_404(slug)
    with db.session() as conn:
        mark_step(conn, member["id"], slug, "ouvir")
    return RedirectResponse(f"/d/{slug}/quiz", status_code=303)


@app.get("/d/{slug}/ler", response_class=HTMLResponse)
def passo_ler(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    texto_html = content.get_texto_html(slug)
    with db.session() as conn:
        mark_step(conn, member["id"], slug, "ler")  # concluído ao abrir a tela
    return templates.TemplateResponse(request, "ler.html", {"devo": devo, "texto_html": texto_html,
    })


@app.get("/d/{slug}/slides", response_class=HTMLResponse)
def passo_slides(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    pdf_nome = devo.get("apresentacao", "apresentacao.pdf")
    tem_pdf = content.has_file(slug, pdf_nome)
    total = content.gerar_slides(slug, pdf_nome) if tem_pdf else 0   # gera na 1ª abertura se ainda não existem
    with db.session() as conn:
        mark_step(conn, member["id"], slug, "slides")
    return templates.TemplateResponse(request, "slides.html", {
        "devo": devo, "tem_pdf": tem_pdf, "total_slides": total,
        "proporcao": content.slides_proporcao(slug) if total else 16 / 9,
    })


@app.get("/d/{slug}/slide/{numero}")
def slide_imagem(request: Request, slug: str, numero: int):
    auth.require_member(request)
    path = content.slide_path(slug, numero)
    if not path:
        raise HTTPException(404, "Slide não encontrado.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/d/{slug}/quiz", response_class=HTMLResponse)
def passo_quiz(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    perguntas = content.get_quiz(slug)
    with db.session() as conn:
        respondidas = {
            r["question_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM quiz_answers WHERE member_id=? AND devotional_slug=?",
                (member["id"], slug),
            ).fetchall()
        }
    return templates.TemplateResponse(request, "quiz.html", {"devo": devo, "perguntas": perguntas,
        "respondidas": respondidas, "total": len(perguntas),
    })


@app.post("/d/{slug}/quiz/responder")
def responder_quiz(request: Request, slug: str, question_id: str = Form(...),
                    escolha: str = Form(...)):
    member = auth.require_member(request)
    devo_ou_404(slug)
    perguntas = content.get_quiz(slug)
    pergunta = next((p for p in perguntas if p["id"] == question_id), None)
    if not pergunta or escolha not in pergunta["opcoes"]:
        raise HTTPException(400, "Resposta inválida para esta pergunta.")
    # A resposta certa vem do quiz.json no servidor, nunca do formulário.
    is_correct = escolha == pergunta["correta"]
    with db.session() as conn:
        conn.execute(
            """INSERT INTO quiz_answers
               (member_id, devotional_slug, question_id, chosen, is_correct, answered_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(member_id, devotional_slug, question_id) DO NOTHING""",   # a primeira resposta vale; reenviar não troca
            (member["id"], slug, question_id, escolha, int(is_correct), agora()),
        )
        respondidas = {
            r["question_id"] for r in conn.execute(
                "SELECT question_id FROM quiz_answers WHERE member_id=? AND devotional_slug=?",
                (member["id"], slug),
            ).fetchall()
        }
        if all(p["id"] in respondidas for p in perguntas):
            mark_step(conn, member["id"], slug, "quiz")
    return RedirectResponse(f"/d/{slug}/quiz#{quote(question_id)}-resposta", status_code=303)


@app.get("/d/{slug}/praticar", response_class=HTMLResponse)
def passo_praticar(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    licoes_html = content.get_licoes_html(slug)
    with db.session() as conn:
        nota = conn.execute(
            "SELECT text FROM notes WHERE member_id=? AND devotional_slug=?",
            (member["id"], slug),
        ).fetchone()
    return templates.TemplateResponse(request, "praticar.html", {"devo": devo, "licoes_html": licoes_html,
        "nota": nota["text"] if nota else "",
    })


@app.post("/d/{slug}/praticar/concluir")
def concluir_praticar(request: Request, slug: str, nota: str = Form("")):
    member = auth.require_member(request)
    devo_ou_404(slug)
    with db.session() as conn:
        if nota.strip():
            conn.execute(
                """INSERT INTO notes (member_id, devotional_slug, text, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(member_id, devotional_slug)
                   DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at""",
                (member["id"], slug, nota.strip()[:5000], agora()),
            )
        mark_step(conn, member["id"], slug, "praticar")
    return RedirectResponse(f"/d/{slug}?concluida=1", status_code=303)


# ---------- Mídia (áudio e PDF servidos da pasta devocionais/) ----------

@app.get("/d/{slug}/midia/{filename}")
def midia(request: Request, slug: str, filename: str):
    auth.require_member(request)
    devo = devo_ou_404(slug)
    path = content.media_path(devo, filename)
    if not path:
        raise HTTPException(404, "Arquivo não encontrado.")
    # Tipo explícito: a imagem slim não conhece .m4a e mandaria octet-stream, que o Safari do iPhone não toca.
    return FileResponse(path, media_type=TIPOS_MIDIA.get(path.suffix.lower()), content_disposition_type="inline")


# ---------- Admin: publicar ----------

@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    auth.require_admin(request)
    with db.session() as conn:
        mapa = colecoes_por_id(conn)
        pendentes = conn.execute("SELECT COUNT(*) FROM members WHERE status='pendente'").fetchone()[0]
    return templates.TemplateResponse(request, "admin.html", {
        "devotionals": anexar_colecao(content.list_devotionals(), mapa),
        "colecoes": list(mapa.values()), "pendentes": pendentes,
        "erro_msg": request.query_params.get("erro"),
    })


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


async def ler_upload(upload: UploadFile, limite: int, rotulo: str) -> bytes:
    data = await upload.read(limite + 1)
    if len(data) > limite:
        raise ValueError(f"{rotulo} passou do limite de {limite // (1024 * 1024)} MB.")
    if not data:
        raise ValueError(f"{rotulo} veio vazio.")
    return data


def extensao_permitida(upload: UploadFile, permitidas: set[str], rotulo: str) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in permitidas:
        raise ValueError(f"{rotulo}: extensão '{ext or '(nenhuma)'}' não aceita. Use {', '.join(sorted(permitidas))}.")
    return ext


@app.post("/admin/publicar")
async def admin_publicar(
    request: Request,
    titulo: str = Form(...),
    data: str = Form(...),
    versiculo: str = Form(""),
    colecao_id: str = Form(""),
    texto: UploadFile = File(...),
    audio: UploadFile = File(...),
    apresentacao: UploadFile = File(...),
    quiz: UploadFile = File(...),
    licoes: UploadFile = File(...),
):
    auth.require_admin(request)

    def falhou(msg: str):
        return RedirectResponse(f"/admin?erro={quote(msg)}", status_code=303)

    titulo = titulo.strip()[:120]
    data = data.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        return falhou("Data inválida. Use o formato AAAA-MM-DD.")
    try:
        datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        return falhou("Data inválida.")
    titulo_slug = slugify(titulo)
    if not titulo_slug:
        return falhou("Título precisa ter pelo menos uma letra ou número.")

    colecao: int | None = None
    if colecao_id.strip():
        try:
            colecao = int(colecao_id)
        except ValueError:
            return falhou("Coleção inválida.")
        with db.session() as conn:
            if not conn.execute("SELECT 1 FROM collections WHERE id=?", (colecao,)).fetchone():
                return falhou("Coleção não existe mais. Recarregue a página.")

    slug = f"{data}-{titulo_slug}"
    if not content.slug_valido(slug):
        return falhou("Título gerou um nome de pasta inválido.")
    folder = DEVOCIONAIS_DIR / slug
    if folder.exists():
        return falhou("Já existe uma devocional com esse título e data. Mude o título ou apague a pasta antiga.")

    try:
        ext_audio = extensao_permitida(audio, EXT_AUDIO, "Áudio")
        ext_pdf = extensao_permitida(apresentacao, EXT_PDF, "Apresentação")
        texto_bytes = await ler_upload(texto, LIMITE_TEXTO, "Texto")
        licoes_bytes = await ler_upload(licoes, LIMITE_TEXTO, "Lições")
        quiz_bytes = await ler_upload(quiz, LIMITE_TEXTO, "Quiz")
        content.validar_quiz(quiz_bytes)
        for b, rotulo in ((texto_bytes, "Texto"), (licoes_bytes, "Lições")):
            try:
                b.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"{rotulo} precisa ser um arquivo de texto UTF-8.")
        audio_bytes = await ler_upload(audio, LIMITE_AUDIO, "Áudio")
        pdf_bytes = await ler_upload(apresentacao, LIMITE_PDF, "Apresentação")
        if not pdf_bytes.startswith(b"%PDF"):
            raise ValueError("Apresentação não parece ser um PDF válido.")
    except ValueError as e:
        return falhou(str(e))

    audio_name = f"audio{ext_audio}"
    pdf_name = f"apresentacao{ext_pdf}"

    folder.mkdir(parents=True)
    (folder / "texto.md").write_bytes(texto_bytes)
    (folder / audio_name).write_bytes(audio_bytes)
    (folder / pdf_name).write_bytes(pdf_bytes)
    (folder / "quiz.json").write_bytes(quiz_bytes)
    (folder / "licoes.md").write_bytes(licoes_bytes)

    meta = {
        "titulo": titulo,
        "data": data,
        "versiculo": versiculo.strip()[:120],
        "colecao_id": colecao,
        "audio": audio_name,
        "apresentacao": pdf_name,
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    content.get_texto_html.cache_clear()
    content.get_licoes_html.cache_clear()
    await run_in_threadpool(content.gerar_slides, slug, pdf_name)   # se falhar, a tela usa o PDF embutido

    return RedirectResponse("/admin?publicado=1", status_code=303)


# ---------- Admin: membros (inscrições, senha, exclusão) ----------

def _volta_membros(param: str, msg: str):
    return RedirectResponse(f"/admin/membros?{param}={quote(msg)}", status_code=303)


@app.get("/admin/membros", response_class=HTMLResponse)
def admin_membros(request: Request):
    me = auth.require_admin(request)
    with db.session() as conn:
        rows = conn.execute("SELECT id, name, email, is_admin, status, created_at FROM members ORDER BY id").fetchall()
    pendentes = [dict(r) for r in rows if r["status"] == "pendente"]
    ativos = [dict(r) for r in rows if r["status"] == "ativo"]
    return templates.TemplateResponse(request, "admin_membros.html", {
        "pendentes": pendentes, "ativos": ativos, "me": me,
        "convite_link": (f"{PUBLIC_URL or str(request.base_url).rstrip('/')}/cadastro?c={quote(CADASTRO_CODIGO)}" if CADASTRO_CODIGO else ""),
        "ok_msg": request.query_params.get("ok"),
        "erro_msg": request.query_params.get("erro"),
        "senha_min": auth.SENHA_MIN,
    })


def _membro_ou_404(conn, member_id: int):
    row = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Membro não encontrado.")
    return row


@app.post("/admin/membros/{member_id}/aprovar")
def admin_membro_aprovar(request: Request, member_id: int):
    auth.require_admin(request)
    with db.session() as conn:
        row = _membro_ou_404(conn, member_id)
        conn.execute("UPDATE members SET status='ativo' WHERE id=?", (member_id,))
    if row["email"] and mailer.email_habilitado():
        link = f"{PUBLIC_URL}/login" if PUBLIC_URL else "/login"
        enviar_email_silencioso(
            row["email"], f"Sua inscrição no {APP_NOME} foi aprovada",
            f"Olá, {primeiro_nome(row['name'])}.\n\nSua inscrição foi aprovada. Entre em: {link}",
            f"<p>Olá, {esc(primeiro_nome(row['name']))}.</p><p>Sua inscrição foi aprovada.</p><p>{botao_email(link, 'Entrar')}</p>",
        )
    return _volta_membros("ok", f"{row['name']} aprovado. Já pode entrar.")


@app.post("/admin/membros/{member_id}/excluir")
def admin_membro_excluir(request: Request, member_id: int):
    me = auth.require_admin(request)
    if member_id == me["id"]:
        return _volta_membros("erro", "Você não pode excluir a própria conta.")
    with db.session() as conn:
        row = _membro_ou_404(conn, member_id)
        for tabela in ("progress", "quiz_answers", "notes", "pin_resets", "shares"):
            conn.execute(f"DELETE FROM {tabela} WHERE member_id=?", (member_id,))
        conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    return _volta_membros("ok", f"{row['name']} excluído.")


@app.post("/admin/membros/{member_id}/senha")
def admin_membro_senha(request: Request, member_id: int, senha: str = Form(...)):
    auth.require_admin(request)
    erro = auth.validar_senha(senha)
    if erro:
        return _volta_membros("erro", erro)
    with db.session() as conn:
        row = _membro_ou_404(conn, member_id)
        trocar_senha(conn, member_id, senha)
    if row["email"]:
        auth.limpar_login(row["email"])
    return _volta_membros("ok", f"Senha de {row['name']} redefinida. As sessões abertas dessa pessoa foram encerradas.")


# ---------- Admin: coleções ----------

def _volta_colecoes(param: str, msg: str):
    return RedirectResponse(f"/admin/colecoes?{param}={quote(msg)}", status_code=303)


@app.get("/admin/colecoes", response_class=HTMLResponse)
def admin_colecoes(request: Request):
    auth.require_admin(request)
    contagem = content.count_by_collection()
    with db.session() as conn:
        cols = [dict(c, total=contagem.get(c["id"], 0)) for c in colecoes_por_id(conn).values()]
    return templates.TemplateResponse(request, "admin_colecoes.html", {
        "colecoes": cols,
        "ok_msg": request.query_params.get("ok"),
        "erro_msg": request.query_params.get("erro"),
    })


def _validar_colecao(nome: str, descricao: str) -> tuple[str, str] | str:
    nome, descricao = nome.strip()[:80], descricao.strip()[:500]
    if len(nome) < 2:
        return "Dê um nome à coleção."
    return nome, descricao


@app.post("/admin/colecoes")
def admin_colecao_criar(request: Request, nome: str = Form(...), descricao: str = Form("")):
    auth.require_admin(request)
    v = _validar_colecao(nome, descricao)
    if isinstance(v, str):
        return _volta_colecoes("erro", v)
    nome, descricao = v
    with db.session() as conn:
        conn.execute("INSERT INTO collections (name, description, created_at) VALUES (?,?,?)", (nome, descricao, agora()))
    return _volta_colecoes("ok", f"Coleção '{nome}' criada.")


@app.post("/admin/colecoes/{colecao_id}/editar")
def admin_colecao_editar(request: Request, colecao_id: int, nome: str = Form(...), descricao: str = Form("")):
    auth.require_admin(request)
    v = _validar_colecao(nome, descricao)
    if isinstance(v, str):
        return _volta_colecoes("erro", v)
    nome, descricao = v
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM collections WHERE id=?", (colecao_id,)).fetchone():
            raise HTTPException(404, "Coleção não encontrada.")
        conn.execute("UPDATE collections SET name=?, description=? WHERE id=?", (nome, descricao, colecao_id))
    return _volta_colecoes("ok", f"Coleção '{nome}' atualizada.")


@app.post("/admin/colecoes/{colecao_id}/excluir")
def admin_colecao_excluir(request: Request, colecao_id: int):
    auth.require_admin(request)
    if content.count_by_collection().get(colecao_id, 0) > 0:
        return _volta_colecoes("erro", "Essa coleção tem devocionais. Mova ou apague as devocionais antes de excluir.")
    with db.session() as conn:
        row = conn.execute("SELECT name FROM collections WHERE id=?", (colecao_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Coleção não encontrada.")
        conn.execute("DELETE FROM collections WHERE id=?", (colecao_id,))
    return _volta_colecoes("ok", f"Coleção '{row['name']}' excluída.")


# ---------- PWA ----------

@app.get("/manifest.json")
def manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")
