"""Análise completa de caixa de uma empresa.

  python analisar.py ARQUIVO [ARQUIVO...] [--saldo 15000] [--hoje 30/06/2025]
                     [--empresa padaria] [--nome "Padaria X"] [--setor "padaria"] [--sem-ia]

Etapas: ler → mapear colunas → normalizar → classificar → métricas → diagnóstico.
As regras de classificação aprendidas ficam em dados/regras_<empresa>.json.
O resultado completo vai para saida/analise_<empresa>.json.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from fluxocaixa.classificador import RegrasEmpresa, classificar
from fluxocaixa.diagnostico import brl, diagnosticar, numeros_inventados
from fluxocaixa.mapeador import tem_credencial_ia
from fluxocaixa.metricas import calcular
from fluxocaixa.normalizador import parse_data, parse_valor
from fluxocaixa.pipeline import processar
from painel import gerar_html

def dbr(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


ICONE = {"critico": "🔴", "atencao": "🟡", "info": "🔵", "saudavel": "🟢"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--saldo", help="saldo bancário no início dos dados (ex.: 15000 ou 15.000,00)")
    ap.add_argument("--hoje", help="data de referência dd/mm/aaaa (padrão: último lançamento pago)")
    ap.add_argument("--empresa", default="minha_empresa")
    ap.add_argument("--setor")
    ap.add_argument("--nome", help="nome da empresa no painel (padrão: --empresa)")
    ap.add_argument("--sem-ia", action="store_true")
    a = ap.parse_args()

    usar_ia = False if a.sem_ia else tem_credencial_ia()
    print(f"Modo: {'IA (Claude)' if usar_ia else 'sem IA (heurística + regras + texto-modelo)'}\n")

    # 1-3: leitura, mapeamento, normalização
    partes = []
    for arq in a.arquivos:
        res = processar(arq, usar_ia=usar_ia)
        for t in res.tabelas:
            print(f"  {'✔' if t.usada else '✘'} {t.origem} ({t.mapeamento.metodo})"
                  + (f" — {'; '.join(t.problemas)}" if t.problemas else ""))
        partes.append(res.lancamentos)
    df = pd.concat(partes, ignore_index=True)
    if df.empty:
        print("Nenhum lançamento aproveitado.")
        return

    # 4: classificação (com memória por empresa)
    caminho_regras = Path("dados") / f"regras_{a.empresa}.json"
    regras = RegrasEmpresa.carregar(caminho_regras)
    df, rel = classificar(df, regras, usar_ia=usar_ia, setor=a.setor)
    regras.salvar(caminho_regras)
    print(f"\n  {len(df)} lançamentos classificados: "
          + ", ".join(f"{k}={v}" for k, v in rel["por_fonte"].items())
          + (f"  ·  {rel['descricoes_para_ia']} descrições únicas em {rel['chamadas_ia']} chamada(s) à IA"
             if rel["descricoes_para_ia"] else ""))
    if rel.get("erro_ia"):
        print(f"  (IA de classificação falhou: {rel['erro_ia']})")

    # 5: métricas
    hoje = parse_data(a.hoje) if a.hoje else None
    saldo = parse_valor(a.saldo) if a.saldo else None
    m = calcular(df, hoje=hoje, saldo_inicial=saldo)

    # 6: diagnóstico
    d = diagnosticar(m, usar_ia=usar_ia, setor=a.setor)

    imprimir(m, d)
    Path("saida").mkdir(exist_ok=True)
    destino = Path("saida") / f"analise_{a.empresa}.json"
    destino.write_text(json.dumps({"metricas": m, "diagnostico": d.model_dump()},
                                  ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    df.to_csv(Path("saida") / f"lancamentos_{a.empresa}.csv", index=False, sep=";",
              decimal=",", encoding="utf-8-sig")
    painel = Path("saida") / f"painel_{a.empresa}.html"
    painel.write_text(gerar_html({"metricas": m, "diagnostico": d.model_dump()},
                                 a.nome or a.empresa, a.setor), encoding="utf-8")
    print(f"\n→ {painel}  (abra no navegador)\n→ {destino}\n→ saida/lancamentos_{a.empresa}.csv\n→ {caminho_regras}")


def imprimir(m: dict, d) -> None:
    ref, mensal, p = m["referencia"], m["mensal"], m["projecao"]
    print("\n" + "═" * 72)
    print(f" {ICONE[d.situacao]}  DIAGNÓSTICO DE CAIXA  ·  referência {dbr(ref['hoje'])}  ·  {d.metodo}")
    print("═" * 72)
    print(f"\n{d.resumo}\n")
    print("Números do mês (média dos últimos "
          f"{ref['dias_na_janela']} dias, por data do lançamento)")
    linhas = [("Faturamento", mensal["receita"]), ("Custos variáveis", -mensal["custos_variaveis"]),
              ("Despesas fixas", -mensal["despesas_fixas"]),
              ("Resultado operacional", mensal["resultado_operacional"])]
    for nome, v in linhas:
        print(f"  {nome:24} {brl(v):>14}")
    if m["margem_contribuicao_pct"] is not None:
        print(f"  {'Margem de contribuição':24} {m['margem_contribuicao_pct']:>13.1f}%")
    if m["ponto_equilibrio_mensal"]:
        print(f"  {'Ponto de equilíbrio':24} {brl(m['ponto_equilibrio_mensal']):>14}")

    rotulo = "Saldo projetado" if ref["saldo_inicial_informado"] else "Variação projetada"
    print(f"\n{rotulo}")
    if m["saldo_atual"] is not None:
        print(f"  {'hoje':24} {brl(m['saldo_atual']):>14}")
    for n in (30, 60, 90):
        print(f"  {'em ' + str(n) + ' dias':24} {brl(p[f'saldo_{n}d']):>14}")
    print(f"  {'mínimo':24} {brl(p['saldo_minimo']):>14}  em {dbr(p['data_saldo_minimo'])}")
    print(f"\nA receber: {brl(m['a_receber']['total'])} (vencido {brl(m['a_receber']['vencidas'])})"
          f"  ·  A pagar: {brl(m['a_pagar']['total'])} (vencido {brl(m['a_pagar']['vencidas'])})")

    if m["alertas"]:
        print("\nAlertas")
        for al in m["alertas"]:
            v = f"{al['valor']:.1f}%" if al["codigo"] == "margem_baixa" else brl(al["valor"])
            print(f"  {ICONE[al['nivel']]} {al['titulo']} ({v})")

    print("\nO que fazer")
    for i, ac in enumerate(d.acoes, 1):
        print(f"  {i}. {ac.titulo}  [{ac.prazo}]\n     {ac.porque}")
    inventados = numeros_inventados(d, m)
    if inventados:
        print(f"\n  ⚠ números no texto que não vêm das métricas: {inventados}")


if __name__ == "__main__":
    main()
