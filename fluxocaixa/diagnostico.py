"""Diagnóstico em linguagem simples + 3 ações concretas.

A IA recebe APENAS as métricas já calculadas (nenhum lançamento individual) e
é proibida de fazer contas novas: todo número no texto tem de vir do JSON.
Sem IA, um texto-modelo é montado a partir dos alertas.
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

MODELO = os.environ.get("FLUXO_MODELO", "claude-opus-5-5")


class Acao(BaseModel):
    titulo: str = Field(description="verbo no imperativo, até 8 palavras")
    porque: str = Field(description="1-2 frases ligando a ação a um número das métricas")
    prazo: Literal["esta semana", "este mês", "próximos 90 dias"]


class Diagnostico(BaseModel):
    situacao: Literal["saudavel", "atencao", "critico"]
    resumo: str = Field(description="3 frases no máximo, para o dono ler no celular")
    pontos_de_atencao: list[str] = Field(description="até 4 itens curtos")
    acoes: list[Acao] = Field(description="exatamente 3 ações, da mais urgente para a menos")
    metodo: str = Field(default="ia", description="preenchido pelo sistema; ignore")


INSTRUCOES = """Você é um consultor financeiro de pequenas empresas brasileiras. \
Recebe métricas de caixa JÁ CALCULADAS de uma empresa e escreve um diagnóstico \
para o dono, que não é da área financeira.

