"""Plano de contas simplificado para PMEs + regras de palavras-chave do Brasil.

`grupo` alimenta as métricas:
  receita          → faturamento operacional
  custo_variavel   → acompanha as vendas (insumos, comissões, taxas de cartão, impostos sobre venda)
  despesa_fixa     → existe mesmo sem vender (aluguel, folha, internet...)
  financeiro       → juros, empréstimos, tarifas bancárias
  socios           → retiradas e pró-labore
  investimento     → equipamentos, reformas
  nao_operacional  → aportes, transferências entre contas, outras receitas
"""
from __future__ import annotations

CATEGORIAS: dict[str, dict] = {
    # entradas
    "Vendas e serviços":            {"tipo": "entrada", "grupo": "receita"},
    "Outras receitas":              {"tipo": "entrada", "grupo": "nao_operacional"},
    "Aportes e empréstimos recebidos": {"tipo": "entrada", "grupo": "nao_operacional"},
    # saídas
    "Fornecedores e insumos":       {"tipo": "saida", "grupo": "custo_variavel"},
    "Impostos sobre vendas":        {"tipo": "saida", "grupo": "custo_variavel"},
    "Taxas de cartão e comissões":  {"tipo": "saida", "grupo": "custo_variavel"},
    "Frete e logística":            {"tipo": "saida", "grupo": "custo_variavel"},
    "Pessoal e encargos":           {"tipo": "saida", "grupo": "despesa_fixa"},
    "Aluguel e ocupação":           {"tipo": "saida", "grupo": "despesa_fixa"},
    "Energia, água e telecom":      {"tipo": "saida", "grupo": "despesa_fixa"},
    "Serviços profissionais":       {"tipo": "saida", "grupo": "despesa_fixa"},
    "Sistemas e software":          {"tipo": "saida", "grupo": "despesa_fixa"},
    "Marketing":                    {"tipo": "saida", "grupo": "despesa_fixa"},
    "Manutenção":                   {"tipo": "saida", "grupo": "despesa_fixa"},
    "Tarifas bancárias e juros":    {"tipo": "saida", "grupo": "financeiro"},
    "Empréstimos (parcelas)":       {"tipo": "saida", "grupo": "financeiro"},
    "Retirada dos sócios":          {"tipo": "saida", "grupo": "socios"},
    "Investimentos e equipamentos": {"tipo": "saida", "grupo": "investimento"},
    "Outras despesas":              {"tipo": "saida", "grupo": "despesa_fixa"},
    # neutro
    "Transferência entre contas":   {"tipo": None, "grupo": "nao_operacional"},
}

PADRAO_SEM_CATEGORIA = {"entrada": "Outras receitas", "saida": "Outras despesas"}

