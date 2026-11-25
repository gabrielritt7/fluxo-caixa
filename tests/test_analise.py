from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from fluxocaixa import classificador as clf
from fluxocaixa.classificador import (ItemClassificado, LoteClassificado, RegrasEmpresa,
                                      chave_descricao, classificar, mascarar)
from fluxocaixa.diagnostico import Acao, Diagnostico, diagnostico_modelo, numeros_inventados
from fluxocaixa.metricas import calcular


def lanc(rows):
    """rows: (data, descricao, valor, tipo, status, vencimento, categoria_original)"""
    cols = ["data", "descricao", "valor", "tipo", "status", "vencimento", "categoria_original"]
    df = pd.DataFrame([r + (None,) * (len(cols) - len(r)) for r in rows], columns=cols)
    df["status"] = df["status"].fillna("realizado")
    df["origem"] = "teste"
    return df


# ------------------------------------------------------------- classificação

def test_chave_e_mascara():
    assert chave_descricao("PIX 12/03 Fornecedor Ávila NF 4521") == "pix fornecedor avila nf"
    assert "12.345.678" not in mascarar("PIX RECEBIDO - 12.345.678/0001-90")


@pytest.mark.parametrize("desc,tipo,esperado", [
    ("DEB AUTOMATICO CEMIG", "saida", "Energia, água e telecom"),
    ("DAS Simples Nacional 03/2025", "saida", "Impostos sobre vendas"),
    ("Taxa maquininha Stone", "saida", "Taxas de cartão e comissões"),
    ("CRED STONE 12/03", "entrada", "Vendas e serviços"),
    ("Pró-labore Ana", "saida", "Retirada dos sócios"),
    ("Pagamento das contas da loja", "saida", "Outras despesas"),  # "das" não é DAS
])
def test_regras_padrao(desc, tipo, esperado):
    out, _ = classificar(lanc([(date(2025, 3, 1), desc, 10.0, tipo)]), RegrasEmpresa(), usar_ia=False)
    assert out.iloc[0]["categoria"] == esperado


def test_palavra_chave_vence_categoria_generica_da_planilha():
    df = lanc([(date(2025, 3, 1), "Taxa maquininha", 50.0, "saida", None, None, "Financeiro"),
               (date(2025, 3, 1), "Boleto 123", 80.0, "saida", None, None, "Insumos"),
               (date(2025, 3, 1), "Fulano", 300.0, "entrada", None, None, "Serviços"),
               (date(2025, 3, 1), "Beltrano", 90.0, "saida", None, None, "Serviços")])
    out, _ = classificar(df, RegrasEmpresa(), usar_ia=False)
    assert list(out["categoria"]) == ["Taxas de cartão e comissões", "Fornecedores e insumos",
                                      "Vendas e serviços", "Serviços profissionais"]


def test_correcao_do_usuario_vira_regra_e_tem_prioridade(tmp_path):
    regras = RegrasEmpresa()
    regras.corrigir("PIX ENVIADO - MARIA S", "Pessoal e encargos")
    regras.corrigir("", "Marketing", palavra_chave="agencia lua")
    caminho = tmp_path / "r.json"
    regras.salvar(caminho)
    regras = RegrasEmpresa.carregar(caminho)
    df = lanc([(date(2025, 3, 5), "PIX ENVIADO - MARIA S", 1500.0, "saida"),
               (date(2025, 3, 6), "Agência Lua - campanha março", 900.0, "saida")])
    out, _ = classificar(df, regras, usar_ia=False)
    assert list(out["categoria"]) == ["Pessoal e encargos", "Marketing"]
    assert set(out["fonte_categoria"]) == {"regra_usuario"}


def test_ia_recebe_descricoes_unicas_e_aprende(monkeypatch):
    chamadas = []

    def falso(itens, setor=None, cliente=None):
        chamadas.append(itens)
        return {it["id"]: ItemClassificado(id=it["id"], confianca="alta",
                                           categoria="Fornecedores e insumos")
                for it in itens}

    monkeypatch.setattr(clf, "classificar_com_ia", falso)
    df = lanc([(date(2025, 3, d), f"PAG*MERCADOLIVRE {d}", 100.0, "saida") for d in range(1, 11)])
    regras = RegrasEmpresa()
    out, rel = classificar(df, regras, usar_ia=True)
    assert len(chamadas[0]) == 1  # 10 lançamentos, 1 descrição distinta
    assert (out["fonte_categoria"] == "ia").all()
    # segunda vez: resolvido pela regra aprendida, sem chamar a IA
    out2, rel2 = classificar(df, regras, usar_ia=True)
    assert len(chamadas) == 1 and (out2["fonte_categoria"] == "regra_ia").all()


def test_ia_nao_pode_inventar_categoria():
    with pytest.raises(Exception):
        LoteClassificado(itens=[{"id": 0, "categoria": "Categoria Inventada", "confianca": "alta"}])


def test_classificar_com_ia_monta_chamada():
    capturado = {}

    class Msgs:
        def parse(self, **kw):
            capturado.update(kw)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=LoteClassificado(
                itens=[ItemClassificado(id=0, categoria="Marketing", confianca="media")]))

    cliente = SimpleNamespace(beta=SimpleNamespace(messages=Msgs()))
    r = clf.classificar_com_ia([{"id": 0, "descricao": "META ADS", "tipo": "saida",
                                 "categoria_original": None}], setor="padaria", cliente=cliente)
    assert r[0].categoria == "Marketing"
    assert capturado["output_format"] is LoteClassificado and "padaria" in capturado["system"]


