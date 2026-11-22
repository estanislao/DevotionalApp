"""Publicação de devocional (M-20): validações, recusas sem resíduo, limites e leitura em blocos."""
import io
import json
import pytest
from urllib.parse import unquote
from tests.conftest import entrar, cadastrar_e_aprovar, mensagem

QUIZ_OK = json.dumps([{"id": "p1", "pergunta": "Q?", "opcoes": ["A", "B"], "correta": "B", "explicacao": "x"}]).encode()


def pdf_real() -> bytes:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument.new()
    doc.new_page(400, 225)
    saida = io.BytesIO()
    doc.save(saida)
    return saida.getvalue()


def publicar(c, titulo="Devocional Nova", data="2026-12-06", audio=(b"ID3" + b"\0" * 5000, "a.mp3"), pdf=None,
             quiz=QUIZ_OK, texto=b"Ola **mundo**", licoes=b"- uma", colecao_id="", versiculo="Jo 1:1"):
    arquivos = {
        "audio": (audio[1], audio[0], "audio/mpeg"),
        "apresentacao": ("s.pdf", pdf if pdf is not None else pdf_real(), "application/pdf"),
        "quiz": ("q.json", quiz, "application/json"),
        "texto": ("t.md", texto, "text/markdown"),
        "licoes": ("l.md", licoes, "text/markdown"),
    }
    return c.post("/admin/publicar", data={"titulo": titulo, "data": data, "versiculo": versiculo, "colecao_id": colecao_id}, files=arquivos)


def pastas(app_mod):
    return sorted(p.name for p in app_mod.content.DEVOCIONAIS_DIR.iterdir())


def test_publicacao_valida_cria_tudo_e_gera_os_slides(app_mod, client):
    entrar(client)
    r = publicar(client)
    assert r.status_code == 303 and r.headers["location"] == "/admin" and "Publicado" in mensagem(client, r)
    slug = "2026-12-06-devocional-nova"
    pasta = app_mod.content.DEVOCIONAIS_DIR / slug
    assert sorted(p.name for p in pasta.iterdir()) == ["apresentacao.pdf", "audio.mp3", "licoes.md", "meta.json", "quiz.json", "slides", "texto.md"]
    assert json.loads((pasta / "meta.json").read_text())["titulo"] == "Devocional Nova"
    assert len(list((pasta / "slides").glob("*.jpg"))) == 1
    assert not [p for p in pastas(app_mod) if p.startswith(".")]                 # nenhuma pasta temporária sobrou
    assert slug in client.get("/").text                                          # aparece no início
    assert client.get(f"/d/{slug}/slide/1").status_code == 200


@pytest.mark.parametrize("nome,kwargs,trecho", [
    ("data_invalida", {"data": "06/12/2026"}, "Data inválida"),
    ("audio_html", {"audio": (b"x" * 100, "a.html")}, "extensão"),
    ("pdf_falso", {"pdf": b"isto nao e um pdf"}, "não parece ser um PDF"),
    ("quiz_invalido", {"quiz": b'[{"id": 1, "pergunta": "Q?", "opcoes": ["A", "B"], "correta": "B"}]'}, "precisa ser um texto"),
    ("quiz_nao_json", {"quiz": b"{quebrado"}, "não é um JSON"),
    ("texto_nao_utf8", {"texto": b"\xff\xfe\x00 invalido"}, "UTF-8"),
    ("titulo_sem_letras", {"titulo": "!!!"}, "pelo menos uma letra"),
])
def test_recusas_nao_deixam_nada_no_disco(app_mod, client, nome, kwargs, trecho):
    entrar(client)
    antes = pastas(app_mod)
    r = publicar(client, **kwargs)
    assert r.status_code == 303 and r.headers["location"] == "/admin", nome
    texto = mensagem(client, r)
    assert "Não publicado" in texto and trecho.lower() in texto.lower(), (nome, texto[:400])
    assert pastas(app_mod) == antes, f"{nome}: sobrou algo no disco: {pastas(app_mod)}"


def test_titulo_e_data_repetidos_sao_recusados_sem_sobrescrever(app_mod, client):
    entrar(client)
    publicar(client)
    meta = (app_mod.content.DEVOCIONAIS_DIR / "2026-12-06-devocional-nova" / "meta.json").read_text()
    r = publicar(client, versiculo="OUTRO")
    assert "Já existe uma devocional" in mensagem(client, r)
    assert (app_mod.content.DEVOCIONAIS_DIR / "2026-12-06-devocional-nova" / "meta.json").read_text() == meta
    assert not [p for p in pastas(app_mod) if p.startswith(".")]


def test_arquivo_acima_do_limite_e_recusado_e_limpo(app_mod, client, monkeypatch):
    entrar(client)
    monkeypatch.setattr(app_mod, "LIMITE_AUDIO", 3000)
    antes = pastas(app_mod)
    r = publicar(client, audio=(b"x" * 5000, "a.mp3"))
    assert "limite" in mensagem(client, r) and pastas(app_mod) == antes
    monkeypatch.setattr(app_mod, "LIMITE_PDF", 100)
    r = publicar(client, audio=(b"x" * 500, "a.mp3"))
    assert "limite" in mensagem(client, r) and pastas(app_mod) == antes


def test_audio_e_pdf_sao_lidos_em_blocos_nunca_inteiros(app_mod, client, monkeypatch):
    from starlette.datastructures import UploadFile
    tamanhos = []
    original = UploadFile.read

    async def espiao(self, size=-1):
        tamanhos.append(size)
        return await original(self, size)
    monkeypatch.setattr(UploadFile, "read", espiao)
    entrar(client)
    publicar(client, audio=(b"x" * (3 * 1024 * 1024 + 17), "a.mp3"))               # 3 MB: precisa de vários blocos
    assert tamanhos and max(tamanhos) <= app_mod.LIMITE_TEXTO + 1, "algum arquivo foi lido de uma vez só"
    assert tamanhos.count(app_mod.BLOCO) >= 4                                       # os blocos de 1 MB do áudio
    assert (app_mod.content.DEVOCIONAIS_DIR / "2026-12-06-devocional-nova" / "audio.mp3").stat().st_size == 3 * 1024 * 1024 + 17


def test_so_admin_publica(app_mod, client):
    admin = entrar(client)
    ana, _ = cadastrar_e_aprovar(app_mod, admin)
    antes = pastas(app_mod)
    assert publicar(ana).status_code == 403
    assert pastas(app_mod) == antes


def test_publicacao_em_andamento_nao_aparece_nas_listas(app_mod):
    from app import content
    tmp = app_mod.content.DEVOCIONAIS_DIR / ".publicando-abc123"
    tmp.mkdir()
    (tmp / "meta.json").write_text(json.dumps({"titulo": "Fantasma", "data": "2026-12-07"}))
    assert all(d["slug"] != ".publicando-abc123" for d in content.list_devotionals())
