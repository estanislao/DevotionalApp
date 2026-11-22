import pytest
from tests.conftest import entrar, cadastrar_e_aprovar

SLUG = "2026-11-22-teste"
PROTEGIDAS = ["/", "/colecao/1", f"/d/{SLUG}", f"/d/{SLUG}/ler", f"/d/{SLUG}/slides", f"/d/{SLUG}/slide/1",
              f"/d/{SLUG}/midia/audio.m4a", f"/d/{SLUG}/quiz", "/admin", "/admin/membros", "/admin/colecoes"]


# ---------- cabeçalhos (A-02) ----------
def test_cabecalhos_de_seguranca_em_todas_as_respostas(client):
    for caminho in ("/login", "/cadastro", "/nao-existe", "/static/css/style.css"):
        h = client.get(caminho).headers
        assert h["x-content-type-options"] == "nosniff", caminho
        assert h["x-frame-options"] == "SAMEORIGIN", caminho
        assert "frame-ancestors 'self'" in h["content-security-policy"], caminho
        assert h["referrer-policy"] == "strict-origin-when-cross-origin", caminho
        assert "camera=()" in h["permissions-policy"], caminho


def test_hsts_so_com_cookie_secure(app_mod, monkeypatch):
    from fastapi.testclient import TestClient
    from app import auth
    monkeypatch.setattr(auth, "COOKIE_SECURE", True)
    with TestClient(app_mod.app, follow_redirects=False) as c:
        assert "max-age=31536000" in c.get("/login").headers["strict-transport-security"]
    monkeypatch.setattr(auth, "COOKIE_SECURE", False)
    with TestClient(app_mod.app, follow_redirects=False) as c:
        assert "strict-transport-security" not in c.get("/login").headers


# ---------- autorização ----------
@pytest.mark.parametrize("rota", PROTEGIDAS)
def test_rotas_protegidas_redirecionam_para_login(client, rota):
    r = client.get(rota)
    assert r.status_code in (303, 307) and r.headers["location"].endswith("/login")


def test_acoes_que_gravam_exigem_login(client):
    for rota, dados in (("/partilhas", {"texto": "oi"}), ("/admin/colecoes", {"nome": "x"}), ("/admin/membros/1/aprovar", {}),
                        (f"/d/{SLUG}/praticar/concluir", {})):
        assert client.post(rota, data=dados).status_code == 303, rota


def test_membro_comum_nao_acessa_admin(app_mod, client):
    admin = entrar(client)
    ana, _ = cadastrar_e_aprovar(app_mod, admin)
    for rota in ("/admin", "/admin/membros", "/admin/colecoes"):
        assert ana.get(rota).status_code == 403, rota


def test_conta_pendente_nao_entra(client):
    client.post("/cadastro", data={"nome": "Bia", "email": "bia@teste.com", "senha": "segredo123", "senha2": "segredo123"})
    r = client.post("/login", data={"email": "bia@teste.com", "senha": "segredo123"})
    assert r.headers["location"] == "/login?erro=pendente"


# ---------- login ----------
def test_login_senha_errada_e_bloqueio_por_email(client):
    for _ in range(5):
        assert client.post("/login", data={"email": "admin@teste.com", "senha": "errada"}).headers["location"].startswith("/login?erro=1")
    r = client.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"})
    assert r.headers["location"] == "/login?erro=bloqueado"


def test_login_bloqueio_por_ip_com_emails_diferentes(app_mod, client):
    for i in range(app_mod.LOGIN_FALHAS_POR_IP):
        client.post("/login", data={"email": f"x{i}@teste.com", "senha": "errada"})
    r = client.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"})
    assert r.headers["location"] == "/login?erro=bloqueado"


# ---------- cadastro (A-03) ----------
def test_cadastro_isca_nao_cria_conta(client):
    from app import db
    r = client.post("/cadastro", data={"nome": "Robo", "email": "robo@teste.com", "senha": "segredo123", "senha2": "segredo123", "site": "http://spam"})
    assert r.headers["location"] == "/login?ok=cadastro"   # finge sucesso
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM members WHERE email='robo@teste.com'").fetchone()[0] == 0


