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
import json
import unicodedata
import secrets
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
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
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESET_VALIDADE = timedelta(minutes=30)
RESET_INTERVALO = timedelta(minutes=5)   # um e-mail por conta a cada 5 min
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_NOME = "Portal de Devocionais"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def ler_admin_env() -> dict | None:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    senha = os.environ.get("ADMIN_PASSWORD", "")
    nome = os.environ.get("ADMIN_NAME", "Administrador").strip() or "Administrador"
    if not email and not senha:
        return None
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


# ---------- Páginas de erro amigáveis (em vez de JSON) ----------

def pagina_erro(request: Request, status: int, titulo: str, mensagem: str):
    logado = auth.current_member(request) is not None
    return templates.TemplateResponse(request, "erro.html", {
        "titulo": titulo, "mensagem": mensagem,
        "voltar_url": "/" if logado else "/login",
        "voltar_texto": "Voltar ao início" if logado else "Ir para o login",
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
    except RuntimeError:
        pass


def botao_email(link: str, rotulo: str) -> str:
    return (f'<a href="{link}" style="display:inline-block;padding:12px 20px;background:#2E3192;color:#fff;'
            f'border-radius:10px;text-decoration:none;font-weight:600">{rotulo}</a>')


# ---------- Login / cadastro / logout ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    erro = request.query_params.get("erro")
    mensagens = {
        "1": "E-mail ou senha incorretos.",
        "pendente": "Sua inscrição ainda não foi aprovada pelo administrador.",
        "bloqueado": "Muitas tentativas erradas. Espere alguns minutos e tente de novo.",
    }
    ok = request.query_params.get("ok")
    ok_msgs = {
        "senha": "Senha redefinida. Entre com a nova.",
        "cadastro": "Inscrição enviada! Você poderá entrar assim que o administrador aprovar.",
    }
    return templates.TemplateResponse(request, "login.html", {
        "erro_msg": mensagens.get(erro), "ok_msg": ok_msgs.get(ok),
        "email_habilitado": mailer.email_habilitado(),
        "email": request.query_params.get("email", ""),
    })


@app.post("/login")
def login_submit(email: str = Form(...), senha: str = Form(...)):
    email = email.strip().lower()
    if auth.bloqueado_por(email) > 0:
        return RedirectResponse("/login?erro=bloqueado", status_code=303)
    with db.session() as conn:
        row = conn.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
    if not row or not auth.verificar_senha(senha, row["password_hash"]):
        auth.registrar_erro(email)
        return RedirectResponse(f"/login?erro=1&email={quote(email)}", status_code=303)
    if row["status"] != "ativo":
        return RedirectResponse("/login?erro=pendente", status_code=303)
    auth.registrar_acerto(email)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.make_session_cookie(row["id"]),
        max_age=auth.MAX_AGE,
        httponly=True,
        secure=auth.COOKIE_SECURE,
        samesite="lax",
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "cadastro.html", {
        "erro_msg": request.query_params.get("erro"),
        "nome": request.query_params.get("nome", ""),
        "email": request.query_params.get("email", ""),
        "senha_min": auth.SENHA_MIN,
    })


@app.post("/cadastro")
def cadastro_submit(nome: str = Form(...), email: str = Form(...), senha: str = Form(...), senha2: str = Form(...)):
    nome, email = nome.strip()[:60], email.strip().lower()

    def volta(msg: str):
        return RedirectResponse(f"/cadastro?erro={quote(msg)}&nome={quote(nome)}&email={quote(email)}", status_code=303)

    if len(nome) < 2:
        return volta("Digite seu nome.")
    if not EMAIL_RE.match(email):
        return volta("E-mail inválido.")
    if len(senha) < auth.SENHA_MIN:
        return volta(f"A senha precisa ter pelo menos {auth.SENHA_MIN} caracteres.")
    if senha != senha2:
        return volta("As duas senhas não são iguais.")
    with db.session() as conn:
        if conn.execute("SELECT 1 FROM members WHERE email=?", (email,)).fetchone():
            return volta("Já existe uma conta com esse e-mail. Use 'Esqueci minha senha' se precisar.")
        conn.execute(
            "INSERT INTO members (name, email, password_hash, pin, is_admin, status, created_at) VALUES (?,?,?,'',0,'pendente',?)",
            (nome, email, auth.hash_senha(senha), agora()),
        )
        admins = [r["email"] for r in conn.execute(
            "SELECT email FROM members WHERE is_admin=1 AND email IS NOT NULL").fetchall()]
    if mailer.email_habilitado():
        link = f"{PUBLIC_URL}/admin/membros" if PUBLIC_URL else "/admin/membros"
        for adm in admins:
            enviar_email_silencioso(
                adm, f"{APP_NOME}: nova inscrição de {nome}",
                f"{nome} ({email}) pediu para entrar no {APP_NOME}.\n\nAprove ou recuse em: {link}",
                f"<p><strong>{nome}</strong> ({email}) pediu para entrar no {APP_NOME}.</p>"
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
        "enviado": request.query_params.get("enviado"),
        "erro_msg": request.query_params.get("erro"),
    })


