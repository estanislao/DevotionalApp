"""Lê as devocionais da pasta devocionais/<slug>/ (meta.json, texto.md, quiz.json, licoes.md)."""
import json
import re
from pathlib import Path
from functools import lru_cache
import markdown as md

DEVOCIONAIS_DIR = Path(__file__).parent.parent / "devocionais"

STEPS = ["slides", "ouvir", "ler", "quiz", "praticar"]

# Para o progresso contam 3 atividades: Conteúdo (basta UM entre slides, ouvir ou ler), Quiz e Praticar.
CONTEUDO = ["slides", "ouvir", "ler"]
TOTAL_ATIVIDADES = 3


def atividades_feitas(steps) -> int:
    return int(any(s in steps for s in CONTEUDO)) + int("quiz" in steps) + int("praticar" in steps)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def slug_valido(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def list_devotionals() -> list[dict]:
    """Retorna todas as devocionais publicadas, mais recente primeiro."""
    if not DEVOCIONAIS_DIR.exists():
        return []
    items = []
    for folder in sorted(DEVOCIONAIS_DIR.iterdir(), reverse=True):
        meta_path = folder / "meta.json"
        if not (folder.is_dir() and meta_path.exists()):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # pasta quebrada não derruba a tela inicial
        meta["slug"] = folder.name
        items.append(meta)
    return items


def list_by_collection(colecao_id: int) -> list[dict]:
    return [d for d in list_devotionals() if d.get("colecao_id") == colecao_id]


def count_by_collection() -> dict[int, int]:
    contagem: dict[int, int] = {}
    for d in list_devotionals():
        cid = d.get("colecao_id")
        if cid is not None:
            contagem[cid] = contagem.get(cid, 0) + 1
    return contagem


def get_devotional(slug: str) -> dict | None:
    if not slug_valido(slug):
        return None
    meta_path = DEVOCIONAIS_DIR / slug / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    meta["slug"] = slug
    return meta


@lru_cache(maxsize=64)
def get_texto_html(slug: str) -> str:
    path = DEVOCIONAIS_DIR / slug / "texto.md"
    if not path.exists():
        return ""
    return md.markdown(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def get_licoes_html(slug: str) -> str:
    path = DEVOCIONAIS_DIR / slug / "licoes.md"
    if not path.exists():
        return ""
    return md.markdown(path.read_text(encoding="utf-8"))


def validar_quiz(raw: bytes) -> list[dict]:
    """Confere o formato do quiz.json. Levanta ValueError com mensagem legível."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"quiz.json não é um JSON válido: {e}")
    if not isinstance(data, list) or not data:
        raise ValueError("quiz.json precisa ser uma lista com pelo menos uma pergunta.")
    ids = set()
    for i, p in enumerate(data, 1):
        if not isinstance(p, dict):
            raise ValueError(f"Pergunta {i} não é um objeto.")
        for campo in ("id", "pergunta", "opcoes", "correta"):
            if campo not in p:
                raise ValueError(f"Pergunta {i} está sem o campo '{campo}'.")
        if not isinstance(p["opcoes"], list) or len(p["opcoes"]) < 2:
            raise ValueError(f"Pergunta {i} precisa de pelo menos 2 opções.")
        if p["correta"] not in p["opcoes"]:
            raise ValueError(f"Pergunta {i}: 'correta' não está entre as opções.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(p["id"])):
            raise ValueError(f"Pergunta {i}: 'id' deve ter só letras, números, '-' ou '_'.")
        if p["id"] in ids:
            raise ValueError(f"Pergunta {i}: id '{p['id']}' repetido.")
        ids.add(p["id"])
    return data


def get_quiz(slug: str) -> list[dict]:
    path = DEVOCIONAIS_DIR / slug / "quiz.json"
    if not path.exists():
        return []
    try:
        return validar_quiz(path.read_bytes())
    except ValueError:
        return []  # quiz quebrado aparece como "não publicado" em vez de erro 500


def has_file(slug: str, filename: str) -> bool:
    return (DEVOCIONAIS_DIR / slug / filename).is_file()


def media_path(devo: dict, filename: str) -> Path | None:
    """Só serve os arquivos declarados no meta.json (áudio e apresentação)."""
    permitidos = {devo.get("audio"), devo.get("apresentacao")} - {None, ""}
    if filename not in permitidos:
        return None
    path = DEVOCIONAIS_DIR / devo["slug"] / filename
    return path if path.is_file() else None
