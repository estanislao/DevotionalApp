"""Ambiente de teste: banco e pasta de devocionais temporários, sem tocar em dados reais."""
import os
os.environ.update(SESSION_SECRET="s" * 32, COOKIE_SECURE="0", ADMIN_EMAIL="admin@teste.com",
                  ADMIN_PASSWORD="senha-admin-1", BREVO_API_KEY="", MAIL_FROM="", PUBLIC_URL="",
                  CADASTRO_CODIGO="convite-teste", PBKDF2_ITERACOES="1000")
import json
import pytest
from fastapi.testclient import TestClient

CONVITE = "convite-teste"


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    from app import db, main, content
    dev = tmp_path / "devocionais"
    pasta = dev / "2026-11-22-teste"
    pasta.mkdir(parents=True)
    (pasta / "meta.json").write_text(json.dumps({"titulo": "Teste", "data": "2026-11-22", "versiculo": "", "colecao_id": None,
                                                 "audio": "audio.m4a", "apresentacao": "apresentacao.pdf"}), encoding="utf-8")
    (pasta / "quiz.json").write_text(json.dumps([{"id": "p1", "pergunta": "Q?", "opcoes": ["A", "B"], "correta": "B", "explicacao": "x"}]), encoding="utf-8")
    (pasta / "texto.md").write_text("Olá", encoding="utf-8")
    (pasta / "licoes.md").write_text("- uma", encoding="utf-8")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(content, "DEVOCIONAIS_DIR", dev)
    monkeypatch.setattr(main, "DEVOCIONAIS_DIR", dev)
    return main


@pytest.fixture()
def client(app_mod):
    with TestClient(app_mod.app, follow_redirects=False) as c:
        yield c


def novo_cliente(app_mod, ip="testclient"):
    return TestClient(app_mod.app, follow_redirects=False, client=(ip, 50000))


def cadastro(c, nome="Ana", email="ana@teste.com", senha="segredo123", convite=CONVITE, **extra):
    dados = {"nome": nome, "email": email, "senha": senha, "senha2": senha, "convite": convite}
    dados.update(extra)
    return c.post("/cadastro", data=dados)


def entrar(c, email="admin@teste.com", senha="senha-admin-1"):
    r = c.post("/login", data={"email": email, "senha": senha})
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers.get("location")
    return c


def membro_id(email):
    from app import db
    with db.session() as c:
        return c.execute("SELECT id FROM members WHERE email=?", (email,)).fetchone()["id"]


def cadastrar_e_aprovar(app_mod, admin, nome="Ana", email="ana@teste.com", senha="segredo123", ip="testclient"):
    r = cadastro(admin, nome, email, senha)
    assert r.headers["location"] == "/login?ok=cadastro", r.headers["location"]
    mid = membro_id(email)
    admin.post(f"/admin/membros/{mid}/aprovar")
    outro = novo_cliente(app_mod, ip)
    entrar(outro, email, senha)
    return outro, mid