@app.post("/esqueci")
def esqueci_enviar(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    if not mailer.email_habilitado():
        return RedirectResponse(f"/esqueci?erro={quote('Envio de e-mail não está configurado. Fale com o administrador.')}", status_code=303)
    # Resposta é a mesma exista a conta ou não (não revela quem está cadastrado).
    enviado = RedirectResponse("/esqueci?enviado=1", status_code=303)
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
    html = (f"<p>Olá, {primeiro_nome(row['name'])}.</p><p>Para criar uma nova senha no {APP_NOME}, toque no botão "
            f"abaixo (vale por 30 minutos):</p><p>{botao_email(link, 'Criar nova senha')}</p>"
            f'<p style="color:#666;font-size:13px">Se não foi você que pediu, ignore este e-mail.<br>Link: {link}</p>')
    enviar_email_silencioso(row["email"], "Sua nova senha no Portal de Devocionais", texto, html)
    return enviado


def _reset_valido(conn, token: str):
    row = conn.execute("SELECT * FROM pin_resets WHERE token_hash=?", (_hash_token(token),)).fetchone()
    if not row or row["used_at"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row


LINK_VENCIDO = ("Link inválido ou vencido",
                "Esse link já foi usado ou passou dos 30 minutos. Peça um novo em 'Esqueci minha senha'.")


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


@app.post("/redefinir/{token}")
def redefinir_salvar(request: Request, token: str, senha: str = Form(...), senha2: str = Form(...)):
    if len(senha) < auth.SENHA_MIN:
        return RedirectResponse(f"/redefinir/{token}?erro={quote(f'A senha precisa ter pelo menos {auth.SENHA_MIN} caracteres.')}", status_code=303)
    if senha != senha2:
        return RedirectResponse(f"/redefinir/{token}?erro={quote('As duas senhas não são iguais.')}", status_code=303)
    with db.session() as conn:
        reset = _reset_valido(conn, token)
        if not reset:
            return pagina_erro(request, 400, *LINK_VENCIDO)
        conn.execute("UPDATE members SET password_hash=? WHERE id=?", (auth.hash_senha(senha), reset["member_id"]))
        conn.execute("UPDATE pin_resets SET used_at=? WHERE id=?", (agora(), reset["id"]))
        email = conn.execute("SELECT email FROM members WHERE id=?", (reset["member_id"],)).fetchone()["email"]
    if email:
        auth.registrar_acerto(email)
    return RedirectResponse("/login?ok=senha", status_code=303)


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
            done = len(progress_for(conn, m["id"], destaque["slug"])) if destaque else 0
            familia.append({"name": primeiro_nome(m["name"]), "done": done, "total": len(content.STEPS)})
        meu_progresso = len(progress_for(conn, member["id"], destaque["slug"])) if destaque else 0
        pendentes = conn.execute("SELECT COUNT(*) FROM members WHERE status='pendente'").fetchone()[0] if member["is_admin"] else 0

    return templates.TemplateResponse(request, "index.html", {
        "member": member, "destaque": destaque, "ultimas": ultimas, "colecoes": colecoes, "avulsas": avulsas,
        "familia": familia, "meu_progresso": meu_progresso, "total_passos": len(content.STEPS),
        "aba": request.query_params.get("aba", "colecao"), "pendentes": pendentes,
    })


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
            feitos[d["slug"]] = len(progress_for(conn, member["id"], d["slug"]))
    return templates.TemplateResponse(request, "colecao.html", {
        "colecao": dict(col), "devs": devs, "feitos": feitos, "total_passos": len(content.STEPS),
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
    return RedirectResponse(f"/d/{slug}/ler", status_code=303)


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
    tem_pdf = content.has_file(slug, devo.get("apresentacao", "apresentacao.pdf"))
    with db.session() as conn:
        mark_step(conn, member["id"], slug, "slides")
    return templates.TemplateResponse(request, "slides.html", {"devo": devo, "tem_pdf": tem_pdf,
    })


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
            """INSERT OR REPLACE INTO quiz_answers
               (member_id, devotional_slug, question_id, chosen, is_correct, answered_at)
               VALUES (?,?,?,?,?,?)""",
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
    return FileResponse(path)


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
            f"<p>Olá, {primeiro_nome(row['name'])}.</p><p>Sua inscrição foi aprovada.</p><p>{botao_email(link, 'Entrar')}</p>",
        )
    return _volta_membros("ok", f"{row['name']} aprovado. Já pode entrar.")


@app.post("/admin/membros/{member_id}/excluir")
def admin_membro_excluir(request: Request, member_id: int):
    me = auth.require_admin(request)
    if member_id == me["id"]:
        return _volta_membros("erro", "Você não pode excluir a própria conta.")
    with db.session() as conn:
        row = _membro_ou_404(conn, member_id)
        for tabela in ("progress", "quiz_answers", "notes", "pin_resets"):
            conn.execute(f"DELETE FROM {tabela} WHERE member_id=?", (member_id,))
        conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    return _volta_membros("ok", f"{row['name']} excluído.")


@app.post("/admin/membros/{member_id}/senha")
def admin_membro_senha(request: Request, member_id: int, senha: str = Form(...)):
    auth.require_admin(request)
    if len(senha) < auth.SENHA_MIN:
        return _volta_membros("erro", f"A senha precisa ter pelo menos {auth.SENHA_MIN} caracteres.")
    with db.session() as conn:
        row = _membro_ou_404(conn, member_id)
        conn.execute("UPDATE members SET password_hash=? WHERE id=?", (auth.hash_senha(senha), member_id))
    if row["email"]:
        auth.registrar_acerto(row["email"])
    return _volta_membros("ok", f"Senha de {row['name']} redefinida.")


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