# -------------------------------------------------------------------- métricas

def empresa_exemplo():
    """60 dias (01/03 a 29/04), contas redondas para conferir à mão."""
    rows = []
    for mes in (3, 4):
        rows += [(date(2025, mes, 5), "Venda balcão", 20000.0, "entrada"),
                 (date(2025, mes, 6), "Fornecedor farinha", 8000.0, "saida"),   # variável
                 (date(2025, mes, 10), "Aluguel loja", 4000.0, "saida"),         # fixa
                 (date(2025, mes, 15), "Folha de pagamento", 6000.0, "saida")]   # fixa
    rows.append((date(2025, 4, 29), "Venda balcão", 0.01, "entrada"))  # fixa "hoje"
    rows += [(date(2025, 4, 20), "Cliente ABC", 3000.0, "entrada", "pendente", date(2025, 4, 25)),
             (date(2025, 4, 28), "Aluguel loja", 4000.0, "saida", "pendente", date(2025, 5, 10))]
    out, _ = classificar(lanc(rows), RegrasEmpresa(), usar_ia=False)
    return out


def test_metricas_conferidas_a_mao():
    m = calcular(empresa_exemplo(), saldo_inicial=10000)
    assert m["referencia"]["hoje"] == "2025-04-29" and m["referencia"]["dias_na_janela"] == 60
    mensal = m["mensal"]
    # competência: receita 40.000,01 + 3.000 pendente = 43.000,01 em 60 dias → /2
    assert mensal["receita"] == pytest.approx(21500.0, abs=0.01)
    assert mensal["custos_variaveis"] == 8000.0
    assert mensal["despesas_fixas"] == 12000.0  # 20.000 pago + 4.000 pendente = 24.000 / 2
    assert m["margem_contribuicao_pct"] == pytest.approx(62.79, abs=0.01)
    assert m["ponto_equilibrio_mensal"] == pytest.approx(12000 / (13500 / 21500), abs=0.01)
    # caixa: só realizado → 10.000 + 40.000,01 − 36.000
    assert m["saldo_atual"] == pytest.approx(14000.01, abs=0.001)
    assert m["a_receber"]["vencidas"] == 3000.0 and m["a_pagar"]["proximos_30_dias"] == 4000.0
    assert any(a["codigo"] == "recebiveis_vencidos" for a in m["alertas"])


def test_projecao_nao_conta_aluguel_duas_vezes():
    m = calcular(empresa_exemplo(), saldo_inicial=10000)
    # 30 dias: +21.500 receita −8.000 var −12.000 fixas (aluguel pendente de 10/05 já
    # está dentro das fixas estimadas, não soma de novo) ⇒ variação +1.500
    assert m["projecao"]["saldo_30d"] == pytest.approx(14000.01 + 1500, abs=1)
    # recebível vencido (3.000) fica de fora — conservador
    assert m["projecao"]["primeiro_dia_negativo"] is None


def test_alerta_de_caixa_negativo_e_ja_negativo():
    m = calcular(empresa_exemplo(), saldo_inicial=-20000)
    assert m["alertas"][0]["codigo"] == "caixa_ja_negativo"
    m2 = calcular(empresa_exemplo(), saldo_inicial=-15000)  # saldo hoje −999,99 → negativo
    assert m2["alertas"][0]["codigo"] == "caixa_ja_negativo"


# ------------------------------------------------------------------- diagnóstico

def test_diagnostico_modelo_sempre_tem_3_acoes():
    m = calcular(empresa_exemplo(), saldo_inicial=10000)
    d = diagnostico_modelo(m)
    assert len(d.acoes) == 3 and d.situacao in ("atencao", "critico", "saudavel")
    assert numeros_inventados(d, m) == []


def test_detecta_numero_inventado_pela_ia():
    m = calcular(empresa_exemplo(), saldo_inicial=10000)
    d = Diagnostico(situacao="atencao",
                    resumo="Seu saldo é R$ 14.000 e vai faltar R$ 77.777 em junho.",
                    pontos_de_atencao=[], acoes=[Acao(titulo="x", porque="y", prazo="este mês")] * 3)
    assert numeros_inventados(d, m) == [77777.0]


def test_conta_extraordinaria_entra_por_cima_da_media():
    """13º salário não é recorrente: não pode ser 'absorvido' pela média da folha."""
    base = calcular(empresa_exemplo(), saldo_inicial=10000)["projecao"]["saldo_30d"]
    df = empresa_exemplo()
    extra = lanc([(date(2025, 5, 20), "13º salário - 1ª parcela", 3000.0, "saida", "pendente",
                   date(2025, 5, 20))])
    extra, _ = classificar(extra, RegrasEmpresa(), usar_ia=False)
    m = calcular(pd.concat([df, extra], ignore_index=True), hoje=date(2025, 4, 29), saldo_inicial=10000)
    assert m["projecao"]["saldo_30d"] == pytest.approx(base - 3000, abs=1)


def test_painel_gera_html_com_dados(tmp_path):
    from painel import gerar_html
    m = calcular(empresa_exemplo(), saldo_inicial=10000)
    h = gerar_html({"metricas": m, "diagnostico": diagnostico_modelo(m).model_dump()},
                   "Padaria </script> Teste", "padaria")
    assert h.startswith("<!doctype html>") and "{{" not in h
    assert "Padaria <\\/script> Teste" in h  # nome não quebra o <script>
