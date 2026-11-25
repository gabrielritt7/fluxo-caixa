"""Classificação de lançamentos em categorias.

Ordem de decisão (da mais barata/confiável para a mais cara):
  1. Regras da empresa  — aprendidas com correções do usuário e respostas da IA
  2. Regras padrão      — palavras-chave do Brasil (CEMIG, DAS, folha...)
  3. Categoria que já veio na planilha — de-para memorizado por empresa
  4. IA em lotes        — só descrições distintas que sobraram
  5. "Outras receitas/despesas" com confiança baixa

Tudo que a IA classifica vira regra da empresa: no mês seguinte a mesma
descrição não custa nada.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from .categorias import CATEGORIAS, PADRAO_SEM_CATEGORIA, REGRAS_PADRAO, SINONIMOS_CATEGORIA
from .normalizador import sem_acento

MODELO = os.environ.get("FLUXO_MODELO", "claude-opus-5-5")
TAMANHO_LOTE = 100

NomeCategoria = Literal[tuple(CATEGORIAS)]  # type: ignore[valid-type]


# ----------------------------------------------------------------- chave

def chave_descricao(descricao: str) -> str:
    """Forma canônica para casar descrições "iguais": sem acento, sem números
    (datas, notas, parcelas), sem pontuação. 'PIX 12/03 Fornecedor X NF 4521'
    → 'pix fornecedor x nf'."""
    s = sem_acento(str(descricao or ""))
    s = re.sub(r"\d+", " ", s)
    s = re.sub(r"[^a-z ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def mascarar(descricao: str) -> str:
    """Remove CPF/CNPJ/contas antes de mandar para a IA (LGPD)."""
    return re.sub(r"\d[\d./-]{4,}\d", "#", str(descricao or ""))[:80]


def _contem(texto: str, palavra: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(palavra)}(?![a-z])", texto) is not None


# ------------------------------------------------------------ regras empresa

@dataclass
class Regra:
    chave: str          # descrição canônica (ou palavra-chave, se exata=False)
    categoria: str
    origem: str         # "usuario" | "ia" | "categoria_planilha"
    exata: bool = True  # True: chave == descrição canônica; False: contém palavra


@dataclass
class RegrasEmpresa:
    """Memória de classificação de UMA empresa. No produto: tabela no banco."""

    regras: list[Regra] = field(default_factory=list)
    categorias_planilha: dict[str, str] = field(default_factory=dict)  # "tipo:original" → padrão

    @classmethod
    def carregar(cls, caminho: Path) -> "RegrasEmpresa":
        if not caminho.exists():
            return cls()
        d = json.loads(caminho.read_text(encoding="utf-8"))
        return cls([Regra(**r) for r in d.get("regras", [])], d.get("categorias_planilha", {}))

    def salvar(self, caminho: Path) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps({"regras": [asdict(r) for r in self.regras],
                                       "categorias_planilha": self.categorias_planilha},
                                      ensure_ascii=False, indent=1), encoding="utf-8")

    def buscar(self, chave: str) -> Regra | None:
        # regras do usuário vencem as da IA; exatas vencem as por palavra
        prioridade = {"usuario": 0, "categoria_planilha": 1, "ia": 2}
        candidatas = [r for r in self.regras
                      if (r.exata and r.chave == chave) or (not r.exata and _contem(chave, r.chave))]
        return min(candidatas, key=lambda r: (prioridade.get(r.origem, 9), not r.exata),
                   default=None)

    def adicionar(self, chave: str, categoria: str, origem: str, exata: bool = True) -> None:
        if categoria not in CATEGORIAS or not chave:
            return
        self.regras = [r for r in self.regras
                       if not (r.chave == chave and r.exata == exata and
                               (origem == "usuario" or r.origem != "usuario"))]
        if not any(r.chave == chave and r.exata == exata for r in self.regras):
            self.regras.append(Regra(chave, categoria, origem, exata))

    def corrigir(self, descricao: str, categoria: str, palavra_chave: str | None = None) -> None:
        """Chamado quando o usuário corrige uma categoria na tela. Com
        `palavra_chave` ("cemig"), a regra vale para qualquer descrição que a contenha."""
        if palavra_chave:
            self.adicionar(chave_descricao(palavra_chave), categoria, "usuario", exata=False)
        else:
            self.adicionar(chave_descricao(descricao), categoria, "usuario")


# --------------------------------------------------------------- regras padrão

def regra_padrao(chave: str, tipo: str) -> str | None:
    for palavras, categoria, tipo_exigido in REGRAS_PADRAO:
        if tipo_exigido and tipo_exigido != tipo:
            continue
        if any(_contem(chave, p) for p in palavras):
            return categoria
    return None


def categoria_da_planilha(original, tipo: str, regras: "RegrasEmpresa") -> str | None:
    """Traduz a categoria que o dono usou ('Insumos', 'Utilidades') para o plano
    padrão. Memoriza por empresa; o usuário pode corrigir o de-para."""
    if original is None or (isinstance(original, float) and pd.isna(original)):
        return None
    chave_memoria = f"{tipo}:{original}"
    if chave_memoria in regras.categorias_planilha:
        return regras.categorias_planilha[chave_memoria]
    chave = chave_descricao(original)
    cat = None
    for palavras, se_entrada, se_saida in SINONIMOS_CATEGORIA:
        if chave in palavras or any(_contem(chave, p) for p in palavras if " " in p):
            cat = se_entrada if tipo == "entrada" else se_saida
            if cat:
                break
    cat = cat or regra_padrao(chave, tipo)
    if cat:
        regras.categorias_planilha[chave_memoria] = cat
    return cat


def compativel(categoria: str, tipo: str) -> bool:
    t = CATEGORIAS.get(categoria, {}).get("tipo")
    return t is None or t == tipo


# ----------------------------------------------------------------------- IA

class ItemClassificado(BaseModel):
    id: int
    categoria: NomeCategoria  # enum: a IA só pode escolher categorias que existem
    confianca: Literal["alta", "media", "baixa"]


class LoteClassificado(BaseModel):
    itens: list[ItemClassificado] = Field(description="um item para cada id recebido")


def _instrucoes() -> str:
    linhas = "\n".join(f"- {n} ({'entrada' if c['tipo'] == 'entrada' else 'saída' if c['tipo'] else 'neutro'})"
                       for n, c in CATEGORIAS.items())
    return (
        "Você classifica lançamentos financeiros de pequenas empresas brasileiras no "
        "plano de contas abaixo. Para cada item (id, descrição, tipo e, se houver, a "
        "categoria que o próprio dono usou), escolha UMA categoria compatível com o tipo "
        "(entrada ou saída).\n\nCategorias:\n" + linhas + "\n\n"
        "Use 'Outras receitas'/'Outras despesas' só quando nada mais servir, com confiança "
        "baixa. Devolva exatamente um item por id recebido."
    )


def classificar_com_ia(itens: list[dict], setor: str | None = None, cliente=None) -> dict[int, ItemClassificado]:
    """`itens`: [{id, descricao, tipo, categoria_original}] — já deduplicados."""
    import anthropic

    cliente = cliente or anthropic.Anthropic()
    resultado: dict[int, ItemClassificado] = {}
    sistema = _instrucoes() + (f"\nSetor da empresa: {setor}." if setor else "")
    for i in range(0, len(itens), TAMANHO_LOTE):
        lote = itens[i:i + TAMANHO_LOTE]
        resposta = cliente.beta.messages.parse(
            model=MODELO,
            max_tokens=16000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=sistema,
            messages=[{"role": "user", "content": json.dumps(lote, ensure_ascii=False)}],
            output_format=LoteClassificado,
        )
        if resposta.stop_reason == "refusal" or resposta.parsed_output is None:
            continue  # fica para o fallback; não derruba a análise
        ids = {it["id"] for it in lote}
        for item in resposta.parsed_output.itens:
            if item.id in ids:
                resultado[item.id] = item
    return resultado


# ---------------------------------------------------------------- principal

def classificar(df: pd.DataFrame, regras: RegrasEmpresa, usar_ia: bool | None = None,
                setor: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Adiciona colunas `categoria`, `grupo`, `fonte_categoria` e `confianca`."""
    from .mapeador import tem_credencial_ia

    if usar_ia is None:
        usar_ia = tem_credencial_ia()
    df = df.copy()
    df["chave"] = df["descricao"].map(chave_descricao)
    cats, fontes, confs = [None] * len(df), [None] * len(df), [None] * len(df)

    pendentes: dict[tuple[str, str], list[int]] = {}
    for pos, (_, linha) in enumerate(df.iterrows()):
        chave, tipo = linha["chave"], linha["tipo"]
        r = regras.buscar(chave)
        if r and compativel(r.categoria, tipo):
            cats[pos], fontes[pos], confs[pos] = r.categoria, f"regra_{r.origem}", "alta"
            continue
        # palavra-chave específica ("taxa maquininha") vence categoria genérica
        # que o dono usou ("Financeiro")
        cat = regra_padrao(chave, tipo)
        if cat:
            cats[pos], fontes[pos], confs[pos] = cat, "regra_padrao", "media"
            continue
        orig = categoria_da_planilha(linha["categoria_original"], tipo, regras)
        if orig and compativel(orig, tipo):
            cats[pos], fontes[pos], confs[pos] = orig, "categoria_planilha", "alta"
            continue
        pendentes.setdefault((chave, tipo), []).append(pos)

    rel = {"total": len(df), "descricoes_para_ia": 0, "chamadas_ia": 0}
    if pendentes and usar_ia:
        itens, por_id = [], {}
        for n, ((chave, tipo), posicoes) in enumerate(pendentes.items()):
            exemplo = df.iloc[posicoes[0]]
            itens.append({"id": n, "descricao": mascarar(exemplo["descricao"]), "tipo": tipo,
                          "categoria_original": exemplo["categoria_original"]})
            por_id[n] = (chave, tipo, posicoes)
        rel["descricoes_para_ia"] = len(itens)
        rel["chamadas_ia"] = -(-len(itens) // TAMANHO_LOTE)
        try:
            respostas = classificar_com_ia(itens, setor=setor)
        except Exception as e:  # rede, chave, limite: segue sem IA
            respostas, rel["erro_ia"] = {}, f"{type(e).__name__}: {str(e)[:120]}"
        for n, item in respostas.items():
            chave, tipo, posicoes = por_id[n]
            if not compativel(item.categoria, tipo):
                continue
            for pos in posicoes:
                cats[pos], fontes[pos], confs[pos] = item.categoria, "ia", item.confianca
            if item.confianca != "baixa":
                regras.adicionar(chave, item.categoria, "ia")  # aprende

    for pos in range(len(df)):
        if cats[pos] is None:
            cats[pos] = PADRAO_SEM_CATEGORIA.get(df.iloc[pos]["tipo"], "Outras despesas")
            fontes[pos], confs[pos] = "sem_regra", "baixa"

    df["categoria"], df["fonte_categoria"], df["confianca"] = cats, fontes, confs
    df["grupo"] = df["categoria"].map(lambda c: CATEGORIAS[c]["grupo"])
    rel["por_fonte"] = df["fonte_categoria"].value_counts().to_dict()
    return df.drop(columns="chave"), rel
