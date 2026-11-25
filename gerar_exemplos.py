"""Gera planilhas de teste "bagunçadas", no estilo das que PMEs usam de verdade,
e um gabarito (exemplos/gabarito.json) com os totais corretos de cada uma."""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

PASTA = Path(__file__).parent / "exemplos"
random.seed(42)

RECEITAS = ["Venda balcão", "Venda cartão débito", "Venda cartão crédito", "Pix cliente",
            "Recebimento boleto", "Venda iFood", "Encomenda festa"]
DESPESAS = [("Fornecedor farinha", "Insumos"), ("Energia CEMIG", "Utilidades"),
            ("Aluguel loja", "Ocupação"), ("Folha de pagamento", "Pessoal"),
            ("DAS Simples Nacional", "Impostos"), ("Internet Vivo", "Utilidades"),
            ("Embalagens", "Insumos"), ("Manutenção forno", "Manutenção"),
            ("Taxa maquininha", "Financeiro"), ("Contador", "Serviços")]


def br(v: float, rs: bool = True) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}" if rs else s


def lancamentos(n: int, inicio: date, dias: int):
    out = []
    for _ in range(n):
        d = inicio + timedelta(days=random.randint(0, dias))
        if random.random() < 0.55:
            out.append((d, random.choice(RECEITAS), round(random.uniform(80, 3500), 2), "entrada", "Vendas"))
        else:
            desc, cat = random.choice(DESPESAS)
            out.append((d, desc, round(random.uniform(50, 6000), 2), "saida", cat))
    return sorted(out)


def gabarito(lancs):
    return {"n": len(lancs),
            "entradas": round(sum(l[2] for l in lancs if l[3] == "entrada"), 2),
            "saidas": round(sum(l[2] for l in lancs if l[3] == "saida"), 2)}


def padaria_simples():
    """Título no topo, linha em branco, coluna Tipo, linha de TOTAL no fim."""
    lancs = lancamentos(60, date(2025, 3, 1), 30)
    wb = Workbook()
    ws = wb.active
    ws.title = "Caixa"
    ws["A1"] = "PADARIA PÃO DOURADO - CONTROLE DE CAIXA MARÇO/2025"
    ws["A1"].font = Font(bold=True)
    ws.append([])
    ws.append(["Data", "Descrição", "Valor (R$)", "Tipo", "Categoria"])
    for d, desc, v, t, cat in lancs:
        ws.append([d, desc, v, "Entrada" if t == "entrada" else "Saída", cat])
    ws.append([])
    ws.append(["", "TOTAL", f"=SUM(C4:C{3 + len(lancs)})", "", ""])
    wb.save(PASTA / "padaria_simples.xlsx")
    return gabarito(lancs)


def clinica_colunas_separadas():
    """Entradas e saídas em colunas separadas, valores como TEXTO 'R$ 1.234,56',
    datas como texto, coluna de saldo, células mescladas e aba de Resumo."""
    lancs = lancamentos(80, date(2025, 1, 1), 89)
    wb = Workbook()
    ws = wb.active
    ws.title = "Movimento"
    ws.merge_cells("A1:E1")
    ws["A1"] = "Clínica Sorriso - Fluxo de Caixa 1º Tri"
    ws.merge_cells("A2:E2")
    ws["A2"] = "Responsável: financeiro"
    ws.append(["Dt. Lançamento", "Histórico", "Entradas", "Saídas", "Saldo"])
    ws.append(["", "Saldo anterior", "", "", br(15000)])
    saldo = 15000.0
    for d, desc, v, t, _ in lancs:
        desc = desc.replace("Venda", "Consulta") if t == "entrada" else desc
        saldo += v if t == "entrada" else -v
        ws.append([d.strftime("%d/%m/%Y"), desc,
                   br(v) if t == "entrada" else "", br(v) if t == "saida" else "", br(saldo)])
    ws.append(["", "Saldo final", "", "", br(saldo)])
    r = wb.create_sheet("Resumo")
    r.append(["Mês", "Receita", "Despesa", "Resultado"])
    for m in ("Jan", "Fev", "Mar"):
        r.append([m, br(random.uniform(20000, 40000)), br(random.uniform(15000, 35000)), ""])
    wb.save(PASTA / "clinica_colunas_separadas.xlsx")
    return gabarito(lancs)


