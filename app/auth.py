"""Login por e-mail + senha, sessão guardada em cookie assinado."""
import os
import time
import hmac
import hashlib
import secrets
import threading
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

SENHA_MIN = 6

# ---------- Senhas (PBKDF2 da biblioteca padrão, sem dependência extra) ----------

_ITERACOES = 200_000


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


# ---------- Proteção contra força bruta ----------
# Após MAX_TENTATIVAS erros seguidos para o mesmo e-mail, o login dele fica
# bloqueado por BLOQUEIO_SEGUNDOS. Contador em memória (zera ao reiniciar).

MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 15 * 60
_tentativas: dict[str, tuple[int, float]] = {}  # email -> (erros, bloqueado_ate)
_lock = threading.Lock()


def bloqueado_por(chave: str) -> int:
    """Segundos restantes de bloqueio (0 se pode tentar)."""
    with _lock:
        _, ate = _tentativas.get(chave, (0, 0.0))
        return max(int(ate - time.time()), 0)


def registrar_erro(chave: str) -> None:
    with _lock:
        erros, ate = _tentativas.get(chave, (0, 0.0))
        erros += 1
        if erros >= MAX_TENTATIVAS:
            _tentativas[chave] = (0, time.time() + BLOQUEIO_SEGUNDOS)
        else:
            _tentativas[chave] = (erros, ate)


def registrar_acerto(chave: str) -> None:
    with _lock:
        _tentativas.pop(chave, None)


# ---------- Sessão ----------

def make_session_cookie(member_id: int) -> str:
    return serializer.dumps({"member_id": member_id})


def read_session_cookie(token: str) -> int | None:
    try:
        data = serializer.loads(token, max_age=MAX_AGE)
        return data.get("member_id")
    except BadSignature:
        return None
    except Exception:
        return None


def current_member(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    member_id = read_session_cookie(token)
    if member_id is None:
        return None
    with db.session() as conn:
        row = conn.execute("SELECT * FROM members WHERE id = ? AND status = 'ativo'", (member_id,)).fetchone()
        return dict(row) if row else None


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
