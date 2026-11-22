"""Fase 1 da auditoria: convite (M-01), sessão (M-07), origem (M-08), tempos (M-09), bloqueio (M-10),
quiz (M-11), senhas (M-12) e admin só na criação (M-13)."""
import hashlib
import pytest
from tests.conftest import entrar, cadastro, cadastrar_e_aprovar, novo_cliente, membro_id, mensagem, CONVITE

SLUG = "2026-11-22-teste"


# ---------- M-01: cadastro por convite ----------
def test_cadastro_fechado_sem_codigo_ou_com_codigo_errado(client):
    from app import db
    assert "Inscrições por convite" in client.get("/cadastro").text
    assert 'name="convite"' not in client.get("/cadastro").text
    assert "Inscrições por convite" in client.get("/cadastro?c=errado").text
    assert cadastro(client, convite="").status_code == 403
    assert cadastro(client, convite="errado").status_code == 403
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM members WHERE email='ana@teste.com'").fetchone()[0] == 0
    aberto = client.get(f"/cadastro?c={CONVITE}").text
    assert 'name="convite"' in aberto and f'value="{CONVITE}"' in aberto


def test_cadastro_fica_fechado_se_o_codigo_nao_estiver_configurado(app_mod, client, monkeypatch):
    monkeypatch.setattr(app_mod, "CADASTRO_CODIGO", "")
    assert "Inscrições por convite" in client.get("/cadastro?c=").text
    assert cadastro(client, convite="").status_code == 403


def test_quem_chuta_codigos_e_freado(app_mod, client):
    for _ in range(app_mod.CONVITE_ERROS_POR_IP):
        cadastro(client, convite="errado")
    assert cadastro(client, convite=CONVITE).status_code == 403     # nem o código certo passa depois do excesso


def test_admin_ve_o_link_de_convite(client):
    entrar(client)
    assert f"/cadastro?c={CONVITE}" in client.get("/admin/membros").text


# ---------- M-07: versão de sessão ----------
def test_admin_redefine_senha_e_derruba_as_sessoes_antigas(app_mod, client):
    admin = entrar(client)
    ana, mid = cadastrar_e_aprovar(app_mod, admin)
    assert ana.get("/").status_code == 200
    admin.post(f"/admin/membros/{mid}/senha", data={"senha": "outra-senha-9"})
    assert ana.get("/").status_code == 307                       # a sessão antiga caiu
    nova = novo_cliente(app_mod)
    entrar(nova, "ana@teste.com", "outra-senha-9")                # entra com a senha nova
    assert nova.get("/").status_code == 200


def test_troca_da_propria_senha_derruba_so_os_outros_aparelhos(app_mod, client):
    admin = entrar(client)
    ana_celular, _ = cadastrar_e_aprovar(app_mod, admin)
    ana_notebook = novo_cliente(app_mod); entrar(ana_notebook, "ana@teste.com", "segredo123")
    r = ana_celular.post("/conta/senha", data={"atual": "segredo123", "senha": "nova-senha-77", "senha2": "nova-senha-77"})
    assert r.status_code == 200 and "Senha trocada" in r.text
    assert ana_celular.get("/").status_code == 200               # quem trocou continua logado
    assert ana_notebook.get("/").status_code == 307              # o outro aparelho caiu


def test_link_de_recuperacao_pendente_morre_quando_a_senha_muda(app_mod, client):
    from app import db
    admin = entrar(client)
    ana, mid = cadastrar_e_aprovar(app_mod, admin)
    tokens = ["token-um-" + "a" * 30, "token-dois-" + "b" * 30]
    with db.session() as c:
        for t in tokens:
            c.execute("INSERT INTO pin_resets (member_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
                      (mid, hashlib.sha256(t.encode()).hexdigest(), "2026-11-22T10:00:00+00:00", "2099-01-01T00:00:00+00:00"))
    anonimo = novo_cliente(app_mod)
    r = anonimo.post(f"/redefinir/{tokens[0]}", data={"senha": "senha-nova-55", "senha2": "senha-nova-55"})
    assert r.status_code == 303                                   # o primeiro link funciona
    assert novo_cliente(app_mod).get(f"/redefinir/{tokens[1]}").status_code == 400   # o segundo, ainda pendente, foi invalidado


def test_redefinir_pelo_link_ja_entra_logado(app_mod, client):
    from app import db
    admin = entrar(client)
    ana, mid = cadastrar_e_aprovar(app_mod, admin)
    t = "token-tres-" + "c" * 30
    with db.session() as c:
        c.execute("INSERT INTO pin_resets (member_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
                  (mid, hashlib.sha256(t.encode()).hexdigest(), "2026-11-22T10:00:00+00:00", "2099-01-01T00:00:00+00:00"))
    novo = novo_cliente(app_mod)
    assert novo.post(f"/redefinir/{t}", data={"senha": "senha-nova-55", "senha2": "senha-nova-55"}).status_code == 303
    assert novo.get("/").status_code == 200                       # recebeu o cookie com a versão nova


