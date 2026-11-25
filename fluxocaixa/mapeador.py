"""Mapeamento de colunas: descobre qual coluna da planilha é data, valor etc.

Dois caminhos:
  1. IA (Claude) — entende cabeçalhos estranhos e olha os dados de amostra.
  2. Heurística por sinônimos — fallback sem custo, usado sem chave de API
     ou se a chamada falhar.

Só cabeçalhos + poucas linhas de amostra (truncadas) vão para a IA: nada de
arquivo inteiro. Minimiza custo e exposição de dados (LGPD).
"""
from __future__ import annotations

import json
import os
import unicodedata
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

MODELO = os.environ.get("FLUXO_MODELO", "claude-opus-5-5")
LINHAS_AMOSTRA = 15
MAX_CHARS_CELULA = 50


class Mapeamento(BaseModel):
    """Qual coluna da planilha corresponde a cada campo. null = não existe."""

    e_planilha_de_lancamentos: bool = Field(
        description="true se a tabela lista lançamentos financeiros individuais "
                    "(e não um resumo, dashboard ou cadastro)")
    coluna_data: Optional[str] = Field(description="data do lançamento/pagamento/competência")
    coluna_descricao: Optional[str] = Field(description="histórico, descrição, fornecedor ou cliente")
    coluna_valor: Optional[str] = Field(
        description="coluna ÚNICA de valor (com sinal ou acompanhada de coluna de tipo). "
                    "null se entradas e saídas estão em colunas separadas")
    coluna_entrada: Optional[str] = Field(description="coluna só de entradas/receitas/créditos")
    coluna_saida: Optional[str] = Field(description="coluna só de saídas/despesas/débitos")
    coluna_tipo: Optional[str] = Field(description="coluna que diz se é entrada ou saída (ex.: C/D, Receita/Despesa)")
    coluna_categoria: Optional[str] = Field(description="categoria, plano de contas, centro de custo")
    coluna_vencimento: Optional[str] = Field(description="data de vencimento, se diferente da data do lançamento")
    coluna_status: Optional[str] = Field(description="pago/pendente, situação")
    confianca: Literal["alta", "media", "baixa"]
    observacoes: str = Field(description="dúvidas ou ressalvas em 1-2 frases, em português")
    metodo: str = Field(default="ia", description="preenchido pelo sistema; ignore")

    def colunas_usadas(self) -> dict[str, str]:
        campos = ["coluna_data", "coluna_descricao", "coluna_valor", "coluna_entrada",
                  "coluna_saida", "coluna_tipo", "coluna_categoria", "coluna_vencimento",
                  "coluna_status"]
        return {c: getattr(self, c) for c in campos if getattr(self, c)}


# ------------------------------------------------------------------ amostra

