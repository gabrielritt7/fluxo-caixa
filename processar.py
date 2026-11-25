"""Uso:  python processar.py ARQUIVO [ARQUIVO...] [--sem-ia]

Lê cada arquivo, mostra o mapeamento de colunas que foi entendido e salva os
lançamentos normalizados em saida/<arquivo>.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fluxocaixa.mapeador import tem_credencial_ia
from fluxocaixa.pipeline import processar

ROTULOS = {"coluna_data": "Data", "coluna_descricao": "Descrição", "coluna_valor": "Valor",
           "coluna_entrada": "Entradas", "coluna_saida": "Saídas", "coluna_tipo": "Tipo",
           "coluna_categoria": "Categoria", "coluna_vencimento": "Vencimento",
           "coluna_status": "Status"}


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(args: list[str]) -> None:
    usar_ia = False if "--sem-ia" in args else None
    arquivos = [a for a in args if not a.startswith("--")]
    if not arquivos:
        print(__doc__)
        return
    if usar_ia is None and not tem_credencial_ia():
        print("(ANTHROPIC_API_KEY não definida: usando mapeamento heurístico)\n")
    Path("saida").mkdir(exist_ok=True)

    for arq in arquivos:
        print("=" * 72)
        print(arq)
        res = processar(arq, usar_ia=usar_ia)
        for t in res.tabelas:
            marca = "✔" if t.usada else "✘ ignorada"
            print(f"\n  [{marca}] {t.origem}  ·  método: {t.mapeamento.metodo}"
                  f"  ·  confiança: {t.mapeamento.confianca}")
            for campo, col in t.mapeamento.colunas_usadas().items():
                print(f"      {ROTULOS[campo]:11} ← {col!r}")
            if t.problemas:
                print(f"      problemas: {'; '.join(t.problemas)}")
            if t.relatorio:
                r = t.relatorio
                print(f"      {r['linhas_lidas']} linhas → {r['lancamentos_validos']} lançamentos "
                      f"(descartadas: {r['descartadas_total']} total/saldo, "
                      f"{r['descartadas_sem_valor']} sem valor, {r['descartadas_sem_data']} sem data)")

        df = res.lancamentos
        if df.empty:
            print("\n  Nenhum lançamento aproveitado.")
            continue
        ent = df.loc[df.tipo == "entrada", "valor"].sum()
        sai = df.loc[df.tipo == "saida", "valor"].sum()
        print(f"\n  {len(df)} lançamentos · {df.data.min():%d/%m/%Y} a {df.data.max():%d/%m/%Y}")
        print(f"  Entradas {brl(ent)} · Saídas {brl(sai)} · Resultado {brl(ent - sai)}")
        destino = Path("saida") / (Path(arq).stem + ".csv")
        df.to_csv(destino, index=False, sep=";", decimal=",", encoding="utf-8-sig")
        print(f"  → {destino}")
    print("=" * 72)


if __name__ == "__main__":
    main(sys.argv[1:])
