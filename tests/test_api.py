import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fluxocaixa.api import create_app

EXEMPLOS = Path(__file__).parent.parent / "exemplos"


@pytest.fixture(scope="module", autouse=True)
def gerar():
    import gerar_exemplos
    gerar_exemplos.main()


@pytest.fixture()
def cli(tmp_path):
    return TestClient(create_app(tmp_path / "t.db", tmp_path / "up", usar_ia=False))


def nova_empresa(cli, **kw):
    r = cli.post("/empresas", json={"nome": "Padaria Teste", "setor": "padaria", **kw})
    assert r.status_code == 201
    return r.json()["id"]


def enviar(cli, eid, nome):
    with open(EXEMPLOS / nome, "rb") as f:
        return cli.post(f"/empresas/{eid}/uploads", files={"arquivo": (nome, f)})


def test_fluxo_completo_upload_confirmacao_analise(cli):
    eid = nova_empresa(cli, saldo_inicial=18000)
    r = enviar(cli, eid, "demo_padaria_trigo_bom.xlsx")
    assert r.status_code == 201
    previa = r.json()
    t = previa["tabelas"][0]
    assert previa["status"] == "aguardando_confirmacao" and t["sugerida"]
    assert t["mapeamento"]["coluna_valor"] == "Valor" and len(t["amostra"]) == 8
    assert t["origem"] == "demo_padaria_trigo_bom.xlsx › Caixa"

    r = cli.post(f"/empresas/{eid}/uploads/{previa['id']}/confirmar", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    gab = json.loads((EXEMPLOS / "gabarito.json").read_text())["demo_padaria_trigo_bom.xlsx"]
    assert res["novos"] == gab["n"] and res["ja_existiam"] == 0

    a = cli.get(f"/empresas/{eid}/analise").json()
    assert a["metricas"]["saldo_atual"] == pytest.approx(8124, abs=1)
    assert len(a["diagnostico"]["acoes"]) == 3
    p = cli.get(f"/empresas/{eid}/painel")
    assert p.status_code == 200 and "Padaria Teste" in p.text


def test_reenviar_mesma_planilha_nao_duplica_e_lembra_mapeamento(cli):
    eid = nova_empresa(cli)
    u1 = enviar(cli, eid, "oficina_sinal.csv").json()
    cli.post(f"/empresas/{eid}/uploads/{u1['id']}/confirmar", json={})
    u2 = enviar(cli, eid, "oficina_sinal.csv").json()
    assert u2["tabelas"][0]["mapeamento"]["metodo"] == "memorizado"
    r = cli.post(f"/empresas/{eid}/uploads/{u2['id']}/confirmar", json={}).json()
    assert r["novos"] == 0 and r["ja_existiam"] == 50
    assert cli.get(f"/empresas/{eid}/lancamentos").json()["total"] == 50


def test_usuario_corrige_mapeamento_errado(cli):
    eid = nova_empresa(cli)
    previa = enviar(cli, eid, "cabecalho_estranho.xlsx").json()
    # simula a tela: usuário diz que "E/S" não é tipo → fica impossível saber entrada/saída
    r = cli.post(f"/empresas/{eid}/uploads/{previa['id']}/confirmar",
                 json={"tabelas": [{"indice": 0, "mapeamento": {"coluna_tipo": None}}]})
    assert r.status_code == 422 and "distinguir" in json.dumps(r.json(), ensure_ascii=False)
    # coluna inventada também é recusada
    r = cli.post(f"/empresas/{eid}/uploads/{previa['id']}/confirmar",
                 json={"tabelas": [{"indice": 0, "mapeamento": {"coluna_data": "Não existe"}}]})
    assert r.status_code == 422
    # mapeamento correto passa
    r = cli.post(f"/empresas/{eid}/uploads/{previa['id']}/confirmar",
                 json={"tabelas": [{"indice": 0, "mapeamento": {"coluna_tipo": "E/S"}}]})
    assert r.status_code == 200 and r.json()["novos"] == 40


def test_aba_de_resumo_fica_de_fora_e_pode_ser_escolhida(cli):
    eid = nova_empresa(cli)
    previa = enviar(cli, eid, "clinica_colunas_separadas.xlsx").json()
    sug = {t["origem"].split("› ")[1]: t["sugerida"] for t in previa["tabelas"]}
    assert sug == {"Movimento": True, "Resumo": False}


def test_correcao_de_categoria_aprende_e_aplica_nos_iguais(cli):
    eid = nova_empresa(cli)
    u = enviar(cli, eid, "extrato_banco.ofx").json()
    assert u["formato"] == "ofx" and u["lancamentos_ofx"] == 30
    res = cli.post(f"/empresas/{eid}/uploads/{u['id']}/confirmar", json={}).json()
    assert res["para_revisar"] > 0

    revisar = cli.get(f"/empresas/{eid}/lancamentos", params={"confianca": "baixa", "busca": "mercadolivre"}).json()
    alvo = revisar["itens"][0]
    iguais = cli.get(f"/empresas/{eid}/lancamentos", params={"busca": "mercadolivre"}).json()["total"]
    r = cli.patch(f"/empresas/{eid}/lancamentos/{alvo['id']}",
                  json={"categoria": "Fornecedores e insumos", "aplicar_em": "iguais"}).json()
    assert r["atualizados"] == iguais and r["regra_criada"]["tipo"] == "descricao_igual"

    # palavra-chave: "pix enviado" vale para qualquer PIX ENVIADO
    pix = cli.get(f"/empresas/{eid}/lancamentos", params={"busca": "pix enviado"}).json()["itens"]
    if pix:
        r = cli.patch(f"/empresas/{eid}/lancamentos/{pix[0]['id']}",
                      json={"categoria": "Pessoal e encargos", "aplicar_em": "palavra",
                            "palavra_chave": "PIX ENVIADO"})
        assert r.status_code == 200 and r.json()["regra_criada"]["chave"] == "pix enviado"

    # próximo extrato: a regra aprendida já classifica sozinha
    u2 = enviar(cli, eid, "extrato_banco.ofx").json()
    cli.post(f"/empresas/{eid}/uploads/{u2['id']}/confirmar", json={})
    ml = cli.get(f"/empresas/{eid}/lancamentos", params={"busca": "mercadolivre"}).json()["itens"]
    assert {l["categoria"] for l in ml} == {"Fornecedores e insumos"}


def test_validacoes_da_correcao(cli):
    eid = nova_empresa(cli)
    u = enviar(cli, eid, "padaria_simples.xlsx").json()
    cli.post(f"/empresas/{eid}/uploads/{u['id']}/confirmar", json={})
    venda = cli.get(f"/empresas/{eid}/lancamentos", params={"busca": "venda balcao"}).json()["itens"][0]
    assert cli.patch(f"/empresas/{eid}/lancamentos/{venda['id']}",
                     json={"categoria": "Aluguel e ocupação"}).status_code == 422  # saída numa entrada
    assert cli.patch(f"/empresas/{eid}/lancamentos/{venda['id']}",
                     json={"categoria": "Inventada"}).status_code == 422
    assert cli.patch(f"/empresas/{eid}/lancamentos/{venda['id']}",
                     json={"categoria": "Outras receitas", "aplicar_em": "palavra",
                           "palavra_chave": "xyz"}).status_code == 422


def test_isolamento_entre_empresas_e_exclusao(cli):
    a, b = nova_empresa(cli), nova_empresa(cli)
    u = enviar(cli, a, "padaria_simples.xlsx").json()
    cli.post(f"/empresas/{a}/uploads/{u['id']}/confirmar", json={})
    assert cli.get(f"/empresas/{b}/lancamentos").json()["total"] == 0
    assert cli.get(f"/empresas/{b}/uploads/{u['id']}").status_code == 404
    lid = cli.get(f"/empresas/{a}/lancamentos").json()["itens"][0]["id"]
    assert cli.patch(f"/empresas/{b}/lancamentos/{lid}", json={"categoria": "Marketing"}).status_code == 404
    r = cli.delete(f"/empresas/{a}/uploads/{u['id']}").json()
    assert r["lancamentos_removidos"] == 60
    assert cli.get(f"/empresas/{a}/analise").status_code == 409


def test_arquivo_invalido(cli):
    eid = nova_empresa(cli)
    r = cli.post(f"/empresas/{eid}/uploads", files={"arquivo": ("virus.exe", b"MZ...")})
    assert r.status_code == 415
    r = cli.post(f"/empresas/{eid}/uploads", files={"arquivo": ("vazio.csv", b"\n\n")})
    assert r.status_code == 422
    assert cli.post("/empresas/999/uploads", files={"arquivo": ("a.csv", b"x")}).status_code == 404


def test_confirmar_duas_vezes(cli):
    eid = nova_empresa(cli)
    u = enviar(cli, eid, "padaria_simples.xlsx").json()
    assert cli.post(f"/empresas/{eid}/uploads/{u['id']}/confirmar", json={}).status_code == 200
    assert cli.post(f"/empresas/{eid}/uploads/{u['id']}/confirmar", json={}).status_code == 409


def test_status_pendente_vira_pago_ao_reenviar(cli, tmp_path):
    import openpyxl
    eid = nova_empresa(cli, saldo_inicial=0)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Data", "Descrição", "Valor", "Tipo", "Situação", "Vencimento"])
    ws.append(["01/03/2025", "Venda balcão", 1000, "Entrada", "Pago", None])
    ws.append(["02/03/2025", "Cliente ABC", 500, "Entrada", "Pendente", "10/03/2025"])
    ws.append(["03/03/2025", "Aluguel loja", 300, "Saída", "Pago", None])
    p = tmp_path / "caixa.xlsx"; wb.save(p)
    for situacao in ("Pendente", "Pago"):
        ws["E3"] = situacao; wb.save(p)
        with open(p, "rb") as f:
            u = cli.post(f"/empresas/{eid}/uploads", files={"arquivo": ("caixa.xlsx", f)}).json()
        cli.post(f"/empresas/{eid}/uploads/{u['id']}/confirmar", json={})
    itens = cli.get(f"/empresas/{eid}/lancamentos").json()["itens"]
    assert len(itens) == 3
    assert next(l for l in itens if l["descricao"] == "Cliente ABC")["status"] == "realizado"
