"""Regressão dos achados R-01 (XSS pelo nome), N-02 (HTML nos e-mails) e R-02 (corpo gigante)."""
from tests.conftest import entrar, cadastro


def cadastrar(c, nome, email, senha="segredo123"):
    return cadastro(c, nome, email, senha)


def test_r01_nome_com_apostrofo_nao_vira_javascript(client):
    nome = "x');alert(1);('"
    assert cadastrar(client, nome, "xss@teste.com").headers["location"] == "/login?ok=cadastro"
    entrar(client)
    html = client.get("/admin/membros").text
    assert nome not in html                                    # nunca aparece cru
    assert "confirm('Recusar" not in html and "confirm('Excluir" not in html
    assert 'data-confirm="Recusar e apagar a inscrição de x&#39;);alert(1);(&#39;?"' in html and 'onsubmit=' not in html


def test_r01_nome_de_colecao_tambem_fica_fora_do_javascript(client):
    entrar(client)
    client.post("/admin/colecoes", data={"nome": "c');alert(1);('", "descricao": ""})
    html = client.get("/admin/colecoes").text
    assert "confirm('Excluir a cole" not in html and "c');alert(1);('" not in html
    assert 'data-confirm="Excluir a coleção c&#39;);alert(1);(&#39;?"' in html and 'onclick=' not in html


def test_n02_email_com_html_e_recusado(client):
    r = cadastrar(client, "Ana", "<a/href=https://evil.example>clique</a>@a.co")
    assert "E-mail%20inv%C3%A1lido" in r.headers["location"]
    r = cadastrar(client, "Ana", "a" * 250 + "@teste.com")           # passa de 254 caracteres
    assert "E-mail%20inv%C3%A1lido" in r.headers["location"]


def test_n02_nome_com_html_sai_escapado_no_email_ao_admin(app_mod, client, monkeypatch):
    enviados = []
    monkeypatch.setattr(app_mod.mailer, "email_habilitado", lambda: True)
    monkeypatch.setattr(app_mod.mailer, "enviar", lambda para, assunto, texto, html: enviados.append((para, html)))
    cadastrar(client, "<img src=x onerror=alert(1)>", "ana@teste.com")
    assert enviados, "o admin deveria ter sido avisado"
    html = enviados[0][1]
    assert "<img" not in html and "&lt;img" in html


def test_r02_corpo_grande_e_recusado_fora_da_publicacao(client):
    r = client.post("/login", data={"email": "a@b.com", "senha": "x"}, headers={"content-length": str(5 * 1024 * 1024)})
    assert r.status_code == 413
    assert r.headers["x-content-type-options"] == "nosniff"     # a resposta de erro também leva os cabeçalhos
    entrar(client)
    r = client.post("/admin/publicar", data={"titulo": "x"}, headers={"content-length": str(5 * 1024 * 1024)})
    assert r.status_code != 413                                 # /admin/publicar aceita arquivos grandes