Regras obrigatórias:
- Use somente números que aparecem no JSON. Não calcule valores novos, não some, \
não estime. Pode arredondar para reais inteiros ao citar (ex.: R$ 8.028).
- Formato brasileiro: R$ 1.234, datas dd/mm.
- Linguagem simples, direta, sem jargão (se usar "margem de contribuição" ou \
"ponto de equilíbrio", explique em poucas palavras).
- Ações concretas e possíveis para uma PME (cobrar atrasados, renegociar prazo \
com fornecedor, revisar taxa da maquininha, cortar uma despesa específica...). \
Nada genérico como "melhore a gestão".
- Se "saldo_inicial_informado" for false, a projeção é a VARIAÇÃO do caixa, não o \
saldo — deixe isso claro e recomende informar o saldo bancário atual.
- Situação: "critico" se houver alerta crítico, "atencao" se houver alerta de \
atenção, senão "saudavel"."""


def diagnosticar_com_ia(metricas: dict, setor: str | None = None, cliente=None) -> Diagnostico:
    import anthropic

    cliente = cliente or anthropic.Anthropic()
    dados = {k: v for k, v in metricas.items() if k != "projecao"}
    dados["projecao"] = {k: v for k, v in metricas["projecao"].items() if k not in ("serie_semanal", "serie_diaria")}
    if setor:
        dados["setor"] = setor
    resposta = cliente.beta.messages.parse(
        model=MODELO,
        max_tokens=16000,
        # escrita com julgamento: esforço médio (padrão do Opus 5.5) explícito
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=INSTRUCOES,
        messages=[{"role": "user", "content": json.dumps(dados, ensure_ascii=False)}],
        output_format=Diagnostico,
    )
    if resposta.stop_reason == "refusal" or resposta.parsed_output is None:
        raise RuntimeError(f"IA não retornou diagnóstico (stop_reason={resposta.stop_reason})")
    d = resposta.parsed_output
    d.metodo = f"ia ({resposta.model})"
    return d


# ------------------------------------------------------- verificação de números

def _numeros_em_reais(texto: str) -> list[float]:
    achados = re.findall(r"R\$\s?(-?[\d.]+(?:,\d{1,2})?)", texto)
    return [float(n.replace(".", "").replace(",", ".")) for n in achados]


def _valores_das_metricas(obj) -> list[float]:
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _valores_das_metricas(x)]
    if isinstance(obj, list):
        return [v for x in obj for v in _valores_das_metricas(x)]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [abs(float(obj))]
    return []


def numeros_inventados(d: Diagnostico, metricas: dict) -> list[float]:
    """Todo 'R$ X' do texto tem de existir nas métricas (tolerância de
    arredondamento). Em produção: se algo vier inventado, refaz ou usa o modelo."""
    conhecidos = _valores_das_metricas(metricas)
    texto = " ".join([d.resumo, *d.pontos_de_atencao, *(a.porque + " " + a.titulo for a in d.acoes)])
    return [n for n in _numeros_em_reais(texto)
            if not any(abs(abs(n) - k) <= max(1.0, 0.005 * k) for k in conhecidos)]


# ------------------------------------------------------------- texto-modelo

def brl(v: float) -> str:
    return "R$ " + f"{abs(v):,.0f}".replace(",", ".") if v >= 0 else "−R$ " + f"{abs(v):,.0f}".replace(",", ".")


ACOES_POR_ALERTA = {
    "caixa_ja_negativo": ("Priorize os pagamentos desta semana", "esta semana",
                          "O caixa está em {v}: liste o que vence nos próximos 7 dias e pague primeiro o que gera multa ou corta fornecimento."),
    "caixa_negativo": ("Antecipe recebíveis ou adie pagamentos", "esta semana",
                       "A projeção chega a {v}. Negocie prazos com fornecedores antes do aperto, não depois."),
    "recebiveis_vencidos": ("Cobre os clientes em atraso", "esta semana",
                            "Há {v} parados em recebimentos vencidos — é dinheiro que já é seu."),
    "contas_vencidas": ("Renegocie as contas vencidas", "esta semana",
                        "São {v} em contas vencidas; juros e multas corroem o caixa a cada dia."),
    "operacao_no_prejuizo": ("Corte a maior despesa fixa", "este mês",
                             "A operação perde {v} por mês. Comece revendo a maior linha de despesa."),
    "abaixo_do_equilibrio": ("Defina meta de vendas mensal", "este mês",
                             "Faltam {v} por mês de faturamento para pagar todas as contas."),
    "margem_baixa": ("Revise preços e custos variáveis", "este mês",
                     "Sobram só {p}% de cada venda depois dos custos diretos."),
    "despesa_em_alta": ("Investigue a despesa que disparou", "este mês",
                        "Um aumento de {v} no último mês merece explicação."),
    "pouco_folego": ("Monte uma reserva de emergência", "próximos 90 dias",
                     "No ritmo atual o caixa dura pouco: cada mês de reserva compra tempo para corrigir a rota."),
}


def diagnostico_modelo(m: dict) -> Diagnostico:
    al = m["alertas"]
    situacao = "critico" if any(a["nivel"] == "critico" for a in al) else \
        "atencao" if any(a["nivel"] == "atencao" for a in al) else "saudavel"
    mensal = m["mensal"]
    frases = [f"Nos últimos {m['referencia']['dias_na_janela']} dias a empresa faturou em média "
              f"{brl(mensal['receita'])} por mês e o resultado da operação foi de "
              f"{brl(mensal['resultado_operacional'])} por mês."]
    if m["saldo_atual"] is not None:
        frases.append(f"O saldo hoje é {brl(m['saldo_atual'])} e a projeção para 30 dias é "
                      f"{brl(m['projecao']['saldo_30d'])}.")
    else:
        frases.append("Informe o saldo bancário atual para a projeção mostrar o saldo real, "
                      "não só a variação.")
    acoes, vistos = [], set()
    for a in al:
        if a["codigo"] in ACOES_POR_ALERTA and a["codigo"] not in vistos:
            titulo, prazo, txt = ACOES_POR_ALERTA[a["codigo"]]
            acoes.append(Acao(titulo=titulo, prazo=prazo,
                              porque=txt.format(v=brl(abs(a["valor"]) if a["codigo"] == "operacao_no_prejuizo"
                                                      else a["valor"]), p=f"{a['valor']:.0f}")))
            vistos.add(a["codigo"])
    ordem_prazo = {"esta semana": 0, "este mês": 1, "próximos 90 dias": 2}
    acoes.sort(key=lambda ac: ordem_prazo[ac.prazo])  # estável: mantém gravidade
    acoes = acoes[:3]
    padrao = [Acao(titulo="Separe uma reserva de 3 meses de despesas", prazo="próximos 90 dias",
                   porque=f"Suas despesas fixas são {brl(mensal['despesas_fixas'])} por mês."),
              Acao(titulo="Revise as 3 maiores despesas", prazo="este mês",
                   porque="Pequenos cortes nas maiores linhas fazem mais diferença que nas menores."),
              Acao(titulo="Atualize a planilha toda semana", prazo="esta semana",
                   porque="Com dados frescos o alerta de caixa chega antes do aperto.")]
    while len(acoes) < 3:
        acoes.append(padrao.pop(0))
    return Diagnostico(situacao=situacao, resumo=" ".join(frases),
                       pontos_de_atencao=[a["titulo"] for a in al[:4]], acoes=acoes,
                       metodo="modelo")


def diagnosticar(m: dict, usar_ia: bool | None = None, setor: str | None = None) -> Diagnostico:
    from .mapeador import tem_credencial_ia

    if usar_ia is None:
        usar_ia = tem_credencial_ia()
    if usar_ia:
        try:
            d = diagnosticar_com_ia(m, setor=setor)
            if not numeros_inventados(d, m):
                return d
        except Exception:
            pass
    return diagnostico_modelo(m)
