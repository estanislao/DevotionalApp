"""Portal de devocionais — app único (FastAPI).

Rotas principais:
  /                         início
  /login                    entrar com nome + PIN
  /d/{slug}                 trilha da devocional (5 passos)
  /d/{slug}/ouvir           player de áudio
  /d/{slug}/ler             texto
  /d/{slug}/slides          apresentação (PDF embutido)
  /d/{slug}/quiz            quiz
  /d/{slug}/praticar        lições práticas + anotação
  /admin                    só para o administrador: publicar nova devocional
"""
import os
import re
import json
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, content, auth

BASE_DIR = Path(__file__).parent
DEVOCIONAIS_DIR = BASE_DIR.parent / "devocionais"

# Limites de upload (bytes). Áudio de podcast de 30 min em 64 kbps ≈ 15 MB.
LIMITE_AUDIO = 200 * 1024 * 1024
LIMITE_PDF = 50 * 1024 * 1024
LIMITE_TEXTO = 2 * 1024 * 1024
EXT_AUDIO = {".mp3", ".m4a", ".aac", ".ogg", ".wav"}
EXT_PDF = {".pdf"}


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_seed(seed_raw: str) -> list[dict]:
    """Formato: "Nome:PIN:admin,Nome2:PIN2" — ':admin' é opcional."""
    seed = []
    for item in seed_raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise RuntimeError(f"SEED_MEMBERS inválido no trecho '{item}'. Use Nome:PIN ou Nome:PIN:admin.")
        seed.append({"name": parts[0], "pin": parts[1],
                     "is_admin": len(parts) > 2 and parts[2].lower() == "admin"})
    return seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_raw = os.environ.get("SEED_MEMBERS", "")
    if seed_raw:
        seed = parse_seed(seed_raw)
    else:
        seed = [
            {"name": "Membro 1", "pin": "1111", "is_admin": True},
            {"name": "Membro 2", "pin": "2222"},
            {"name": "Membro 3", "pin": "3333"},
            {"name": "Membro 4", "pin": "4444"},
        ]
    db.init_db(seed)
    DEVOCIONAIS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Portal de Devocionais", lifespan=lifespan)
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
    if request.url.path == "/login":
        return RedirectResponse("/login?erro=membro", status_code=303)
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


# ---------- Login ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.current_member(request):
        return RedirectResponse("/")
    with db.session() as conn:
        members = conn.execute("SELECT id, name FROM members ORDER BY id").fetchall()
    erro = request.query_params.get("erro")
    mensagens = {
        "1": "PIN incorreto. Tente novamente.",
        "membro": "Escolha quem é você antes de digitar o PIN.",
        "bloqueado": "Muitas tentativas erradas. Espere alguns minutos e tente de novo.",
    }
    return templates.TemplateResponse(request, "login.html", {
        "members": members, "erro_msg": mensagens.get(erro),
    })


@app.post("/login")
def login_submit(member_id: int = Form(...), pin: str = Form(...)):
    if auth.bloqueado_por(member_id) > 0:
        return RedirectResponse("/login?erro=bloqueado", status_code=303)
    with db.session() as conn:
        row = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not row or row["pin"] != pin.strip():
        auth.registrar_erro(member_id)
        return RedirectResponse("/login?erro=1", status_code=303)
    auth.registrar_acerto(member_id)
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


# ---------- Início ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    member = auth.current_member(request)
    if not member:
        return RedirectResponse("/login")

    devotionals = content.list_devotionals()
    destaque = devotionals[0] if devotionals else None
    anteriores = devotionals[1:6]

    with db.session() as conn:
        members = conn.execute("SELECT id, name FROM members ORDER BY id").fetchall()
        familia = []
        for m in members:
            done = 0
            if destaque:
                done = len(progress_for(conn, m["id"], destaque["slug"]))
            familia.append({"name": m["name"], "done": done, "total": len(content.STEPS)})
        meu_progresso = 0
        if destaque:
            meu_progresso = len(progress_for(conn, member["id"], destaque["slug"]))

    return templates.TemplateResponse(request, "index.html", {"member": member, "destaque": destaque,
        "anteriores": anteriores, "familia": familia,
        "meu_progresso": meu_progresso, "total_passos": len(content.STEPS),
    })


# ---------- Trilha da devocional ----------

@app.get("/d/{slug}", response_class=HTMLResponse)
def devocional_trilha(request: Request, slug: str):
    member = auth.require_member(request)
    devo = devo_ou_404(slug)
    with db.session() as conn:
        feitos = progress_for(conn, member["id"], slug)
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


# ---------- Admin ----------

@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    auth.require_admin(request)
    devotionals = content.list_devotionals()
    return templates.TemplateResponse(request, "admin.html", {
        "devotionals": devotionals,
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
        "audio": audio_name,
        "apresentacao": pdf_name,
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    content.get_texto_html.cache_clear()
    content.get_licoes_html.cache_clear()

    return RedirectResponse("/admin?publicado=1", status_code=303)


# ---------- PWA ----------

@app.get("/manifest.json")
def manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")