def oficina_sinal():
    """CSV com ';', encoding Windows (latin-1), valor com sinal, data dd/m/aa."""
    lancs = lancamentos(50, date(2025, 2, 1), 27)
    linhas = ["data;descricao;valor;categoria"]
    for d, desc, v, t, cat in lancs:
        desc = desc.replace("Fornecedor farinha", "Peças auto").replace("Venda", "Serviço")
        val = br(v if t == "entrada" else -v, rs=False)
        linhas.append(f"{d.day}/{d.month}/{str(d.year)[2:]};{desc};{val};{cat}")
    (PASTA / "oficina_sinal.csv").write_bytes("\n".join(linhas).encode("cp1252"))
    return gabarito(lancs)


def loja_varias_abas():
    """Uma aba por mês, colunas fora de ordem, natureza C/D, status
    Pago/Pendente, vencimento, e algumas datas como número serial do Excel."""
    todos = []
    wb = Workbook()
    wb.remove(wb.active)
    for mes, nome in ((4, "Abril"), (5, "Maio"), (6, "Junho")):
        lancs = lancamentos(40, date(2025, mes, 1), 27)
        todos += lancs
        ws = wb.create_sheet(nome)
        ws.append(["Situação", "Dia", "Fornecedor/Cliente", "Natureza (C/D)", "Montante", "Vencimento"])
        for d, desc, v, t, _ in lancs:
            dia = (d - date(1899, 12, 30)).days if random.random() < 0.3 else d  # serial
            venc = d + timedelta(days=random.choice([0, 7, 15, 30]))
            ws.append([random.choice(["Pago", "Pago", "Pendente"]), dia, desc,
                       "C" if t == "entrada" else "D", v, venc])
    wb.save(PASTA / "loja_varias_abas.xlsx")
    return gabarito(todos)


MEMOS_BANCO_ENTRADA = ["PIX RECEBIDO - JOAO P SOUZA", "TED RECEBIDA 237 0001 COMERCIAL ABC",
                       "CRED STONE 12/03", "PIX RECEBIDO - 12.345.678/0001-90", "DEP DINHEIRO AG 1234"]
MEMOS_BANCO_SAIDA = ["PIX ENVIADO - MARIA S", "PAG BOLETO 34191.79001 01043.510047",
                     "COMPRA CARTAO - ATACADAO SA", "SISPAG FORNEC 004512", "PAG*MERCADOLIVRE",
                     "DEB AUTOMATICO CEMIG", "TARIFA PACOTE SERVICOS", "DARF 0220"]


def extrato_ofx():
    """Descrições como o banco escreve — a maioria nenhuma regra resolve."""
    lancs = [(d, random.choice(MEMOS_BANCO_ENTRADA if t == "entrada" else MEMOS_BANCO_SAIDA), v, t, c)
             for d, _, v, t, c in lancamentos(30, date(2025, 3, 1), 30)]
    trns = "".join(
        f"<STMTTRN>\n<TRNTYPE>{'CREDIT' if t == 'entrada' else 'DEBIT'}\n"
        f"<DTPOSTED>{d:%Y%m%d}120000[-3:BRT]\n<TRNAMT>{v if t == 'entrada' else -v:.2f}\n"
        f"<FITID>{i}\n<MEMO>{desc}\n</STMTTRN>\n"
        for i, (d, desc, v, t, _) in enumerate(lancs))
    texto = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX>\n<BANKMSGSRSV1><STMTTRNRS>"
             f"<STMTRS>\n<BANKTRANLIST>\n{trns}</BANKTRANLIST>\n</STMTRS></STMTTRNRS></BANKMSGSRSV1>\n</OFX>\n")
    (PASTA / "extrato_banco.ofx").write_text(texto, encoding="latin-1")
    return gabarito(lancs)


def cabecalho_estranho():
    """Cabeçalhos informais que nenhum sinônimo cobre — é aqui que a IA brilha."""
    lancs = lancamentos(40, date(2025, 5, 1), 30)
    wb = Workbook()
    ws = wb.active
    ws.title = "Planilha1"
    ws.append(["Quando", "O que foi", "Quanto", "E/S", "Obs"])
    for d, desc, v, t, _ in lancs:
        ws.append([d.strftime("%d/%m/%Y"), desc, br(v), "E" if t == "entrada" else "S", ""])
    wb.save(PASTA / "cabecalho_estranho.xlsx")
    return gabarito(lancs)


