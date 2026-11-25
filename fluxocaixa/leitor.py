"""Leitura de arquivos financeiros (xlsx, xls, csv, ofx).

Responsabilidade: transformar o arquivo em tabelas "cruas" (DataFrames com
cabeçalho detectado). Nenhuma interpretação financeira acontece aqui.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

LINHAS_BUSCA_CABECALHO = 25


@dataclass
class Tabela:
    """Uma tabela encontrada no arquivo (uma aba, ou o CSV inteiro)."""

    origem: str  # ex.: "planilha.xlsx › Março"
    dados: pd.DataFrame  # colunas = cabeçalho detectado, valores crus
    linha_cabecalho: int  # índice (0-based) da linha de cabeçalho no original


@dataclass
class ArquivoLido:
    caminho: Path
    tabelas: list[Tabela] = field(default_factory=list)
    # Para OFX já sai no formato canônico, sem precisar de mapeamento
    lancamentos_ofx: pd.DataFrame | None = None


# ---------------------------------------------------------------- utilidades

def _e_texto_de_cabecalho(v) -> bool:
    """Célula que parece nome de coluna: texto curto, não número, não data."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    if isinstance(v, (int, float, datetime, pd.Timestamp)):
        return False
    s = str(v).strip()
    if not s or len(s) > 40:
        return False
    if re.fullmatch(r"[-+R$\s\d.,()]+", s):  # número/valor em texto
        return False
    if re.fullmatch(r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}", s):  # data em texto
        return False
    return True


def detectar_linha_cabecalho(bruto: pd.DataFrame) -> int | None:
    """Acha a linha de cabeçalho: a que tem mais células com 'cara' de nome de
    coluna (mínimo 2), olhando só o topo. Ignora títulos de 1 célula."""
    melhor, melhor_pontos = None, 1
    for i in range(min(LINHAS_BUSCA_CABECALHO, len(bruto))):
        pontos = sum(_e_texto_de_cabecalho(v) for v in bruto.iloc[i].tolist())
        if pontos > melhor_pontos:
            melhor, melhor_pontos = i, pontos
    return melhor


def _nomes_unicos(valores: list) -> list[str]:
    nomes, vistos = [], {}
    for i, v in enumerate(valores):
        nome = str(v).strip() if _e_texto_de_cabecalho(v) else f"coluna_{i + 1}"
        if nome in vistos:
            vistos[nome] += 1
            nome = f"{nome}_{vistos[nome]}"
        else:
            vistos[nome] = 0
        nomes.append(nome)
    return nomes


def _tabela_de_bruto(bruto: pd.DataFrame, origem: str) -> Tabela | None:
    bruto = bruto.dropna(how="all").dropna(axis=1, how="all")
    if bruto.empty:
        return None
    bruto = bruto.reset_index(drop=True)
    linha = detectar_linha_cabecalho(bruto)
    if linha is None:
        return None
    dados = bruto.iloc[linha + 1 :].copy()
    dados.columns = _nomes_unicos(bruto.iloc[linha].tolist())
    dados = dados.dropna(how="all").reset_index(drop=True)
    if len(dados) < 2:
        return None
    return Tabela(origem=origem, dados=dados, linha_cabecalho=linha)


# ------------------------------------------------------------------ leitores

def _ler_excel(caminho: Path) -> list[Tabela]:
    abas = pd.read_excel(caminho, sheet_name=None, header=None, dtype=object)
    tabelas = []
    for nome_aba, bruto in abas.items():
        t = _tabela_de_bruto(bruto, f"{caminho.name} › {nome_aba}")
        if t is not None:
            tabelas.append(t)
    return tabelas


def _decodificar(conteudo: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _ler_csv(caminho: Path) -> list[Tabela]:
    texto = _decodificar(caminho.read_bytes())
    try:
        sep = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t|").delimiter
    except csv.Error:
        sep = ";" if texto.count(";") > texto.count(",") else ","
    bruto = pd.read_csv(io.StringIO(texto), sep=sep, header=None, dtype=object,
                        skip_blank_lines=False, engine="python")
    t = _tabela_de_bruto(bruto, caminho.name)
    return [t] if t else []


def _ler_ofx(caminho: Path) -> pd.DataFrame:
    """OFX (extrato bancário) já tem estrutura fixa: não precisa de IA."""
    texto = _decodificar(caminho.read_bytes())

    def campo(bloco: str, tag: str) -> str | None:
        m = re.search(rf"<{tag}>([^<\r\n]*)", bloco, re.IGNORECASE)
        return m.group(1).strip() if m else None

    linhas = []
    for bloco in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", texto, re.S | re.I):
        valor = float((campo(bloco, "TRNAMT") or "0").replace(",", "."))
        data = campo(bloco, "DTPOSTED") or ""
        linhas.append({
            "data": pd.to_datetime(data[:8], format="%Y%m%d", errors="coerce"),
            "descricao": campo(bloco, "MEMO") or campo(bloco, "NAME") or "",
            "valor": abs(valor),
            "tipo": "entrada" if valor >= 0 else "saida",
            "categoria_original": None,
            "vencimento": pd.NaT,
            "status": "realizado",
        })
    return pd.DataFrame(linhas)


def ler_arquivo(caminho: str | Path) -> ArquivoLido:
    caminho = Path(caminho)
    ext = caminho.suffix.lower()
    lido = ArquivoLido(caminho=caminho)
    if ext in (".xlsx", ".xlsm", ".xls"):
        lido.tabelas = _ler_excel(caminho)
    elif ext in (".csv", ".txt"):
        lido.tabelas = _ler_csv(caminho)
    elif ext == ".ofx":
        lido.lancamentos_ofx = _ler_ofx(caminho)
    else:
        raise ValueError(f"Formato não suportado: {ext}")
    return lido
