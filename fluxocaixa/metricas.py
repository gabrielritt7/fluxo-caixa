"""Motor de métricas e projeção de caixa. 100% código, nenhuma conta pela IA.

Entrada: lançamentos classificados (saída de `classificar`).
Saída: um dicionário JSON-serializável, que alimenta o painel e o diagnóstico.

Convenções:
- "Mensal" = média dos últimos até 90 dias com dados realizados, escalada para 30 dias.
- `hoje` padrão = última data com lançamento realizado (útil para planilhas antigas).
- Sem saldo inicial informado, a projeção mostra a VARIAÇÃO do caixa a partir de hoje.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .classificador import chave_descricao

JANELA_DIAS = 90
HORIZONTE_DIAS = 90
GRUPOS_RECORRENTES = ("receita", "custo_variavel", "despesa_fixa", "financeiro", "socios")


def _r(v) -> float:
    return round(float(v), 2)


def _sinal(df: pd.DataFrame) -> pd.Series:
    return df["valor"].where(df["tipo"] == "entrada", -df["valor"])


def calcular(df: pd.DataFrame, hoje: date | None = None,
             saldo_inicial: float | None = None) -> dict:
    df = df.copy()
    df["data"] = pd.to_datetime(df["data"])
    df["vencimento"] = pd.to_datetime(df["vencimento"])
    df["quando"] = df["vencimento"].where(df["status"] == "pendente", df["data"]).fillna(df["data"])

    real = df[df["status"] == "realizado"]
    pend = df[df["status"] == "pendente"]
    if hoje is None:
        hoje = (real["data"].max() if len(real) else df["data"].max()).date()
    t_hoje = pd.Timestamp(hoje)

    # ------------------------------------------------ janela para médias
    # Médias por COMPETÊNCIA (data do lançamento, pago ou não): uma conta
    # pendente de maio é despesa de maio. Saldo de caixa usa só o realizado.
    real_ate_hoje = real[real["data"] <= t_hoje]
    todos_ate_hoje = df[df["data"] <= t_hoje]
    # começa no dia 1º do mês do primeiro lançamento (planilhas são mensais;
    # começar no 1º lançamento do dia 5 inflaria as médias)
    inicio = max(todos_ate_hoje["data"].min().replace(day=1),
                 t_hoje - pd.Timedelta(days=JANELA_DIAS - 1)) if len(todos_ate_hoje) else t_hoje
    janela = todos_ate_hoje[todos_ate_hoje["data"] >= inicio]
    dias = max((t_hoje - inicio).days + 1, 1)
    fator = 30 / dias

    por_grupo = janela.groupby("grupo")["valor"].sum()

    def mensal(grupo: str) -> float:
        return float(por_grupo.get(grupo, 0.0)) * fator

    receita = mensal("receita")
    variavel = mensal("custo_variavel")
    fixas = mensal("despesa_fixa")
    financeiro = mensal("financeiro")
    socios = mensal("socios")
    margem = (receita - variavel) / receita if receita > 0 else None
    compromissos_fixos = fixas + financeiro + socios
    ponto_equilibrio = compromissos_fixos / margem if margem and margem > 0 else None

    # ------------------------------------------------ saldo atual
    variacao_ate_hoje = float(_sinal(real_ate_hoje).sum())
    saldo_atual = saldo_inicial + variacao_ate_hoje if saldo_inicial is not None else None

    # ------------------------------------------------ contas pendentes
    def resumo_pend(tipo: str) -> dict:
        p = pend[pend["tipo"] == tipo]
        vencidas = p[p["quando"] < t_hoje]
        prox30 = p[(p["quando"] >= t_hoje) & (p["quando"] < t_hoje + pd.Timedelta(days=30))]
        return {"total": _r(p["valor"].sum()), "quantidade": int(len(p)),
                "vencidas": _r(vencidas["valor"].sum()), "qtd_vencidas": int(len(vencidas)),
                "proximos_30_dias": _r(prox30["valor"].sum())}

    a_receber, a_pagar = resumo_pend("entrada"), resumo_pend("saida")

    # ------------------------------------------------ mensal (histórico)
    hist = real.assign(mes=real["data"].dt.to_period("M").astype(str), s=_sinal(real))
    historico = [
        {"mes": mes, "entradas": _r(g.loc[g.tipo == "entrada", "valor"].sum()),
         "saidas": _r(g.loc[g.tipo == "saida", "valor"].sum()), "resultado": _r(g["s"].sum())}
        for mes, g in hist.groupby("mes")
    ]

    # ------------------------------------------------ categorias e crescimento
    despesas_cat = (janela[janela.tipo == "saida"].groupby("categoria")["valor"].sum() * fator)
    despesas_cat = despesas_cat.sort_values(ascending=False)
    maiores_despesas = [{"categoria": c, "mensal": _r(v),
                         "pct_receita": _r(100 * v / receita) if receita else None}
                        for c, v in despesas_cat.head(6).items()]

    crescimento = []
    if dias >= 60:
        ult = janela[(janela.tipo == "saida") & (janela.data > t_hoje - pd.Timedelta(days=30))]
        ant = janela[(janela.tipo == "saida") & (janela.data <= t_hoje - pd.Timedelta(days=30))]
        dias_ant = max(dias - 30, 1)
        a = ult.groupby("categoria")["valor"].sum()
        b = ant.groupby("categoria")["valor"].sum() * 30 / dias_ant
        for cat in a.index:
            antes, agora = float(b.get(cat, 0)), float(a[cat])
            if antes > 0 and agora - antes > 200 and agora / antes > 1.15:
                crescimento.append({"categoria": cat, "media_anterior": _r(antes),
                                    "ultimos_30_dias": _r(agora),
                                    "variacao_pct": _r(100 * (agora / antes - 1))})
        crescimento.sort(key=lambda x: x["ultimos_30_dias"] - x["media_anterior"], reverse=True)

    fontes = (janela[janela.grupo == "receita"].groupby("descricao")["valor"].sum()
              .sort_values(ascending=False))
    total_receita_janela = float(fontes.sum())
    maiores_fontes = [{"descricao": d, "pct": _r(100 * v / total_receita_janela)}
                      for d, v in fontes.head(5).items()] if total_receita_janela else []

    # ------------------------------------------------ projeção diária
    projecao = projetar(df, janela, dias, t_hoje, saldo_atual or 0.0)

    m = {
        "referencia": {"hoje": hoje.isoformat(), "inicio_janela": inicio.date().isoformat(),
                       "dias_na_janela": dias, "saldo_inicial_informado": saldo_inicial is not None,
                       "lancamentos": int(len(df))},
        "mensal": {"receita": _r(receita), "custos_variaveis": _r(variavel),
                   "despesas_fixas": _r(fixas), "financeiro": _r(financeiro),
                   "retiradas_socios": _r(socios),
                   "resultado_operacional": _r(receita - variavel - fixas),
                   "geracao_de_caixa": _r(float(_sinal(janela).sum()) * fator)},
        "margem_contribuicao_pct": _r(100 * margem) if margem is not None else None,
        "ponto_equilibrio_mensal": _r(ponto_equilibrio) if ponto_equilibrio else None,
        "saldo_atual": _r(saldo_atual) if saldo_atual is not None else None,
        "a_receber": a_receber, "a_pagar": a_pagar,
        "historico_mensal": historico,
        "maiores_despesas": maiores_despesas,
        "despesas_em_alta": crescimento[:3],
        "maiores_fontes_receita": maiores_fontes,
        "projecao": projecao,
    }
    geracao = m["mensal"]["geracao_de_caixa"]
    m["folego_meses"] = _r(saldo_atual / -geracao) if saldo_atual and saldo_atual > 0 and geracao < 0 else None
    m["alertas"] = alertas(m)
    return m


def projetar(df: pd.DataFrame, janela: pd.DataFrame, dias: int, t_hoje: pd.Timestamp,
             saldo_atual: float) -> dict:
    """Saldo dia a dia nos próximos 90 dias =
       saldo atual
     + contas pendentes conhecidas (na data de vencimento)
     + recorrência estimada por categoria (média diária da janela), descontando
       o que já está lançado como pendente para não contar duas vezes — mas só
       quando a conta é recorrente (mesma descrição já apareceu no histórico).
       Uma conta nova (13º salário, compra de equipamento) entra POR CIMA da média.

    Conservador de propósito: recebíveis vencidos NÃO entram (podem não vir);
    contas a pagar vencidas entram amanhã."""
    pend = df[df["status"] == "pendente"]
    futuro = pend[pend["quando"] > t_hoje]
    pagar_vencido = pend[(pend["quando"] <= t_hoje) & (pend["tipo"] == "saida")]["valor"].sum()

    datas = pd.date_range(t_hoje + pd.Timedelta(days=1), periods=HORIZONTE_DIAS, freq="D")
    fluxo = pd.Series(0.0, index=datas)
    fluxo.iloc[0] -= pagar_vencido
    conhecidos = futuro[futuro["quando"] <= datas[-1]]
    for _, l in conhecidos.iterrows():
        fluxo[l["quando"].normalize()] += l["valor"] if l["tipo"] == "entrada" else -l["valor"]

    rec = janela[janela["grupo"].isin(GRUPOS_RECORRENTES)]
    recorrentes = set(rec.loc[rec["status"] == "realizado", "descricao"].map(chave_descricao))
    conhecidos = conhecidos.assign(chave=conhecidos["descricao"].map(chave_descricao))
    diaria = rec.groupby(["categoria", "tipo"])["valor"].sum() / dias
    for bloco in range(0, HORIZONTE_DIAS, 30):
        idx = datas[bloco:bloco + 30]
        no_bloco = conhecidos[(conhecidos["quando"] >= idx[0]) & (conhecidos["quando"] <= idx[-1])
                              & conhecidos["chave"].isin(recorrentes)]
        ja_lancado = no_bloco.groupby(["categoria", "tipo"])["valor"].sum()
        for (cat, tipo), por_dia in diaria.items():
            estimado = max(por_dia * len(idx) - float(ja_lancado.get((cat, tipo), 0)), 0)
            fluxo[idx] += (estimado if tipo == "entrada" else -estimado) / len(idx)

    saldo = saldo_atual + fluxo.cumsum()
    negativo = saldo[saldo < 0]
    minimo_dia = saldo.idxmin()

    def em(d: int) -> float:
        return _r(saldo.iloc[min(d, HORIZONTE_DIAS) - 1])

    return {
        "saldo_30d": em(30), "saldo_60d": em(60), "saldo_90d": em(90),
        "saldo_minimo": _r(saldo.min()), "data_saldo_minimo": minimo_dia.date().isoformat(),
        "primeiro_dia_negativo": negativo.index[0].date().isoformat() if len(negativo) else None,
        "contas_conhecidas_no_periodo": int(len(conhecidos)),
        "a_pagar_vencido_incluido": _r(pagar_vencido),
        "serie_semanal": [{"data": d.date().isoformat(), "saldo": _r(v)}
                          for d, v in saldo.iloc[6::7].items()],
        "serie_diaria": [_r(v) for v in saldo.tolist()],  # dia 1 = amanhã
        "contas_conhecidas": [
            {"data": l["quando"].date().isoformat(), "descricao": l["descricao"],
             "valor": _r(l["valor"]), "tipo": l["tipo"]}
            for _, l in conhecidos.sort_values("quando").iterrows()][:20],
    }


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _quando(d: date, hoje: date) -> str:
    n = (d - hoje).days
    return "Amanhã" if n == 1 else f"Em {n} dias"


def alertas(m: dict) -> list[dict]:
    """Regras determinísticas. A IA só escreve o texto em cima disto."""
    out = []
    p, mensal = m["projecao"], m["mensal"]
    hoje = date.fromisoformat(m["referencia"]["hoje"])
    if m["saldo_atual"] is not None and m["saldo_atual"] < 0:
        out.append({"nivel": "critico", "codigo": "caixa_ja_negativo",
                    "titulo": "O caixa já está negativo", "valor": m["saldo_atual"]})
    elif p["primeiro_dia_negativo"]:
        d = date.fromisoformat(p["primeiro_dia_negativo"])
        base = "o caixa fica negativo" if m["referencia"]["saldo_inicial_informado"] \
            else "a variação acumulada do caixa fica negativa"
        out.append({"nivel": "critico" if (d - hoje).days <= 30 else "atencao",
                    "codigo": "caixa_negativo",
                    "titulo": f"{_quando(d, hoje)} {base}",
                    "valor": p["saldo_minimo"], "data": p["primeiro_dia_negativo"]})
    if mensal["resultado_operacional"] < 0:
        out.append({"nivel": "critico", "codigo": "operacao_no_prejuizo",
                    "titulo": "A operação gasta mais do que fatura",
                    "valor": mensal["resultado_operacional"]})
    if m["ponto_equilibrio_mensal"] and mensal["receita"] < m["ponto_equilibrio_mensal"]:
        out.append({"nivel": "atencao", "codigo": "abaixo_do_equilibrio",
                    "titulo": "Faturamento abaixo do ponto de equilíbrio",
                    "valor": _r(m["ponto_equilibrio_mensal"] - mensal["receita"])})
    if m["a_receber"]["vencidas"] > 0:
        out.append({"nivel": "atencao", "codigo": "recebiveis_vencidos",
                    "titulo": _plural(m['a_receber']['qtd_vencidas'], "recebimento em atraso", "recebimentos em atraso"),
                    "valor": m["a_receber"]["vencidas"]})
    if m["a_pagar"]["vencidas"] > 0:
        out.append({"nivel": "atencao", "codigo": "contas_vencidas",
                    "titulo": _plural(m['a_pagar']['qtd_vencidas'], "conta a pagar vencida", "contas a pagar vencidas"),
                    "valor": m["a_pagar"]["vencidas"]})
    if m["margem_contribuicao_pct"] is not None and m["margem_contribuicao_pct"] < 30:
        out.append({"nivel": "atencao", "codigo": "margem_baixa",
                    "titulo": "Margem de contribuição baixa", "valor": m["margem_contribuicao_pct"]})
    for d in m["despesas_em_alta"]:
        out.append({"nivel": "info", "codigo": "despesa_em_alta",
                    "titulo": f"{d['categoria']} subiu {d['variacao_pct']:.0f}%",
                    "valor": _r(d["ultimos_30_dias"] - d["media_anterior"])})
    if m["folego_meses"] is not None and m["folego_meses"] < 3:
        out.append({"nivel": "critico", "codigo": "pouco_folego",
                    "titulo": f"No ritmo atual o caixa dura cerca de {m['folego_meses']:.1f} meses".replace(".", ","),
                    "valor": m["saldo_atual"]})
    ordem = {"critico": 0, "atencao": 1, "info": 2}
    return sorted(out, key=lambda a: ordem[a["nivel"]])