def test_cookie_antigo_sem_versao_continua_valendo(app_mod, client):
    from app import auth
    entrar(client)
    antigo = auth.serializer.dumps({"member_id": 1})              # formato anterior a esta mudança
    outro = novo_cliente(app_mod)
    outro.cookies.set(auth.COOKIE_NAME, antigo)
    assert outro.get("/").status_code == 200


# ---------- M-08: origem dos POST ----------
def test_post_de_outro_site_e_recusado(client):
    entrar(client)
    r = client.post("/partilhas", data={"texto": "oi"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/partilhas", data={"texto": "oi"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    from app import db
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 0     # nada foi gravado


def test_post_do_proprio_site_passa(client):
    entrar(client)
    for cab in ({}, {"Origin": "http://testserver"}, {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}):
        assert client.post("/partilhas", data={"texto": "do meu site"}, headers=cab).status_code == 303


def test_origem_usa_public_url_quando_definida(app_mod, client, monkeypatch):
    monkeypatch.setattr(app_mod, "PUBLIC_URL", "https://trilhadefe.app")
    entrar(client)
    assert client.post("/partilhas", data={"texto": "a"}, headers={"Origin": "https://trilhadefe.app"}).status_code == 303
    assert client.post("/partilhas", data={"texto": "b"}, headers={"Origin": "https://trilhadefe.app.evil.com"}).status_code == 403


# ---------- M-09: tempos e respostas uniformes ----------
def test_login_sem_conta_gasta_o_mesmo_hash(app_mod, client, monkeypatch):
    chamadas = []
    original = app_mod.auth.verificar_senha
    monkeypatch.setattr(app_mod.auth, "verificar_senha", lambda s, h: chamadas.append(h) or original(s, h))
    client.post("/login", data={"email": "naoexiste@teste.com", "senha": "qualquer-coisa"})
    assert len(chamadas) == 1 and chamadas[0].startswith("pbkdf2$")      # verificou contra o hash fictício


def test_envio_do_email_de_recuperacao_e_em_segundo_plano(app_mod, client, monkeypatch):
    ordem = []
    monkeypatch.setattr(app_mod.mailer, "email_habilitado", lambda: True)
    monkeypatch.setattr(app_mod.mailer, "enviar", lambda *a: ordem.append("enviou"))
    r = client.post("/esqueci", data={"email": "admin@teste.com"})
    assert r.status_code == 303 and ordem == ["enviou"]      # enviou, mas só depois de montar a resposta
    import inspect
    assert "BackgroundTasks" in inspect.getsource(app_mod.esqueci_enviar)


# ---------- M-10: limites no banco, por par e-mail + IP ----------
def test_quem_erra_de_um_ip_nao_tranca_o_dono_em_outro(app_mod, client):
    atacante = novo_cliente(app_mod, "198.51.100.7")
    for _ in range(5):
        atacante.post("/login", data={"email": "admin@teste.com", "senha": "chute"})
    assert "Muitas tentativas" in mensagem(atacante, atacante.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"}))
    dono = novo_cliente(app_mod, "203.0.113.20")
    entrar(dono)                                               # o dono, de outro IP, entra normalmente


def test_o_bloqueio_sobrevive_a_reinicio(app_mod, client):
    for _ in range(5):
        client.post("/login", data={"email": "admin@teste.com", "senha": "errada"})
    from fastapi.testclient import TestClient
    with TestClient(app_mod.app, follow_redirects=False) as depois_do_reinicio:   # sobe o app de novo, mesmo banco
        r = depois_do_reinicio.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"})
        assert "Muitas tentativas" in mensagem(depois_do_reinicio, r)


def test_login_certo_zera_o_contador_do_par(client):
    for _ in range(4):
        client.post("/login", data={"email": "admin@teste.com", "senha": "errada"})
    entrar(client)
    for _ in range(4):
        client.post("/login", data={"email": "admin@teste.com", "senha": "errada"})
    assert client.post("/login", data={"email": "admin@teste.com", "senha": "senha-admin-1"}).headers["location"] == "/"


# ---------- M-11: quiz ----------
def test_quiz_a_primeira_resposta_vale(client):
    from app import db
    entrar(client)
    client.post(f"/d/{SLUG}/quiz/responder", data={"question_id": "p1", "escolha": "A"})
    client.post(f"/d/{SLUG}/quiz/responder", data={"question_id": "p1", "escolha": "B"})
    with db.session() as c:
        linha = c.execute("SELECT chosen, is_correct FROM quiz_answers").fetchone()
        assert (linha["chosen"], linha["is_correct"]) == ("A", 0)


@pytest.mark.parametrize("quiz", [
    b'[{"id": 1, "pergunta": "Q?", "opcoes": ["A", "B"], "correta": "B"}]',
    b'[{"id": "p1", "pergunta": "Q?", "opcoes": [1, 2], "correta": 2}]',
    b'[{"id": "p1", "pergunta": "Q?", "opcoes": ["A", ""], "correta": "A"}]',
])
def test_quiz_com_tipos_errados_e_recusado_na_validacao(quiz):
    from app import content
    with pytest.raises(ValueError):
        content.validar_quiz(quiz)


# ---------- M-12: senhas ----------
@pytest.mark.parametrize("senha,quebra", [("curta1", True), ("12345678", True), ("aaaaaaaaa", True), ("Password", True),
                                           ("trilhadefe", True), ("rio-monte-graca-93", False), ("cafe com leite 2026", False)])
def test_validacao_de_senha(senha, quebra):
    from app import auth
    assert (auth.validar_senha(senha) is not None) == quebra


def test_hash_antigo_e_atualizado_no_proximo_login(app_mod, client):
    from app import db, auth
    salt = "ab" * 16
    antigo = f"pbkdf2$500${salt}${hashlib.pbkdf2_hmac('sha256', b'senha-antiga-1', bytes.fromhex(salt), 500).hex()}"
    with db.session() as c:
        c.execute("UPDATE members SET password_hash=? WHERE email='admin@teste.com'", (antigo,))
    assert auth.precisa_rehash(antigo)
    entrar(client, "admin@teste.com", "senha-antiga-1")           # o hash antigo ainda confere
    with db.session() as c:
        novo = c.execute("SELECT password_hash FROM members WHERE email='admin@teste.com'").fetchone()[0]
    assert novo.split("$")[1] == str(auth._ITERACOES) and not auth.precisa_rehash(novo)
    entrar(novo_cliente(app_mod), "admin@teste.com", "senha-antiga-1")   # e a mesma senha segue valendo


def test_tela_minha_conta(app_mod, client):
    entrar(client)
    assert client.get("/conta/senha").status_code == 200
    assert client.post("/conta/senha", data={"atual": "errada-1234", "senha": "nova-senha-77", "senha2": "nova-senha-77"}).status_code == 400
    assert client.post("/conta/senha", data={"atual": "senha-admin-1", "senha": "nova-senha-77", "senha2": "diferente-88"}).status_code == 400
    assert client.post("/conta/senha", data={"atual": "senha-admin-1", "senha": "12345678", "senha2": "12345678"}).status_code == 400
    assert client.post("/conta/senha", data={"atual": "senha-admin-1", "senha": "nova-senha-77", "senha2": "nova-senha-77"}).status_code == 200
    entrar(novo_cliente(app_mod), "admin@teste.com", "nova-senha-77")


# ---------- M-13: admin só na criação ----------
def test_inscricao_pendente_nao_vira_admin_no_reinicio(app_mod, client, monkeypatch):
    from app import db
    cadastro(client, "Intruso", "intruso@teste.com", "minha-senha-12")
    monkeypatch.setenv("ADMIN_EMAIL", "intruso@teste.com"); monkeypatch.setenv("ADMIN_PASSWORD", "outra-senha-34")
    db.init_db(app_mod.ler_admin_env())                         # simula o reinício do servidor com aquele ADMIN_EMAIL
    with db.session() as c:
        linha = c.execute("SELECT is_admin, status FROM members WHERE email='intruso@teste.com'").fetchone()
        assert (linha["is_admin"], linha["status"]) == (0, "pendente")
    r = client.post("/login", data={"email": "intruso@teste.com", "senha": "outra-senha-34"})
    assert "incorretos" in mensagem(client, r)                  # a senha do .env não vira a senha dele


def test_custo_da_senha_errada_e_igual_com_hash_antigo_e_sem_conta(app_mod, client, monkeypatch):
    """R-03: o total de iterações gastas não pode depender de a conta existir nem de o hash ser antigo."""
    from app import db, auth
    salt = "cd" * 16
    antigo = f"pbkdf2$500${salt}${hashlib.pbkdf2_hmac('sha256', b'senha-antiga-1', bytes.fromhex(salt), 500).hex()}"
    with db.session() as c:
        c.execute("UPDATE members SET password_hash=? WHERE email='admin@teste.com'", (antigo,))
    auth.hash_falso()                                            # o hash fictício é calculado uma vez e guardado
    gastas = []
    original = hashlib.pbkdf2_hmac
    monkeypatch.setattr(auth.hashlib, "pbkdf2_hmac", lambda alg, pw, salt, it, *a, **k: gastas.append(it) or original(alg, pw, salt, it, *a, **k))
    client.post("/login", data={"email": "admin@teste.com", "senha": "errada-123"})
    com_conta = sum(gastas)
    gastas.clear()
    client.post("/login", data={"email": "ninguem@teste.com", "senha": "errada-123"})
    sem_conta = sum(gastas)
    assert com_conta == sem_conta == auth._ITERACOES
