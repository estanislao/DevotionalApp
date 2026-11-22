import pytest
from tests.conftest import entrar, cadastro, cadastrar_e_aprovar, novo_cliente, mensagem

SLUG = "2026-11-22-teste"
PROTEGIDAS = ["/", "/colecao/1", f"/d/{SLUG}", f"/d/{SLUG}/ler", f"/d/{SLUG}/slides", f"/d/{SLUG}/slide/1",
              f"/d/{SLUG}/midia/audio.m4a", f"/d/{SLUG}/quiz", "/admin", "/admin/membros", "/admin/colecoes", "/conta/senha"]


# ---------- cabeçalhos ----------
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
                        (f"/d/{SLUG}/praticar/concluir", {}),
                        ("/conta/senha", {"atual": "x", "senha": "y", "senha2": "y"})):
        assert client.post(rota, data=dados).status_code == 303, rota


def test_membro_comum_nao_acessa_admin(app_mod, client):
    admin = entrar(client)
    ana, _ = cadastrar_e_aprovar(app_mod, admin)
    for rota in ("/admin", "/admin/membros", "/admin/colecoes"):
        assert ana.get(rota).status_code == 403, rota


def test_membro_comum_nao_apaga_partilha_de_outro(app_mod, client):
    admin = entrar(client)
    admin.post("/partilhas", data={"texto": "do admin"})
    ana, _ = cadastrar_e_aprovar(app_mod, admin)
    assert ana.post("/partilhas/1/excluir").status_code == 403


def test_conta_pendente_nao_entra(client):
    cadastro(client, "Bia", "bia@teste.com")
    r = client.post("/login", data={"email": "bia@teste.com", "senha": "segredo123"})
    assert r.headers["location"] == "/login" and "ainda não foi aprovada" in mensagem(client, r)


# ---------- login ----------
def test_login_senha_errada_e_bloqueio_do_par_email_ip(client):
    for _ in range(5):
        r = client.post("/login", data={"email": "admin@teste.com", "senha": "errada"})
        assert r.headers["location"] == "/login" and "incorretos" in mensagem(client, r)
    r = client.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"})
    assert "Muitas tentativas" in mensagem(client, r)


def test_login_bloqueio_por_ip_com_emails_diferentes(app_mod, client):
    for i in range(app_mod.auth.LOGIN_IP_MAX):
        client.post("/login", data={"email": f"x{i}@teste.com", "senha": "errada"})
    r = client.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"})
    assert "Muitas tentativas" in mensagem(client, r)


# ---------- cadastro (A-03) ----------
def test_cadastro_isca_nao_cria_conta(client):
    from app import db
    r = cadastro(client, "Robo", "robo@teste.com", site="http://spam")
    assert r.headers["location"] == "/login" and "Inscrição enviada" in mensagem(client, r)   # finge sucesso
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM members WHERE email='robo@teste.com'").fetchone()[0] == 0


def test_cadastro_limite_por_ip(app_mod, client):
    for i in range(app_mod.CADASTRO_POR_IP):
        assert cadastro(client, f"P{i}", f"p{i}@teste.com").headers["location"] == "/login"
    r = cadastro(client, "Extra", "extra@teste.com")
    assert "Muitas inscrições" in mensagem(client, r)


def test_cadastro_fecha_com_muitas_pendentes(app_mod, client, monkeypatch):
    monkeypatch.setattr(app_mod, "CADASTRO_PENDENTES_MAX", 2)
    monkeypatch.setattr(app_mod, "CADASTRO_POR_IP", 99)
    for i in range(2):
        cadastro(client, f"P{i}", f"p{i}@teste.com")
    assert "temporariamente" in mensagem(client, cadastro(client, "Zeca", "z@teste.com"))


def test_cadastro_senha_curta_comum_e_email_duplicado_neutro(client):
    from app import db
    assert "pelo menos" in mensagem(client, cadastro(client, "Cid", "cid@teste.com", senha="123"))
    assert "muito comum" in mensagem(client, cadastro(client, "Cid", "cid@teste.com", senha="12345678"))
    assert cadastro(client, "Cid", "cid@teste.com").headers["location"] == "/login"
    # segundo cadastro com o mesmo e-mail: mesma resposta, nenhuma conta nova (não revela que existe)
    r2 = cadastro(client, "Cid2", "cid@teste.com")
    assert r2.headers["location"] == "/login" and "Inscrição enviada" in mensagem(client, r2)
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM members WHERE email='cid@teste.com'").fetchone()[0] == 1


# ---------- recuperação ----------
def test_esqueci_responde_igual_para_email_existente_ou_nao(app_mod, client, monkeypatch):
    enviados = []
    monkeypatch.setattr(app_mod.mailer, "email_habilitado", lambda: True)
    monkeypatch.setattr(app_mod.mailer, "enviar", lambda para, assunto, texto, html: enviados.append(para))
    a = client.post("/esqueci", data={"email": "admin@teste.com"})
    b = client.post("/esqueci", data={"email": "ninguem@teste.com"})
    assert a.status_code == b.status_code == 303
    assert a.headers["location"].split("?")[0] == b.headers["location"].split("?")[0] == "/esqueci/enviado"
    assert enviados == ["admin@teste.com"]      # só a conta que existe recebe e-mail; a resposta na tela é a mesma
    assert "admin@teste.com" not in a.headers["location"]   # o e-mail inteiro não vai para a URL (só a versão mascarada)


# ---------- admin sem senha no .env ----------
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
    assert r.headers["location"] == "/#partilhas" and "já compartilhou" in mensagem(client, r)
