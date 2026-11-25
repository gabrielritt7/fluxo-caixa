# Fluxo de Caixa — protótipo do leitor de planilhas

Lê planilhas financeiras de PMEs (xlsx, csv, ofx), descobre o que é cada coluna
(com IA ou heurística) e devolve lançamentos padronizados:

| data | descricao | valor | tipo | categoria_original | vencimento | status | origem |
|------|-----------|-------|------|--------------------|------------|--------|--------|

## Etapas

1. **Leitura** (`leitor.py`) — xlsx/csv/ofx, detecta cabeçalho, uma tabela por aba.
2. **Mapeamento de colunas** (`mapeador.py`) — IA com saída estruturada, ou heurística.
3. **Normalização** (`normalizador.py`) — valores, datas, tipo e status. Só código.
4. **Classificação** (`classificador.py`, `categorias.py`) — regras da empresa → regras
   padrão do Brasil → categoria da planilha → IA em lotes (só descrições distintas).
   O que a IA e o usuário classificam vira regra: o mês seguinte custa menos.
5. **Métricas e projeção** (`metricas.py`) — faturamento, margem, ponto de equilíbrio,
   contas vencidas, despesas em alta, saldo projetado em 30/60/90 dias, alertas. Só código.
6. **Diagnóstico** (`diagnostico.py`) — a IA escreve resumo e 3 ações a partir das métricas;
   todo "R$" do texto é conferido contra as métricas. Sem IA, usa texto-modelo.

## Rodar

```bash
pip install -r requirements.txt
python gerar_exemplos.py                 # cria planilhas bagunçadas em exemplos/
python processar.py exemplos/*.xlsx exemplos/*.csv exemplos/*.ofx   # só etapas 1-3
python analisar.py exemplos/clinica_colunas_separadas.xlsx --saldo 15000 --empresa clinica
python -m pytest -q
```

Com `ANTHROPIC_API_KEY` definida, o mapeamento de colunas usa o Claude
(modelo em `FLUXO_MODELO`, padrão `claude-opus-5-5`). Sem chave, ou se a IA
falhar ou inventar uma coluna, cai para a heurística por sinônimos.
`--sem-ia` força a heurística.

## API web e tela

```bash
uvicorn fluxocaixa.api:app --reload
```

- http://localhost:8000 — tela: empresa → enviar planilha → conferir colunas → revisar categorias → painel
- http://localhost:8000/docs — documentação interativa da API

| Rota | O que faz |
|------|-----------|
| `POST /empresas` | cria a empresa (nome, setor, saldo inicial) |
| `POST /empresas/{id}/uploads` | envia a planilha e devolve a prévia do mapeamento de cada aba |
| `POST /empresas/{id}/uploads/{u}/confirmar` | confirma/corrige as colunas, classifica e grava (sem duplicar) |
| `GET /empresas/{id}/lancamentos?confianca=baixa` | lista para revisão (filtros: categoria, busca) |
| `PATCH /empresas/{id}/lancamentos/{l}` | corrige a categoria: só este, todos iguais ou por palavra-chave (vira regra) |
| `GET /empresas/{id}/analise` · `/painel` | métricas + diagnóstico · painel HTML |
| `DELETE /empresas/{id}/uploads/{u}` | apaga o envio e os lançamentos dele |

Comportamentos importantes:
- O mapeamento confirmado é lembrado pelo "formato" da planilha (nomes das colunas).
- Reenviar a mesma planilha não duplica: lançamentos iguais só têm status/vencimento atualizados (pendente → pago).
- Dados em SQLite (`dados/fluxocaixa.db`, ou `FLUXO_DB`); as tabelas mapeiam direto para Postgres.
- **Sem login neste protótipo.** Em produção: Supabase Auth + checagem de empresa em toda rota.

## Estrutura

- `fluxocaixa/leitor.py` — abre o arquivo, detecta a linha de cabeçalho, uma tabela por aba.
- `fluxocaixa/mapeador.py` — mapeamento de colunas (IA com saída estruturada + heurística) e validação.
- `fluxocaixa/normalizador.py` — converte valores (R$ 1.234,56), datas (inclusive serial do Excel), tipo e status. Só código, nenhuma conta feita pela IA.
- `fluxocaixa/api.py` e `fluxocaixa/armazem.py` — API (FastAPI) e armazenamento.
- `fluxocaixa/estatico/index.html` — tela do protótipo.
- `fluxocaixa/pipeline.py` — orquestra; tem o gancho `confirmar` para a tela "Entendemos assim, está certo?".

## Regras de segurança dos dados

- Só cabeçalhos + 15 linhas de amostra (células truncadas em 50 caracteres) vão para a IA.
- Se não dá para distinguir entrada de saída, a tabela é recusada em vez de adivinhada.
- Na classificação, CPF/CNPJ e números longos são mascarados antes de ir para a IA.
- O diagnóstico recebe só métricas agregadas, nunca lançamentos individuais.
