"""R-12 (nada do usuário na URL), recados de uma vez só, aviso de privacidade (A-12), registro de ações (A-15)."""
import time
import pytest
from tests.conftest import entrar, cadastro, cadastrar_e_aprovar, novo_cliente, mensagem, membro_id, CONVITE
from tests.test_upload import publicar

FRASE_FALSA = "Sua conta foi suspensa, ligue para 0800"


# ---------- R-12: nada do usuário viaja na URL ----------
def test_login_com_erro_nao_poe_o_email_na_url_mas_preenche_o_campo(client):
    r = client.post("/login", data={"email": "ana.secreta@teste.com", "senha": "errada-123"})
    assert r.headers["location"] == "/login"                          # sem e-mail, sem texto
    assert "ana.secreta@teste.com" in client.get("/login").text       # e o campo continua preenchido


def test_cadastro_com_erro_nao_poe_nome_nem_email_na_url(client):
    r = cadastro(client, "Maria Silva", "maria@teste.com", senha="123")
    assert r.headers["location"] == f"/cadastro?c={CONVITE}"
    pagina = client.get(r.headers["location"]).text
    assert "Maria Silva" in pagina and "maria@teste.com" in pagina and "pelo menos" in pagina


def test_recuperacao_com_erro_nao_poe_o_email_na_url(app_mod, client, monkeypatch):
    monkeypatch.setattr(app_mod.mailer, "email_habilitado", lambda: True)
    r = client.post("/esqueci", data={"email": "invalido@"})
    assert r.headers["location"] == "/esqueci"
    assert "invalido@" in client.get("/esqueci").text


def test_mensagem_do_mural_e_do_admin_tambem_nao_vao_na_url(app_mod, client):
    entrar(client)
    r = client.post("/partilhas", data={"texto": "a"})
    assert r.headers["location"] == "/#partilhas"
    r = client.post("/admin/colecoes", data={"nome": "x"})
    assert r.headers["location"] == "/admin/colecoes"


# ---------- o recado é de uma vez só ----------
def test_recado_aparece_uma_vez_e_some(client):
    r = client.post("/login", data={"email": "a@teste.com", "senha": "errada-123"})
    primeiro = client.get(r.headers["location"]).text
    assert "incorretos" in primeiro
    assert "incorretos" not in client.get("/login").text            # na segunda visita já não existe
    assert "recado" not in client.cookies                           # e o cookie foi apagado


# ---------- link forjado não consegue mostrar texto na tela de ninguém ----------
@pytest.mark.parametrize("rota", [
    f"/cadastro?c={CONVITE}&erro={FRASE_FALSA}&ok={FRASE_FALSA}",
    f"/login?erro={FRASE_FALSA}&ok={FRASE_FALSA}&email=x@y.com",
    f"/esqueci?erro={FRASE_FALSA}",
    f"/?erro={FRASE_FALSA}&partilha_erro={FRASE_FALSA}",
    f"/admin?erro={FRASE_FALSA}&publicado=1",
    f"/admin/membros?ok={FRASE_FALSA}&erro={FRASE_FALSA}",
    f"/admin/colecoes?ok={FRASE_FALSA}",
])
def test_link_forjado_nao_mostra_mensagem(client, rota):
    entrar(client)
    html = client.get(rota).text
    assert FRASE_FALSA not in html and "suspensa" not in html


def test_recado_adulterado_ou_de_outra_origem_e_ignorado(app_mod, client):
    from app import auth
    client.cookies.set("recado", "lixo-qualquer")
    assert "lixo" not in client.get("/login").text
    forjado = auth.serializer.dumps({"t": "erro", "m": FRASE_FALSA, "d": {}})      # assinado com outro "sal"
    client.cookies.set("recado", forjado)
    assert FRASE_FALSA not in client.get("/login").text


def test_recado_vencido_e_ignorado(app_mod, client, monkeypatch):
    token = app_mod._recados.dumps({"t": "erro", "m": "mensagem antiga", "d": {}})
    client.cookies.set("recado", token)
    assert "mensagem antiga" in client.get("/login").text           # dentro do prazo aparece
    client.cookies.set("recado", token)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 600)         # 10 minutos depois
    assert "mensagem antiga" not in client.get("/login").text


# ---------- A-12: aviso de privacidade ----------
def test_cadastro_tem_aviso_de_privacidade(client):
    html = client.get(f"/cadastro?c={CONVITE}").text
    assert "Como tratamos seus dados" in html and "administrador" in html and "Brevo" in html


# ---------- A-15: registro das ações do administrador ----------
def acoes(client):
    from app import db
    with db.session() as c:
        return [(r["acao"], r["detalhe"]) for r in c.execute("SELECT acao, detalhe FROM admin_log ORDER BY id")]


def test_acoes_do_admin_ficam_registradas(app_mod, client):
    admin = entrar(client)
    cadastro(admin, "Bia", "bia@teste.com")
    cadastro(admin, "Caio", "caio@teste.com")
    bia, caio = membro_id("bia@teste.com"), membro_id("caio@teste.com")
    admin.post(f"/admin/membros/{bia}/aprovar")
    admin.post(f"/admin/membros/{bia}/senha", data={"senha": "outra-senha-9x"})
    admin.post(f"/admin/membros/{caio}/excluir")                      # pendente: é uma recusa
    admin.post(f"/admin/membros/{bia}/excluir")                       # ativo: é uma exclusão
    admin.post("/admin/colecoes", data={"nome": "Série A", "descricao": ""})
    admin.post("/admin/colecoes/1/editar", data={"nome": "Série B", "descricao": ""})
    publicar(admin)
    admin.post("/admin/colecoes/1/excluir")
    registro = acoes(admin)
    assert [a for a, _ in registro] == ["aprovou inscrição", "redefiniu a senha de", "recusou inscrição", "excluiu membro",
                                        "criou coleção", "editou coleção", "publicou devocional", "excluiu coleção"]
    assert registro[0][1] == "Bia (bia@teste.com)" and registro[6][1] == "2026-12-06-devocional-nova"
    assert "outra-senha-9x" not in str(registro)                      # a senha nunca vai para o registro


def test_pagina_do_registro_so_para_admin_e_lista_as_acoes(app_mod, client):
    admin = entrar(client)
    admin.post("/admin/colecoes", data={"nome": "Minha série", "descricao": ""})
    html = admin.get("/admin/registro").text
    assert "criou coleção" in html and "Minha série" in html and "Administrador" in html
    ana, _ = cadastrar_e_aprovar(app_mod, admin)
    assert ana.get("/admin/registro").status_code == 403
    assert novo_cliente(app_mod).get("/admin/registro").status_code in (303, 307)


def test_acao_recusada_nao_registra(app_mod, client):
    admin = entrar(client)
    admin.post(f"/admin/membros/{membro_id('admin@teste.com')}/excluir")   # o admin não exclui a si mesmo
    assert acoes(admin) == []


def test_registro_guarda_so_as_ultimas_1000(app_mod, client):
    from app import db
    entrar(client)
    admin = {"id": 1, "name": "Administrador"}
    with db.session() as c:
        for i in range(1005):
            app_mod.registrar_acao(c, admin, "acao", f"n{i}")
    with db.session() as c:
        n, primeiro = c.execute("SELECT COUNT(*), MIN(detalhe) FROM admin_log").fetchone()
        assert n == 1000
        assert c.execute("SELECT COUNT(*) FROM admin_log WHERE detalhe='n0'").fetchone()[0] == 0     # as mais antigas saíram