def _celula(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d (data do Excel)")
    s = str(v).strip()
    return s[:MAX_CHARS_CELULA]


def montar_amostra(df: pd.DataFrame) -> str:
    """Cabeçalhos + linhas espalhadas (início, meio e fim) em JSON compacto."""
    n = len(df)
    if n <= LINHAS_AMOSTRA:
        idx = list(range(n))
    else:
        terco = LINHAS_AMOSTRA // 3
        idx = list(range(terco)) + list(range(n // 2, n // 2 + terco)) + list(range(n - terco, n))
    linhas = [{c: _celula(df.iloc[i][c]) for c in df.columns} for i in idx]
    return json.dumps({"colunas": list(df.columns), "total_linhas": n,
                       "amostra": linhas}, ensure_ascii=False)


# ----------------------------------------------------------------------- IA

INSTRUCOES = """Você recebe o cabeçalho e uma amostra de linhas de uma planilha \
financeira de uma pequena empresa brasileira. Identifique qual coluna corresponde \
a cada campo do esquema.

Regras:
- Use exatamente os nomes de coluna fornecidos em "colunas". Se um campo não existe, use null.
- Valores podem estar em formato brasileiro (1.234,56), com "R$", negativos com "-" ou parênteses.
- Se houver uma coluna de saldo acumulado, NÃO a use como valor.
- Se entradas e saídas estão em colunas separadas, preencha coluna_entrada e \
coluna_saida e deixe coluna_valor null.
- Se a tabela for um resumo/totalizador e não uma lista de lançamentos, marque \
e_planilha_de_lancamentos = false.
- Escreva observacoes em português, em no máximo 2 frases."""


def mapear_com_ia(df: pd.DataFrame, cliente=None) -> Mapeamento:
    import anthropic

    cliente = cliente or anthropic.Anthropic()
    resposta = cliente.beta.messages.parse(
        model=MODELO,
        max_tokens=4000,
        # tarefa simples e bem definida: esforço baixo = mais rápido e barato
        output_config={"effort": "low"},
        # se o modelo recusar por algum classificador de segurança, a API
        # tenta automaticamente outro modelo na mesma chamada
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=INSTRUCOES,
        messages=[{"role": "user", "content": montar_amostra(df)}],
        output_format=Mapeamento,
    )
    if resposta.stop_reason == "refusal" or resposta.parsed_output is None:
        raise RuntimeError(f"IA não retornou mapeamento (stop_reason={resposta.stop_reason})")
    mp = resposta.parsed_output
    mp.metodo = f"ia ({resposta.model})"
    return mp


# ---------------------------------------------------------------- heurística

SINONIMOS: dict[str, list[str]] = {
    # ordem importa: vencimento antes de data ("Data Vencimento")
    "coluna_vencimento": ["vencimento", "venc", "vcto", "due"],
    "coluna_status": ["status", "situacao", "pago?", "quitado", "baixa"],
    "coluna_tipo": ["tipo", "natureza", "d/c", "c/d", "operacao", "movimento", "mov"],
    "coluna_entrada": ["entrada", "entradas", "receita", "receitas", "credito",
                       "creditos", "recebimento", "recebimentos"],
    "coluna_saida": ["saida", "saidas", "despesa", "despesas", "debito", "debitos",
                     "pagamento", "pagamentos"],
    "coluna_valor": ["valor", "valores", "montante", "quantia", "r$", "importe", "vlr"],
    "coluna_data": ["data", "dt", "date", "dia", "competencia", "emissao"],
    "coluna_descricao": ["descricao", "historico", "discriminacao", "descr", "detalhe",
                         "lancamento", "item", "fornecedor", "cliente", "favorecido", "memo"],
    "coluna_categoria": ["categoria", "plano de contas", "classificacao", "centro de custo",
                         "conta", "grupo"],
}
IGNORAR = ("saldo",)


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s.lower().replace(".", " ").replace("_", " ").strip()


def _casa(cabecalho: str, sinonimo: str) -> bool:
    palavras = cabecalho.replace("(", " ").replace(")", " ").split()
    return cabecalho == sinonimo or sinonimo in palavras or (
        " " in sinonimo and sinonimo in cabecalho) or ("/" in sinonimo and sinonimo in cabecalho)


def mapear_por_heuristica(df: pd.DataFrame) -> Mapeamento:
    from .normalizador import parse_data, parse_tipo, parse_valor

    escolhido: dict[str, str] = {}
    usadas: set[str] = set()
    for campo, sinonimos in SINONIMOS.items():
        for col in df.columns:
            n = _norm(str(col))
            if col in usadas or any(p in n for p in IGNORAR):
                continue
            if any(_casa(n, s) for s in sinonimos):
                escolhido[campo] = col
                usadas.add(col)
                break

    amostra = df.head(30)

    def taxa(col, fn):
        vals = [v for v in amostra[col].tolist() if not (v is None or (isinstance(v, float) and pd.isna(v)))]
        return sum(fn(v) is not None for v in vals) / len(vals) if vals else 0

    # "Entrada"/"Receita" pode ser coluna de TIPO (texto), não de valor
    for campo in ("coluna_entrada", "coluna_saida"):
        col = escolhido.get(campo)
        if col and taxa(col, parse_valor) < 0.5 and taxa(col, parse_tipo) > 0.5:
            escolhido.pop(campo)
            escolhido.setdefault("coluna_tipo", col)

    # sem cabeçalho reconhecível: descobre pelos dados
    livres = [c for c in df.columns if c not in usadas and not any(p in _norm(str(c)) for p in IGNORAR)]
    if "coluna_data" not in escolhido:
        for c in livres:
            if taxa(c, parse_data) > 0.7:
                escolhido["coluna_data"] = c
                livres.remove(c)
                break
    if not any(k in escolhido for k in ("coluna_valor", "coluna_entrada", "coluna_saida")):
        for c in livres:
            if taxa(c, parse_valor) > 0.7:
                escolhido["coluna_valor"] = c
                livres.remove(c)
                break
    if "coluna_tipo" not in escolhido and "coluna_valor" in escolhido:
        for c in livres:
            if taxa(c, parse_tipo) > 0.8:
                escolhido["coluna_tipo"] = c
                livres.remove(c)
                break
    if "coluna_descricao" not in escolhido:
        for c in livres:
            if taxa(c, parse_valor) < 0.3 and taxa(c, parse_data) < 0.3:
                escolhido["coluna_descricao"] = c
                break

    tem_valor = any(k in escolhido for k in ("coluna_valor", "coluna_entrada", "coluna_saida"))
    tem_data = "coluna_data" in escolhido or "coluna_vencimento" in escolhido
    confianca = "media" if tem_valor and tem_data and "coluna_descricao" in escolhido else "baixa"
    return Mapeamento(
        e_planilha_de_lancamentos=tem_valor and tem_data,
        confianca=confianca,
        observacoes="Mapeado por sinônimos de cabeçalho (sem IA).",
        metodo="heuristica",
        **{k: escolhido.get(k) for k in SINONIMOS},
    )


# ----------------------------------------------------------------- principal

def validar(mp: Mapeamento, df: pd.DataFrame) -> list[str]:
    """Garante que a IA não inventou nomes de coluna e que o mínimo existe."""
    problemas = []
    for campo, col in mp.colunas_usadas().items():
        if col not in df.columns:
            problemas.append(f"{campo} aponta para coluna inexistente: {col!r}")
    if not (mp.coluna_valor or mp.coluna_entrada or mp.coluna_saida):
        problemas.append("nenhuma coluna de valor identificada")
    if not (mp.coluna_data or mp.coluna_vencimento):
        problemas.append("nenhuma coluna de data identificada")
    if (mp.coluna_valor in df.columns and not mp.coluna_tipo
            and not (mp.coluna_entrada or mp.coluna_saida)):
        from .normalizador import parse_valor
        valores = [parse_valor(v) for v in df[mp.coluna_valor].tolist()]
        if not any(v is not None and v < 0 for v in valores):
            # coluna única, sem sinal e sem coluna de tipo: tudo viraria
            # "entrada". Melhor parar e perguntar do que errar em silêncio.
            problemas.append("impossível distinguir entradas de saídas "
                             "(sem coluna de tipo e sem valores negativos)")
    return problemas


def tem_credencial_ia() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def mapear(df: pd.DataFrame, usar_ia: bool | None = None) -> Mapeamento:
    if usar_ia is None:
        usar_ia = tem_credencial_ia()
    if usar_ia:
        try:
            mp = mapear_com_ia(df)
            if not validar(mp, df):
                return mp
            erro = "; ".join(validar(mp, df))
        except Exception as e:  # rede, chave inválida, limite de uso...
            erro = f"{type(e).__name__}: {e}"
        mp = mapear_por_heuristica(df)
        mp.observacoes = f"IA indisponível/inválida ({erro[:120]}). " + mp.observacoes
        return mp
    return mapear_por_heuristica(df)
