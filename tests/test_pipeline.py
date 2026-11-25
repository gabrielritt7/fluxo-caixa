import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from fluxocaixa import mapeador
from fluxocaixa.mapeador import Mapeamento, mapear, validar
from fluxocaixa.normalizador import parse_data, parse_status, parse_tipo, parse_valor
from fluxocaixa.pipeline import processar

EXEMPLOS = Path(__file__).parent.parent / "exemplos"


@pytest.fixture(scope="session", autouse=True)
def gerar():
    import gerar_exemplos
    gerar_exemplos.main()


# ------------------------------------------------------------- conversões

@pytest.mark.parametrize("entrada,esperado", [
    ("R$ 1.234,56", 1234.56), ("1.234,56", 1234.56), ("-1.250,00", -1250.0),
    ("(1.234,56)", -1234.56), ("1234.56", 1234.56), ("1.500", 1500.0),
    ("1,5", 1.5), ("R$ 350,00", 350.0), ("350,00-", -350.0),
    (1500, 1500.0), (99.9, 99.9), ("", None), ("abc", None), (None, None),
    ("1,234,567.89", 1234567.89),
])
def test_parse_valor(entrada, esperado):
    assert parse_valor(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("05/03/2025", date(2025, 3, 5)), ("5/3/25", date(2025, 3, 5)),
    ("2025-03-05", date(2025, 3, 5)), ("05.03.2025", date(2025, 3, 5)),
    ("05/mar/2025", date(2025, 3, 5)), ("5 de março de 2025", date(2025, 3, 5)),
    (45721, date(2025, 3, 5)), (pd.Timestamp("2025-03-05"), date(2025, 3, 5)),
    ("05/03/2025 14:30", date(2025, 3, 5)), ("31/02/2025", None), ("TOTAL", None),
])
def test_parse_data(entrada, esperado):
    assert parse_data(entrada) == esperado


def test_tipo_e_status():
    assert [parse_tipo(x) for x in ("C", "D", "E", "S", "Receita", "Saída", "x")] == \
        ["entrada", "saida", "entrada", "saida", "entrada", "saida", None]
    assert [parse_status(x) for x in ("Pago", "Pendente", "Em aberto", "OK", "?")] == \
        ["realizado", "pendente", "pendente", "realizado", None]


# ----------------------------------------------- arquivos completos (gabarito)



@pytest.mark.parametrize("arquivo", [
    "padaria_simples.xlsx", "clinica_colunas_separadas.xlsx", "oficina_sinal.csv",
    "loja_varias_abas.xlsx", "extrato_banco.ofx", "cabecalho_estranho.xlsx",
])
def test_totais_batem_com_gabarito(arquivo):
    gab = json.loads((EXEMPLOS / "gabarito.json").read_text())[arquivo]
    df = processar(EXEMPLOS / arquivo, usar_ia=False).lancamentos
    assert len(df) == gab["n"]
    assert round(df.loc[df.tipo == "entrada", "valor"].sum(), 2) == gab["entradas"]
    assert round(df.loc[df.tipo == "saida", "valor"].sum(), 2) == gab["saidas"]


def test_aba_de_resumo_e_ignorada():
    res = processar(EXEMPLOS / "clinica_colunas_separadas.xlsx", usar_ia=False)
    usadas = {t.origem.split("› ")[1]: t.usada for t in res.tabelas}
    assert usadas == {"Movimento": True, "Resumo": False}


def test_recusa_quando_nao_da_para_saber_entrada_ou_saida():
    df = pd.DataFrame({"Data": ["01/03/2025"] * 3, "Descr": ["a", "b", "c"],
                       "Valor": ["100,00", "200,00", "50,00"]})
    mp = mapear(df, usar_ia=False)
    assert any("distinguir entradas" in p for p in validar(mp, df))


# ------------------------------------------------------- caminho da IA (mock)

class ClienteFalso:
    """Imita anthropic.Anthropic() para testar sem rede e sem custo."""

    def __init__(self, mp: Mapeamento):
        self.chamadas = []
        resposta = SimpleNamespace(stop_reason="end_turn", parsed_output=mp, model="claude-opus-5-5")
        cliente = self

        class _Msgs:
            def parse(self, **kw):
                cliente.chamadas.append(kw)
                return resposta

        self.beta = SimpleNamespace(messages=_Msgs())


def _df_exemplo():
    return pd.DataFrame({"Quando": ["01/03/2025", "02/03/2025"], "O que foi": ["x", "y"],
                         "Quanto": ["10,00", "20,00"], "E/S": ["E", "S"]})


def test_ia_recebe_so_amostra_e_mapeamento_e_validado():
    mp = Mapeamento(e_planilha_de_lancamentos=True, coluna_data="Quando",
                    coluna_descricao="O que foi", coluna_valor="Quanto", coluna_entrada=None,
                    coluna_saida=None, coluna_tipo="E/S", coluna_categoria=None,
                    coluna_vencimento=None, coluna_status=None, confianca="alta", observacoes="")
    cliente = ClienteFalso(mp)
    out = mapeador.mapear_com_ia(_df_exemplo(), cliente=cliente)
    kw = cliente.chamadas[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_format"] is Mapeamento
    assert "Quanto" in kw["messages"][0]["content"]
    assert out.metodo.startswith("ia") and validar(out, _df_exemplo()) == []


def test_coluna_inventada_pela_ia_cai_para_heuristica(monkeypatch):
    ruim = Mapeamento(e_planilha_de_lancamentos=True, coluna_data="Data Inexistente",
                      coluna_descricao=None, coluna_valor="Quanto", coluna_entrada=None,
                      coluna_saida=None, coluna_tipo="E/S", coluna_categoria=None,
                      coluna_vencimento=None, coluna_status=None, confianca="alta", observacoes="")
    monkeypatch.setattr(mapeador, "mapear_com_ia", lambda df: ruim)
    mp = mapear(_df_exemplo(), usar_ia=True)
    assert mp.metodo == "heuristica" and mp.coluna_data == "Quando"
