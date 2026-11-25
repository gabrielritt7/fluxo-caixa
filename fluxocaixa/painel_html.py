"""Gera o HTML do painel a partir de uma análise (métricas + diagnóstico)."""
from __future__ import annotations

import html
import json
from pathlib import Path

MODELO_HTML = Path(__file__).parent / "painel_modelo.html"


def gerar_html(analise: dict, nome: str, setor: str | None = None,
               exemplo: bool = False, fragmento: bool = False) -> str:
    dados = {"empresa": nome, "setor": setor, "exemplo": exemplo, **analise}
    js = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    corpo = (MODELO_HTML.read_text(encoding="utf-8")
             .replace("{{TITULO}}", html.escape(f"Caixa {nome}"))
             .replace("{{DADOS}}", js))
    if fragmento:
        return corpo
    return ("<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            "</head>\n<body>\n" + corpo + "\n</body>\n</html>\n")
