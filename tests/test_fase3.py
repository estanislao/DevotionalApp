"""Fase 3: conteúdo sem script (M-15), cache por data do arquivo (R-13)."""
import os
import re
from pathlib import Path
import pytest
from tests.conftest import entrar

SLUG = "2026-11-22-teste"
RAIZ = Path(__file__).resolve().parent.parent
PROIBIDO_NO_HTML = re.compile(r"\son(click|submit|change|load|error|input|focus|blur|keydown|keyup|mouse\w+)\s*=|javascript:", re.I)


def blocos_script_inline(html: str):
    return [m for m in re.findall(r"<script\b([^>]*)>", html, flags=re.I) if "src=" not in m]


# ---------- M-15: nenhuma página tem JavaScript embutido ----------
def test_nenhum_template_tem_script_ou_manipulador_inline():
    for arq in sorted((RAIZ / "app" / "templates").glob("*.html")):
        texto = arq.read_text(encoding="utf-8")
        assert not blocos_script_inline(texto), f"{arq.name}: tem <script> embutido"
        assert not PROIBIDO_NO_HTML.search(texto), f"{arq.name}: tem manipulador inline ou javascript:"


def test_csp_nao_permite_script_embutido(client):
    csp = client.get("/login").headers["content-security-policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src and "'self'" in script_src


def test_paginas_renderizadas_so_carregam_o_app_js(client):
    entrar(client)
    for rota in ("/login", "/cadastro?c=convite-teste", "/", f"/d/{SLUG}", f"/d/{SLUG}/ler", f"/d/{SLUG}/ouvir",
                 f"/d/{SLUG}/quiz", f"/d/{SLUG}/praticar", "/admin", "/admin/membros", "/admin/colecoes", "/conta/senha"):
        r = client.get(rota)
        assert r.status_code in (200, 303, 307), (rota, r.status_code)
        if r.status_code == 200:
            assert not blocos_script_inline(r.text), rota
            assert not PROIBIDO_NO_HTML.search(r.text), rota
            assert "/static/app.js" in r.text, rota


def test_app_js_e_servido(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "data-confirm" in r.text and "serviceWorker" in r.text


# ---------- M-15: Markdown sanitizado ----------
MD_HOSTIL = """Texto **forte** e *ênfase*.

<script>alert(1)</script>

<img src=x onerror=alert(1)>

<iframe src="https://evil.example"></iframe>

<div style="position:fixed" onclick="x()">caixa</div>

[clique](javascript:alert(1)) e [seguro](https://exemplo.com/ok) e [e-mail](mailto:a@b.com)

> citação

- item um
- item dois
"""


def test_markdown_remove_o_perigoso_e_mantem_a_formatacao(app_mod):
    from app import content
    html = content.limpar_html(__import__("markdown").markdown(MD_HOSTIL))
    for ruim in ("<script", "alert(1)</script>", "onerror", "<iframe", "onclick", "javascript:", "position:fixed", "<img"):
        assert ruim not in html, ruim
    for bom in ("<strong>forte</strong>", "<em>ênfase</em>", "<blockquote>", "<li>item um</li>", 'href="https://exemplo.com/ok"',
                'href="mailto:a@b.com"', 'rel="noopener noreferrer"'):
        assert bom in html, bom


def test_pagina_de_leitura_e_licoes_saem_sanitizadas(app_mod, client):
    pasta = app_mod.content.DEVOCIONAIS_DIR / SLUG
    (pasta / "texto.md").write_text(MD_HOSTIL, encoding="utf-8")
    (pasta / "licoes.md").write_text("- <img src=x onerror=alert(1)> lição\n", encoding="utf-8")
    entrar(client)
    ler = client.get(f"/d/{SLUG}/ler").text
    assert "<strong>forte</strong>" in ler and "onerror" not in ler and "javascript:" not in ler and "<iframe" not in ler
    praticar = client.get(f"/d/{SLUG}/praticar").text
    assert "lição" in praticar and "onerror" not in praticar


# ---------- R-13: editar o arquivo no servidor aparece sem reiniciar ----------
def test_cache_do_texto_acompanha_a_edicao_do_arquivo(app_mod):
    from app import content
    arq = app_mod.content.DEVOCIONAIS_DIR / SLUG / "texto.md"
    arq.write_text("Versão **um**", encoding="utf-8")
    assert "<strong>um</strong>" in content.get_texto_html(SLUG)
    arq.write_text("Versão **dois**", encoding="utf-8")
    depois = arq.stat().st_mtime_ns + 5_000_000_000            # garante data diferente mesmo em disco com resolução baixa
    os.utime(arq, ns=(depois, depois))
    assert "<strong>dois</strong>" in content.get_texto_html(SLUG)
    assert content.get_texto_html("../etc") == "" and content.get_texto_html("nao-existe") == ""