def test_cadastro_limite_por_ip(app_mod, client):
    for i in range(app_mod.CADASTRO_POR_IP):
        r = client.post("/cadastro", data={"nome": f"P{i}", "email": f"p{i}@teste.com", "senha": "segredo123", "senha2": "segredo123"})
        assert r.headers["location"] == "/login?ok=cadastro"
    r = client.post("/cadastro", data={"nome": "Extra", "email": "extra@teste.com", "senha": "segredo123", "senha2": "segredo123"})
    assert "Muitas+inscri" in r.headers["location"] or "Muitas%20inscri" in r.headers["location"]


def test_cadastro_fecha_com_muitas_pendentes(app_mod, client, monkeypatch):
    monkeypatch.setattr(app_mod, "CADASTRO_PENDENTES_MAX", 2)
    monkeypatch.setattr(app_mod, "CADASTRO_POR_IP", 99)
    for i in range(2):
        client.post("/cadastro", data={"nome": f"P{i}", "email": f"p{i}@teste.com", "senha": "segredo123", "senha2": "segredo123"})
    r = client.post("/cadastro", data={"nome": "Zeca", "email": "z@teste.com", "senha": "segredo123", "senha2": "segredo123"})
    assert "temporariamente" in r.headers["location"]


def test_cadastro_senha_curta_e_email_duplicado(client):
    r = client.post("/cadastro", data={"nome": "Cid", "email": "cid@teste.com", "senha": "123", "senha2": "123"})
    assert "pelo%20menos" in r.headers["location"]
    ok = {"nome": "Cid", "email": "cid@teste.com", "senha": "segredo123", "senha2": "segredo123"}
    assert client.post("/cadastro", data=ok).headers["location"] == "/login?ok=cadastro"
    assert "J%C3%A1%20existe" in client.post("/cadastro", data=ok).headers["location"]


# ---------- recuperação (A-03/A-07) ----------
def test_esqueci_responde_igual_para_email_existente_ou_nao(client):
    a = client.post("/esqueci", data={"email": "admin@teste.com"})
    b = client.post("/esqueci", data={"email": "ninguem@teste.com"})
    assert a.status_code == b.status_code == 303
    assert a.headers["location"].startswith("/esqueci") and b.headers["location"].startswith("/esqueci")


# ---------- admin sem senha no .env (A-06) ----------
def test_boot_sem_admin_password_nao_falha(app_mod, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert app_mod.ler_admin_env() is None


# ---------- quiz e mídia ----------
def test_quiz_resposta_forjada_e_resposta_certa(client):
    entrar(client)
    assert client.post(f"/d/{SLUG}/quiz/responder", data={"question_id": "p1", "escolha": "Z"}).status_code == 400
    r = client.post(f"/d/{SLUG}/quiz/responder", data={"question_id": "p1", "escolha": "B"})
    assert r.status_code == 303
    from app import db
    with db.session() as c:
        assert c.execute("SELECT is_correct FROM quiz_answers").fetchone()[0] == 1
        assert c.execute("SELECT step FROM progress WHERE step='quiz'").fetchone() is not None


def test_midia_so_serve_arquivos_do_meta(client):
    entrar(client)
    for arq in ("quiz.json", "meta.json", "texto.md"):
        assert client.get(f"/d/{SLUG}/midia/{arq}").status_code == 404, arq
    assert client.get(f"/d/{SLUG}/slide/0").status_code == 404
    assert client.get(f"/d/{SLUG}/slide/99").status_code == 404


def test_partilha_escapa_html_e_limita_por_dia(client):
    entrar(client)
    client.post("/partilhas", data={"texto": "<script>alert(1)</script> oi"})
    html = client.get("/").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    client.post("/partilhas", data={"texto": "segunda"}); client.post("/partilhas", data={"texto": "terceira"})
    r = client.post("/partilhas", data={"texto": "quarta"})
    assert "partilha_erro" in r.headers["location"]
