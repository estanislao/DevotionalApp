"""Login simples por nome + PIN, sessão guardada em cookie assinado."""
import os
import time
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

# Proteção contra força bruta no PIN: após MAX_TENTATIVAS erros seguidos para o
# mesmo membro, o login dele fica bloqueado por BLOQUEIO_SEGUNDOS.
MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 15 * 60
_tentativas: dict[int, tuple[int, float]] = {}  # member_id -> (erros, bloqueado_ate)
_lock = threading.Lock()


def bloqueado_por(member_id: int) -> int:
    """Segundos restantes de bloqueio (0 se pode tentar)."""
    with _lock:
        erros, ate = _tentativas.get(member_id, (0, 0.0))
        restante = int(ate - time.time())
        return max(restante, 0)


def registrar_erro(member_id: int) -> None:
    with _lock:
        erros, ate = _tentativas.get(member_id, (0, 0.0))
        erros += 1
        if erros >= MAX_TENTATIVAS:
            _tentativas[member_id] = (0, time.time() + BLOQUEIO_SEGUNDOS)
        else:
            _tentativas[member_id] = (erros, ate)


def registrar_acerto(member_id: int) -> None:
    with _lock:
        _tentativas.pop(member_id, None)


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
        row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
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
