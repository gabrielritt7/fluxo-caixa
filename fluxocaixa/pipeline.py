"""Orquestra: ler arquivo → mapear colunas → (confirmar) → normalizar."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .leitor import ler_arquivo
from .mapeador import Mapeamento, mapear, validar
from .normalizador import COLUNAS_CANONICAS, normalizar


@dataclass
class ResultadoTabela:
    origem: str
    mapeamento: Mapeamento
    problemas: list[str]
    relatorio: dict
    usada: bool


@dataclass
class ResultadoArquivo:
    lancamentos: pd.DataFrame
    tabelas: list[ResultadoTabela] = field(default_factory=list)


def processar(caminho, usar_ia: bool | None = None, confirmar=None) -> ResultadoArquivo:
    """`confirmar(origem, mapeamento) -> Mapeamento | None` é o gancho da tela
    "Entendemos assim, está certo?". None = usuário descartou a tabela."""
    lido = ler_arquivo(caminho)
    if lido.lancamentos_ofx is not None:
        df = lido.lancamentos_ofx.assign(origem=lido.caminho.name)[COLUNAS_CANONICAS]
        return ResultadoArquivo(lancamentos=df)

    partes, resultados = [], []
    for tabela in lido.tabelas:
        mp = mapear(tabela.dados, usar_ia=usar_ia)
        problemas = validar(mp, tabela.dados)
        usada = mp.e_planilha_de_lancamentos and not problemas
        if usada and confirmar is not None:
            confirmado = confirmar(tabela.origem, mp)
            usada = confirmado is not None
            mp = confirmado or mp
        rel = {}
        if usada:
            df, rel = normalizar(tabela.dados, mp, tabela.origem)
            # descarta abas que "pareciam" lançamentos mas quase nada aproveitou
            if len(df) < 3:
                usada = False
                problemas.append(f"só {len(df)} lançamento(s) válido(s)")
            else:
                partes.append(df)
        resultados.append(ResultadoTabela(tabela.origem, mp, problemas, rel, usada))

    lanc = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS_CANONICAS)
    if len(lanc):
        lanc = lanc.sort_values("data", kind="stable").reset_index(drop=True)
    return ResultadoArquivo(lancamentos=lanc, tabelas=resultados)
