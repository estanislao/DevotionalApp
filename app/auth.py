"""Login por e-mail + senha, sessão em cookie assinado e limites de uso gravados no SQLite."""
import os
import time
import hmac
import hashlib
import secrets
import functools
from itsdangerous import URLSafeTimedSerializer, BadSignature
from fastapi import Request, HTTPException
from . import db

SECRET = os.environ.get("SESSION_SECRET", "")
if len(SECRET) < 16:
    raise RuntimeError(
        "SESSION_SECRET ausente ou curta demais (mínimo 16 caracteres). "
        "Defina no .env antes de subir o app."
    )
serializer = URLSafeTimedSerializer(SECRET)
COOKIE_NAME = "sessao_devocional"
MAX_AGE = 60 * 60 * 24 * 90  # 90 dias — a família não deve logar toda hora

# Cookie "secure" só funciona em HTTPS. Em produção (atrás do Caddy) fica ligado;
# para testar em http://localhost, exporte COOKIE_SECURE=0.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"

# ---------- Senhas ----------

SENHA_MIN = 8
SENHAS_COMUNS = {
    "12345678", "123456789", "1234567890", "87654321", "11111111", "00000000", "12341234", "123123123",
    "senha123", "senha1234", "senha@123", "minhasenha", "mudar123", "mudar@123", "admin123", "administrador",
    "password", "password1", "password123", "qwertyui", "qwerty123", "qwertyuiop", "asdfghjk", "abcd1234",
    "abc12345", "abcdefgh", "iloveyou", "letmein1", "welcome1", "brasil123", "brasil2026", "flamengo", "corinthians",
    "jesus123", "deus123", "deusefiel", "jesuscristo", "trilhadefe", "trilhadefe1", "trilha123", "familia123",
}


def validar_senha(senha: str) -> str | None:
    """Devolve a mensagem de erro, ou None se a senha serve."""
    if len(senha) < SENHA_MIN:
        return f"A senha precisa ter pelo menos {SENHA_MIN} caracteres."
    if len(senha) > 200:
        return "A senha é longa demais."
    if senha.lower() in SENHAS_COMUNS or len(set(senha)) < 4:
        return "Essa senha é muito comum. Escolha outra."
    return None


# PBKDF2 da biblioteca padrão, sem dependência extra. 600.000 iterações é o valor atual recomendado pela OWASP
# para PBKDF2-HMAC-SHA256. PBKDF2_ITERACOES existe só para acelerar os testes automatizados.
_ITERACOES = int(os.environ.get("PBKDF2_ITERACOES", "600000"))


def hash_senha(senha: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), _ITERACOES).hex()
    return f"pbkdf2${_ITERACOES}${salt}${digest}"


def verificar_senha(senha: str, armazenado: str | None) -> bool:
    if not armazenado:
        return False
    try:
        _, iteracoes, salt, digest = armazenado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), int(iteracoes)).hex()
        return hmac.compare_digest(calc, digest)
    except (ValueError, TypeError):
        return False


def precisa_rehash(armazenado: str | None) -> bool:
    """True se o hash guardado usa menos iterações que o padrão de hoje (atualiza no próximo login)."""
    try:
        return int(armazenado.split("$")[1]) < _ITERACOES
    except (ValueError, TypeError, IndexError, AttributeError):
        return False


@functools.lru_cache(maxsize=1)
def hash_falso() -> str:
    """Hash de uma senha que ninguém usa: serve para gastar o mesmo tempo quando a conta não existe."""
    return hash_senha(secrets.token_hex(8))


# ---------- Limites de uso, gravados no SQLite (sobrevivem a reinício do contêiner) ----------

def eventos_recentes(chave: str, janela_s: int) -> int:
    with db.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM eventos WHERE chave=? AND ts>?",
                            (chave, time.time() - janela_s)).fetchone()[0]


def registrar_evento(chave: str) -> None:
    with db.session() as conn:
        conn.execute("INSERT INTO eventos (chave, ts) VALUES (?,?)", (chave, time.time()))
        conn.execute("DELETE FROM eventos WHERE ts < ?", (time.time() - 24 * 3600,))   # nada vale mais de 1 dia


def permitir(chave: str, maximo: int, janela_s: int) -> bool:
    """Conta uma ocorrência e diz se ainda está dentro do limite."""
    if eventos_recentes(chave, janela_s) >= maximo:
        return False
    registrar_evento(chave)
    return True


def _apagar_eventos(exato: str | None = None, prefixo: str | None = None) -> None:
    with db.session() as conn:
        if exato is not None:
            conn.execute("DELETE FROM eventos WHERE chave=?", (exato,))
        if prefixo is not None:
            seguro = prefixo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conn.execute("DELETE FROM eventos WHERE chave LIKE ? ESCAPE '\\'", (seguro + "%",))


# Erros de senha: o bloqueio vale para o PAR e-mail + IP (5 em 15 min). Assim quem erra de um lugar não tranca
# o dono da conta que entra de outro. Há ainda um teto por IP (20 em 15 min) e um teto alto por e-mail (100 por hora)
# contra tentativa distribuída.
LOGIN_PAR_MAX = 5
LOGIN_IP_MAX = 20
LOGIN_EMAIL_MAX = 100
JANELA_LOGIN = 15 * 60


def login_bloqueado(email: str, ip: str) -> bool:
    return (eventos_recentes(f"login:par:{email}|{ip}", JANELA_LOGIN) >= LOGIN_PAR_MAX
            or eventos_recentes(f"login:ip:{ip}", JANELA_LOGIN) >= LOGIN_IP_MAX
            or eventos_recentes(f"login:email:{email}", 3600) >= LOGIN_EMAIL_MAX)


def login_falhou(email: str, ip: str) -> None:
    registrar_evento(f"login:par:{email}|{ip}")
    registrar_evento(f"login:ip:{ip}")
    registrar_evento(f"login:email:{email}")


def login_ok(email: str, ip: str) -> None:
    _apagar_eventos(exato=f"login:par:{email}|{ip}")


def limpar_login(email: str) -> None:
    """Zera os bloqueios de uma conta (senha trocada ou redefinida pelo admin)."""
    _apagar_eventos(prefixo=f"login:par:{email}|")
    _apagar_eventos(exato=f"login:email:{email}")


# ---------- Sessão ----------
# O cookie leva a "versão de sessão" do membro. Trocar a senha incrementa a versão no banco e
# derruba, de uma vez, todos os cookies emitidos antes.

def make_session_cookie(member_id: int, version: int = 0) -> str:
    return serializer.dumps({"member_id": member_id, "v": version})


def read_session_cookie(token: str) -> tuple[int, int] | None:
    try:
        data = serializer.loads(token, max_age=MAX_AGE)
        return int(data["member_id"]), int(data.get("v", 0))   # cookie antigo, sem "v", vale como versão 0
    except (BadSignature, KeyError, TypeError, ValueError):
        return None
    except Exception:
        return None


def current_member(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    lido = read_session_cookie(token)
    if lido is None:
        return None
    member_id, version = lido
    with db.session() as conn:
        row = conn.execute("SELECT * FROM members WHERE id = ? AND status = 'ativo'", (member_id,)).fetchone()
    if not row or int(row["session_version"]) != version:
        return None
    return dict(row)


def require_member(request: Request) -> dict:
    member = current_member(request)
    if not member:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return member


def require_admin(request: Request) -> dict:
    member = require_member(request)
    if not member["is_admin"]:
        raise HTTPException(status_code=403, detail="Apenas o administrador pode acessar esta página.")
    return member
