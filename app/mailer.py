"""Envio de e-mail pela API da Brevo (sem dependência extra, só urllib)."""
import os
import json
import urllib.request
import urllib.error

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Trilha de Fé").strip()
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def email_habilitado() -> bool:
    return bool(BREVO_API_KEY and MAIL_FROM)


def enviar(para: str, assunto: str, texto: str, html: str) -> None:
    """Levanta RuntimeError com mensagem curta se o envio falhar."""
    if not email_habilitado():
        raise RuntimeError("Envio de e-mail não está configurado (BREVO_API_KEY / MAIL_FROM).")
    payload = {
        "sender": {"email": MAIL_FROM, "name": MAIL_FROM_NAME},
        "to": [{"email": para}],
        "subject": assunto,
        "textContent": texto,
        "htmlContent": html,
    }
    req = urllib.request.Request(
        BREVO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo respondeu {resp.status}.")
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"Brevo recusou o envio ({e.code}): {corpo}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Não foi possível falar com a Brevo: {e.reason}")