def demo_padaria():
    """Empresa de demonstração com números verossímeis (ago-out/2025).
    A história: a operação dá um pequeno lucro, mas pró-labore + parcela do forno
    consomem o caixa; um cliente de encomendas atrasou e o 13º vem aí."""
    rnd = random.Random(7)
    linhas = []  # (data, historico, categoria, valor, tipo, situacao, vencimento)

    def add(d, h, cat, v, t, sit="Pago", venc=None):
        linhas.append((d, h, cat, round(v, 2), t, sit, venc))

    d = date(2025, 8, 1)
    while d <= date(2025, 10, 31):
        if d.weekday() < 6:  # segunda a sábado
            fator = 1.25 if d.weekday() == 5 else 1.0
            add(d, "Vendas balcão (dinheiro/Pix)", "Vendas", rnd.uniform(520, 680) * fator, "Entrada")
            add(d, "CRED STONE vendas cartão", "Vendas", rnd.uniform(600, 760) * fator, "Entrada")
        if d.weekday() == 0:
            add(d, "Fornecedor Moinho Real", "Insumos", rnd.uniform(2050, 2350), "Saída")
        if d.weekday() == 2:
            add(d, "Laticínios Serra Verde", "Insumos", rnd.uniform(820, 980), "Saída")
        if d.weekday() == 4:
            add(d, "Encomendas de festa", "Vendas", rnd.uniform(300, 520), "Entrada")
        mes = d.month
        if d.day == 5:
            add(d, "Folha de pagamento", "Pessoal", 9800, "Saída")
            add(d, "Pró-labore Ana", "Sócios", 4000, "Saída")
        if d.day == 8:
            add(d, "Embalagens Plastimax", "Insumos", rnd.uniform(620, 760), "Saída")
        if d.day == 10:
            add(d, "Aluguel loja", "Ocupação", 4500, "Saída")
        if d.day == 15:
            add(d, "Energia CEMIG", "Utilidades", {8: 1120, 9: 1260, 10: 1940}[mes], "Saída")
            add(d, "Internet Vivo Fibra", "Utilidades", 149.9, "Saída")
        if d.day == 20:
            add(d, "DAS Simples Nacional", "Impostos", rnd.uniform(2050, 2200), "Saída")
            add(d, "FGTS e INSS", "Pessoal", 1420, "Saída")
            add(d, "Contador Silva & Assoc.", "Serviços", 600, "Saída")
        if d.day == 25:
            add(d, "Parcela empréstimo forno 7/24", "Financeiro", 1830, "Saída")
        if d.day == 28:
            add(d, "Taxa maquininha Stone", "Financeiro", rnd.uniform(430, 480), "Saída")
        d += timedelta(days=1)

    # pendências (contas a receber e a pagar)
    add(date(2025, 10, 10), "Encomenda Buffet Eventos Lima", "Vendas", 3200, "Entrada",
        "Pendente", date(2025, 10, 20))
    add(date(2025, 10, 27), "Fornecedor Moinho Real", "Insumos", 2240, "Saída",
        "Pendente", date(2025, 11, 10))
    add(date(2025, 10, 29), "Encomenda Colégio Santa Rita", "Vendas", 1850, "Entrada",
        "Pendente", date(2025, 11, 12))
    add(date(2025, 11, 28), "13º salário - 1ª parcela", "Pessoal", 4900, "Saída",
        "Pendente", date(2025, 11, 28))
    linhas.sort(key=lambda x: x[0])

    wb = Workbook()
    ws = wb.active
    ws.title = "Caixa"
    ws["A1"] = "PADARIA TRIGO BOM - FLUXO DE CAIXA"
    ws["A1"].font = Font(bold=True)
    ws.append(["Data", "Histórico", "Categoria", "Valor", "Tipo", "Situação", "Vencimento"])
    for l in linhas:
        ws.append(list(l))
    wb.save(PASTA / "demo_padaria_trigo_bom.xlsx")
    pagos = [l for l in linhas if l[5] == "Pago"]
    return {"n": len(linhas),
            "entradas": round(sum(l[3] for l in linhas if l[4] == "Entrada"), 2),
            "saidas": round(sum(l[3] for l in linhas if l[4] == "Saída"), 2),
            "pagos": len(pagos)}


def main():
    PASTA.mkdir(exist_ok=True)
    gab = {
        "padaria_simples.xlsx": padaria_simples(),
        "clinica_colunas_separadas.xlsx": clinica_colunas_separadas(),
        "oficina_sinal.csv": oficina_sinal(),
        "loja_varias_abas.xlsx": loja_varias_abas(),
        "extrato_banco.ofx": extrato_ofx(),
        "cabecalho_estranho.xlsx": cabecalho_estranho(),
        "demo_padaria_trigo_bom.xlsx": demo_padaria(),
    }
    (PASTA / "gabarito.json").write_text(json.dumps(gab, indent=2, ensure_ascii=False))
    for nome, g in gab.items():
        print(f"{nome:35} {g['n']:4} lançamentos")


if __name__ == "__main__":
    main()