# (palavras-chave, categoria, tipo exigido ou None). Primeira que casar vence,
# então as mais específicas vêm antes. Palavras já sem acento e minúsculas.
REGRAS_PADRAO: list[tuple[tuple[str, ...], str, str | None]] = [
    (("transferencia entre contas", "transf mesma titularidade", "aplicacao", "resgate"),
     "Transferência entre contas", None),
    (("pro labore", "pro-labore", "prolabore", "retirada socio", "retirada dos socios",
      "distribuicao de lucros"), "Retirada dos sócios", "saida"),
    (("taxa maquininha", "mdr", "antecipacao", "comissao", "taxa ifood", "taxa cartao",
      "stone", "cielo", "getnet", "pagseguro"), "Taxas de cartão e comissões", "saida"),
    (("das simples", "das mei", "guia das", "simples nacional", "icms", "iss", "pis", "cofins", "irpj", "csll", "darf",
      "gnre"), "Impostos sobre vendas", "saida"),
    (("salario", "folha", "ferias", "13o", "decimo terceiro", "fgts", "inss", "vale transporte",
      "vale refeicao", "rescisao", "adiantamento salarial", "beneficios"),
     "Pessoal e encargos", "saida"),
    (("aluguel", "condominio", "iptu"), "Aluguel e ocupação", "saida"),
    (("energia", "cemig", "enel", "light", "copel", "celesc", "coelba", "cpfl", "equatorial",
      "agua", "sabesp", "copasa", "cedae", "sanepar", "embasa", "internet", "vivo", "claro",
      "tim", "telefone", "telecom", "gas"), "Energia, água e telecom", "saida"),
    (("contador", "contabilidade", "advogado", "honorarios", "consultoria"),
     "Serviços profissionais", "saida"),
    (("software", "sistema", "assinatura", "licenca", "google workspace", "microsoft", "aws"),
     "Sistemas e software", "saida"),
    (("google ads", "meta ads", "facebook", "instagram", "marketing", "publicidade",
      "panfleto", "influenciador"), "Marketing", "saida"),
    (("manutencao", "conserto", "reparo", "assistencia tecnica"), "Manutenção", "saida"),
    (("frete", "correios", "transportadora", "motoboy", "entrega", "combustivel", "posto"),
     "Frete e logística", "saida"),
    (("tarifa", "iof", "juros", "anuidade", "pacote de servicos", "multa"),
     "Tarifas bancárias e juros", "saida"),
    (("parcela emprestimo", "emprestimo", "financiamento", "pronampe", "consorcio"),
     "Empréstimos (parcelas)", "saida"),
    (("fornecedor", "fornec", "insumo", "materia prima", "mercadoria", "pecas", "embalagem",
      "embalagens", "estoque", "atacado", "compra"), "Fornecedores e insumos", "saida"),
    (("equipamento", "maquina", "reforma", "computador", "mobiliario"),
     "Investimentos e equipamentos", "saida"),
    (("aporte", "emprestimo recebido", "capital"), "Aportes e empréstimos recebidos", "entrada"),
    (("venda", "vendas", "cliente", "recebimento", "consulta", "servico", "pix recebido",
      "ifood", "boleto", "encomenda", "cartao", "debito", "credito", "stone", "cielo",
      "getnet", "pagseguro", "sumup", "mercado pago"),
     "Vendas e serviços", "entrada"),
    (("rendimento", "estorno", "reembolso", "cashback"), "Outras receitas", "entrada"),
]

# Nomes que os donos costumam dar às próprias categorias → plano padrão.
# (palavras, categoria se for entrada, categoria se for saída)
SINONIMOS_CATEGORIA: list[tuple[tuple[str, ...], str | None, str | None]] = [
    (("vendas", "venda", "receita", "receitas", "faturamento", "servicos prestados"),
     "Vendas e serviços", None),
    (("servicos", "servico", "terceiros", "servicos de terceiros", "contabilidade", "juridico"),
     "Vendas e serviços", "Serviços profissionais"),
    (("insumos", "insumo", "materia prima", "cmv", "custo das mercadorias", "fornecedores",
      "mercadorias", "estoque", "compras", "pecas", "materiais"), None, "Fornecedores e insumos"),
    (("utilidades", "contas de consumo", "consumo", "energia", "agua", "telefone", "internet",
      "telecom"), None, "Energia, água e telecom"),
    (("ocupacao", "aluguel", "imovel", "instalacoes", "estrutura"), None, "Aluguel e ocupação"),
    (("pessoal", "folha", "salarios", "rh", "funcionarios", "colaboradores", "encargos"),
     None, "Pessoal e encargos"),
    (("impostos", "tributos", "taxas e impostos", "fiscal"), None, "Impostos sobre vendas"),
    (("manutencao", "reparos"), None, "Manutenção"),
    (("financeiro", "despesas financeiras", "tarifas", "bancarias", "juros"),
     "Outras receitas", "Tarifas bancárias e juros"),
    (("marketing", "publicidade", "propaganda"), None, "Marketing"),
    (("sistemas", "software", "tecnologia", "ti"), None, "Sistemas e software"),
    (("logistica", "frete", "transporte"), None, "Frete e logística"),
    (("socios", "pro labore", "retiradas"), None, "Retirada dos sócios"),
    (("investimentos", "equipamentos", "imobilizado"), None, "Investimentos e equipamentos"),
    (("emprestimos", "financiamentos"), "Aportes e empréstimos recebidos", "Empréstimos (parcelas)"),
]
