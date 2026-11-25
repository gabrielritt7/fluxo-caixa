"""Normalização determinística: datas, valores em reais, tipo e status.

Tudo aqui é código puro e testável — a IA nunca faz conta nem converte número.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

import pandas as pd

from .mapeador import Mapeamento

MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}

PALAVRAS_TOTAL = ("total", "subtotal", "saldo", "soma", "resultado")

COLUNAS_CANONICAS = ["data", "descricao", "valor", "tipo", "categoria_original",
                     "vencimento", "status", "origem"]


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c)).lower().strip()


def _vazio(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (
        isinstance(v, str) and not v.strip()) or v is pd.NaT


# -------------------------------------------------------------------- valores

def parse_valor(v) -> float | None:
    """Converte valores no padrão brasileiro (e variações) para float.

    'R$ 1.234,56' → 1234.56 · '(1.234,56)' → -1234.56 · '-1.250' → -1250.0
    '1234.56' → 1234.56 · 1500 (número do Excel) → 1500.0 · '' → None
    """
    if _vazio(v) or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", " ")
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    s = re.sub(r"(?i)r\$|\s", "", s)
    if s.endswith("-"):
        negativo, s = True, s[:-1]
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        return None

    if "," in s and "." in s:
        # o separador que aparece por último é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        if len(partes) == 2:
            s = s.replace(",", ".")  # vírgula decimal (padrão BR)
        else:
            s = s.replace(",", "")  # 1,234,567
    elif s.count(".") > 1:
        s = s.replace(".", "")  # 1.234.567
    elif "." in s and len(s.split(".")[1]) == 3:
        s = s.replace(".", "")  # '1.500' no Brasil = mil e quinhentos
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negativo else n


# ---------------------------------------------------------------------- datas

def parse_data(v) -> date | None:
    """Datas em qualquer formato comum no Brasil (sempre dia antes do mês)."""
    if _vazio(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        # número serial do Excel (1 = 01/01/1900); faixa ~1954 a ~2064
        if 20000 <= v <= 60000:
            return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
        return None
    s = sem_acento(str(v))
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", s)  # remove hora

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)  # ISO
    if m:
        a, mes, d = map(int, m.groups())
        return _data_ou_none(a, mes, d)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)  # dd/mm/aa
    if m:
        d, mes, a = map(int, m.groups())
        return _data_ou_none(_ano(a), mes, d)
    m = re.fullmatch(r"(\d{1,2})[-/. ]+(?:de )?([a-z]{3})[a-z]*\.?[-/. ]+(?:de )?(\d{2,4})", s)
    if m and m.group(2) in MESES:  # 05/mar/2025, 5 de março de 2025
        return _data_ou_none(_ano(int(m.group(3))), MESES[m.group(2)], int(m.group(1)))
    return None


def _ano(a: int) -> int:
    return a + 2000 if a < 100 else a


def _data_ou_none(a: int, m: int, d: int) -> date | None:
    try:
        return date(a, m, d)
    except ValueError:
        return None


# ----------------------------------------------------------------- tipo/status

def parse_tipo(v) -> str | None:
    if _vazio(v):
        return None
    s = sem_acento(str(v))
    if s in ("c", "cr", "e", "+") or s.startswith(("entrada", "receita", "credito", "recebi", "venda")):
        return "entrada"
    if s in ("d", "db", "s", "-") or s.startswith(("saida", "despesa", "debito", "pagamento", "custo", "compra")):
        return "saida"
    return None


def parse_status(v) -> str | None:
    if _vazio(v):
        return None
    s = sem_acento(str(v))
    if s in ("x", "ok", "sim", "s") or s.startswith(("pago", "quitad", "recebid", "liquidad", "realizad", "baixad", "conciliad")):
        return "realizado"
    if s in ("nao", "n") or s.startswith(("pendente", "aberto", "em aberto", "a pagar", "a receber", "previsto", "agendad", "atrasad", "vencid")):
        return "pendente"
    return None


def _e_linha_de_total(descricao) -> bool:
    if _vazio(descricao):
        return False
    s = sem_acento(str(descricao))
    return s.startswith(PALAVRAS_TOTAL)


# ---------------------------------------------------------------- principal

def normalizar(df: pd.DataFrame, mp: Mapeamento, origem: str) -> tuple[pd.DataFrame, dict]:
    """Aplica o mapeamento e devolve (lançamentos canônicos, relatório)."""
    rel = {"linhas_lidas": len(df), "descartadas_total": 0,
           "descartadas_sem_valor": 0, "descartadas_sem_data": 0,
           "tipo_deduzido_pelo_sinal": 0}
    saida = []

    def col(nome):
        return df[nome] if nome and nome in df.columns else pd.Series([None] * len(df), index=df.index)

    c_data, c_desc = col(mp.coluna_data), col(mp.coluna_descricao)
    c_valor, c_ent, c_sai = col(mp.coluna_valor), col(mp.coluna_entrada), col(mp.coluna_saida)
    c_tipo, c_cat = col(mp.coluna_tipo), col(mp.coluna_categoria)
    c_venc, c_status = col(mp.coluna_vencimento), col(mp.coluna_status)

    for i in df.index:
        desc = c_desc[i]
        if _e_linha_de_total(desc) or (_vazio(desc) and _vazio(c_data[i]) and _vazio(c_venc[i])):
            # linha de total/subtotal, ou total "solto" sem data nem descrição
            if any(not _vazio(x) for x in (c_valor[i], c_ent[i], c_sai[i])):
                rel["descartadas_total"] += 1
            continue

        # valor + tipo
        tipo = parse_tipo(c_tipo[i])
        if mp.coluna_entrada or mp.coluna_saida:
            ent, sai = parse_valor(c_ent[i]) or 0.0, parse_valor(c_sai[i]) or 0.0
            if ent and not sai:
                valor, tipo = abs(ent), tipo or ("entrada" if ent > 0 else "saida")
            elif sai and not ent:
                valor, tipo = abs(sai), tipo or "saida"
            elif ent and sai:  # raro: os dois preenchidos → líquido
                liquido = ent - abs(sai)
                valor, tipo = abs(liquido), ("entrada" if liquido >= 0 else "saida")
            else:
                valor = None
        else:
            v = parse_valor(c_valor[i])
            valor = abs(v) if v is not None else None
            if v is not None and tipo is None:
                tipo = "saida" if v < 0 else "entrada"
                rel["tipo_deduzido_pelo_sinal"] += 1
        if not valor:
            rel["descartadas_sem_valor"] += 1
            continue

        data = parse_data(c_data[i])
        venc = parse_data(c_venc[i])
        if data is None and venc is None:
            rel["descartadas_sem_data"] += 1
            continue

        status = parse_status(c_status[i])
        if status is None:
            status = "realizado" if mp.coluna_status is None else "pendente"

        saida.append({
            "data": data or venc,
            "descricao": "" if _vazio(desc) else str(desc).strip(),
            "valor": round(valor, 2),
            "tipo": tipo,
            "categoria_original": None if _vazio(c_cat[i]) else str(c_cat[i]).strip(),
            "vencimento": venc,
            "status": status,
            "origem": origem,
        })

    resultado = pd.DataFrame(saida, columns=COLUNAS_CANONICAS)
    rel["lancamentos_validos"] = len(resultado)
    return resultado, rel
