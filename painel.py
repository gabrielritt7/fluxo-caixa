"""Gera o painel visual (HTML autossuficiente) a partir de uma análise.

  python painel.py saida/analise_trigo_bom.json --nome "Padaria Trigo Bom" [--setor padaria]
                   [--exemplo] [--fragmento] [-o saida/painel.html]

--exemplo    mostra a faixa "dados de demonstração"
--fragmento  sem <!doctype>/<html>/<head>/<body> (para páginas que já têm o esqueleto)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fluxocaixa.painel_html import gerar_html  # noqa: F401  (reexportado)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analise")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--setor")
    ap.add_argument("--exemplo", action="store_true")
    ap.add_argument("--fragmento", action="store_true")
    ap.add_argument("-o", "--saida")
    a = ap.parse_args()
    analise = json.loads(Path(a.analise).read_text(encoding="utf-8"))
    destino = Path(a.saida or Path(a.analise).with_name(
        Path(a.analise).stem.replace("analise_", "painel_") + ".html"))
    destino.write_text(gerar_html(analise, a.nome, a.setor, a.exemplo, a.fragmento), encoding="utf-8")
    print(f"→ {destino}")


if __name__ == "__main__":
    main()
