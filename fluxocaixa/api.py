"""API web do protótipo.

  uvicorn fluxocaixa.api:app --reload        → http://localhost:8000  (tela)
                                              → http://localhost:8000/docs (API)

Fluxo:
  1. POST /empresas                              cria a empresa
  2. POST /empresas/{id}/uploads                 envia a planilha → prévia do mapeamento
  3. POST /empresas/{id}/uploads/{u}/confirmar   confirma/corrige colunas → classifica e grava
  4. GET  /empresas/{id}/lancamentos?confianca=baixa   revisar categorias
     PATCH /empresas/{id}/lancamentos/{l}        corrige (e aprende a regra)
  5. GET  /empresas/{id}/analise | /painel       métricas, diagnóstico e painel

Sem autenticação neste protótipo: em produção, cada rota checa se o usuário
logado pertence à empresa (Supabase Auth + row level security).
"""
from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .armazem import Armazem
from .categorias import CATEGORIAS
from .classificador import chave_descricao, classificar, compativel
from .diagnostico import diagnosticar
from .leitor import ler_arquivo
from .mapeador import Mapeamento, mapear, tem_credencial_ia, validar
from .metricas import calcular
from .normalizador import COLUNAS_CANONICAS, normalizar, sem_acento
from .painel_html import gerar_html

EXTENSOES = {".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".ofx"}
TAMANHO_MAXIMO = 10 * 1024 * 1024
LINHAS_PREVIA = 8
CAMPOS_MAPEAMENTO = ["coluna_data", "coluna_descricao", "coluna_valor", "coluna_entrada", "coluna_saida",
                     "coluna_tipo", "coluna_categoria", "coluna_vencimento", "coluna_status"]
ESTATICOS = Path(__file__).parent / "estatico"


# ------------------------------------------------------------------ modelos
class NovaEmpresa(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    setor: Optional[str] = Field(default=None, max_length=80)
    saldo_inicial: Optional[float] = Field(default=None, description="saldo bancário no início dos dados")


class AlteraEmpresa(BaseModel):
    nome: Optional[str] = Field(default=None, min_length=1, max_length=120)
    setor: Optional[str] = Field(default=None, max_length=80)
    saldo_inicial: Optional[float] = None


class ConfirmaTabela(BaseModel):
    indice: int
    usar: bool = True
    mapeamento: dict[str, Optional[str]] = Field(
        default_factory=dict, description="só os campos que o usuário mudou, ex. {'coluna_tipo': 'E/S'}")


class Confirmacao(BaseModel):
    tabelas: list[ConfirmaTabela] = Field(default_factory=list)


class Correcao(BaseModel):
    categoria: str
    aplicar_em: Literal["este", "iguais", "palavra"] = "iguais"
    palavra_chave: Optional[str] = Field(default=None, max_length=60)


# ---------------------------------------------------------------- utilidades
def assinatura(colunas) -> str:
    """Identifica o "formato" da planilha para lembrar o mapeamento confirmado."""
    return "|".join(sem_acento(str(c)) for c in colunas)


def _celula(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%d/%m/%Y")
    return str(v)[:60]


def create_app(caminho_db: str | Path = "dados/fluxocaixa.db",
               pasta_uploads: str | Path = "dados/uploads",
               usar_ia: bool | None = None) -> FastAPI:
    app = FastAPI(title="Fluxo de Caixa PME", version="0.1.0",
                  description="Protótipo: planilha → mapeamento → classificação → diagnóstico de caixa.")
    db = Armazem(caminho_db)
    pasta = Path(pasta_uploads)
    ia = tem_credencial_ia() if usar_ia is None else usar_ia
    app.state.db = db

    def empresa_ou_404(empresa_id: int) -> dict:
        e = db.empresa(empresa_id)
        if not e:
            raise HTTPException(404, "Empresa não encontrada")
        return e

    def upload_ou_404(empresa_id: int, upload_id: int) -> dict:
        u = db.upload(upload_id)
        if not u or u["empresa_id"] != empresa_id:
            raise HTTPException(404, "Envio não encontrado")
        return u

    # ------------------------------------------------------------- tela
    @app.get("/", include_in_schema=False)
    def tela():
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/categorias")
    def categorias():
        return [{"nome": n, "tipo": c["tipo"], "grupo": c["grupo"]} for n, c in CATEGORIAS.items()]

    # ---------------------------------------------------------- empresas
    @app.post("/empresas", status_code=201)
    def criar_empresa(e: NovaEmpresa):
        return db.criar_empresa(e.nome.strip(), e.setor, e.saldo_inicial)

    @app.get("/empresas")
    def listar_empresas():
        return db.listar_empresas()

    @app.get("/empresas/{empresa_id}")
    def ver_empresa(empresa_id: int):
        e = empresa_ou_404(empresa_id)
        return {**e, "uploads": db.listar_uploads(empresa_id),
                "categorias": db.resumo_categorias(empresa_id)}

    @app.patch("/empresas/{empresa_id}")
    def alterar_empresa(empresa_id: int, e: AlteraEmpresa):
        empresa_ou_404(empresa_id)
        return db.atualizar_empresa(empresa_id, **e.model_dump(exclude_unset=True))

    # ----------------------------------------------------------- uploads
    @app.post("/empresas/{empresa_id}/uploads", status_code=201)
    async def enviar(empresa_id: int, arquivo: UploadFile = File(...)):
        empresa_ou_404(empresa_id)
        nome = Path(arquivo.filename or "arquivo").name
        ext = Path(nome).suffix.lower()
        if ext not in EXTENSOES:
            raise HTTPException(415, f"Formato {ext or '(sem extensão)'} não suportado. "
                                     "Envie .xlsx, .xls, .csv ou .ofx.")
        conteudo = await arquivo.read(TAMANHO_MAXIMO + 1)
        if len(conteudo) > TAMANHO_MAXIMO:
            raise HTTPException(413, "Arquivo maior que 10 MB.")
        destino = pasta / str(empresa_id) / f"{uuid.uuid4().hex}{ext}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)

        try:
            lido = ler_arquivo(destino)
        except Exception as e:
            destino.unlink(missing_ok=True)
            raise HTTPException(422, f"Não foi possível ler o arquivo: {type(e).__name__}")

        previa: dict = {"formato": ext.lstrip("."), "tabelas": []}
        if lido.lancamentos_ofx is not None:
            previa["formato"] = "ofx"
            previa["lancamentos_ofx"] = len(lido.lancamentos_ofx)
        for i, t in enumerate(lido.tabelas):
            ass = assinatura(t.dados.columns)
            salvo = db.mapeamento_salvo(empresa_id, ass)
            if salvo:
                mp = Mapeamento(**{**salvo, "metodo": "memorizado"})
            else:
                mp = mapear(t.dados, usar_ia=ia)
            problemas = validar(mp, t.dados)
            previa["tabelas"].append({
                "indice": i, "origem": t.origem.replace(destino.name, nome), "colunas": [str(c) for c in t.dados.columns],
                "total_linhas": len(t.dados),
                "amostra": [[_celula(v) for v in linha] for linha in t.dados.head(LINHAS_PREVIA).values.tolist()],
                "mapeamento": mp.model_dump(), "problemas": problemas,
                "sugerida": bool(mp.e_planilha_de_lancamentos and not problemas),
            })
        if not previa["tabelas"] and lido.lancamentos_ofx is None:
            destino.unlink(missing_ok=True)
            raise HTTPException(422, "Nenhuma tabela com cabeçalho encontrada no arquivo.")
        upload_id = db.criar_upload(empresa_id, nome, str(destino), "aguardando_confirmacao", previa)
        return {"id": upload_id, "nome_arquivo": nome, "status": "aguardando_confirmacao", **previa}

    @app.get("/empresas/{empresa_id}/uploads/{upload_id}")
    def ver_upload(empresa_id: int, upload_id: int):
        u = upload_ou_404(empresa_id, upload_id)
        u.pop("caminho", None)
        return u

    @app.post("/empresas/{empresa_id}/uploads/{upload_id}/confirmar")
    def confirmar(empresa_id: int, upload_id: int, c: Confirmacao):
        upload_ou_404(empresa_id, upload_id)
        u = db.upload(upload_id)
        if u["status"] != "aguardando_confirmacao":
            raise HTTPException(409, "Este envio já foi processado.")
        lido = ler_arquivo(u["caminho"])
        for t in lido.tabelas:  # mostra o nome que o usuário enviou, não o salvo
            t.origem = t.origem.replace(Path(u["caminho"]).name, u["nome_arquivo"])
        partes, relatorios, erros = [], [], []

        if lido.lancamentos_ofx is not None:
            partes.append(lido.lancamentos_ofx.assign(origem=u["nome_arquivo"])[COLUNAS_CANONICAS])

        decisoes = {t.indice: t for t in c.tabelas}
        for previa_t in u["previa"]["tabelas"]:
            i = previa_t["indice"]
            d = decisoes.get(i)
            usar = d.usar if d else previa_t["sugerida"]
            if not usar:
                continue
            tabela = lido.tabelas[i]
            alteracoes = {k: v for k, v in (d.mapeamento if d else {}).items() if k in CAMPOS_MAPEAMENTO}
            mp = Mapeamento(**{**previa_t["mapeamento"], **alteracoes,
                               "e_planilha_de_lancamentos": True})
            problemas = validar(mp, tabela.dados)
            if problemas:
                erros.append({"indice": i, "origem": tabela.origem, "problemas": problemas})
                continue
            df, rel = normalizar(tabela.dados, mp, tabela.origem)
            relatorios.append({"indice": i, "origem": tabela.origem, **rel})
            partes.append(df)
            db.salvar_mapeamento(empresa_id, assinatura(tabela.dados.columns),
                                 {**mp.model_dump(), "confianca": "alta",
                                  "observacoes": "Confirmado pelo usuário."})
        if erros:
            raise HTTPException(422, {"mensagem": "Corrija o mapeamento destas tabelas.", "tabelas": erros})
        df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS_CANONICAS)
        if df.empty:
            raise HTTPException(422, "Nenhum lançamento válido nas tabelas escolhidas.")

        regras = db.regras(empresa_id)
        empresa = db.empresa(empresa_id)
        df, rel_cls = classificar(df, regras, usar_ia=ia, setor=empresa["setor"])
        db.salvar_regras(empresa_id, regras)
        gravados = db.inserir_lancamentos(empresa_id, upload_id, df)
        resultado = {"lancamentos_lidos": len(df), **gravados,
                     "classificacao": rel_cls["por_fonte"],
                     "para_revisar": int((df["confianca"] == "baixa").sum()),
                     "tabelas": relatorios}
        if rel_cls.get("erro_ia"):
            resultado["aviso"] = "A IA de classificação falhou; categorias pendentes ficaram como 'Outras'."
        db.concluir_upload(upload_id, resultado)
        return resultado

    @app.delete("/empresas/{empresa_id}/uploads/{upload_id}")
    def excluir_upload(empresa_id: int, upload_id: int):
        u = upload_ou_404(empresa_id, upload_id)
        n = db.excluir_upload(upload_id)
        Path(u["caminho"]).unlink(missing_ok=True)
        return {"excluido": upload_id, "lancamentos_removidos": n}

    # ------------------------------------------------------- lançamentos
    @app.get("/empresas/{empresa_id}/lancamentos")
    def listar_lancamentos(empresa_id: int, categoria: Optional[str] = None,
                           confianca: Optional[Literal["alta", "media", "baixa"]] = None,
                           busca: Optional[str] = None,
                           limite: int = Query(100, ge=1, le=500), deslocamento: int = Query(0, ge=0)):
        empresa_ou_404(empresa_id)
        itens, total = db.lancamentos(empresa_id, categoria, confianca, busca, limite, deslocamento)
        return {"total": total, "itens": itens}

    @app.patch("/empresas/{empresa_id}/lancamentos/{lancamento_id}")
    def corrigir(empresa_id: int, lancamento_id: int, c: Correcao):
        empresa_ou_404(empresa_id)
        l = db.lancamento(empresa_id, lancamento_id)
        if not l:
            raise HTTPException(404, "Lançamento não encontrado")
        if c.categoria not in CATEGORIAS:
            raise HTTPException(422, f"Categoria desconhecida: {c.categoria}")
        if not compativel(c.categoria, l["tipo"]):
            raise HTTPException(422, f"'{c.categoria}' não serve para uma {l['tipo']}.")
        grupo = CATEGORIAS[c.categoria]["grupo"]
        regras = db.regras(empresa_id)
        regra = None
        if c.aplicar_em == "este":
            ids = [lancamento_id]
        elif c.aplicar_em == "iguais":
            regras.corrigir(l["descricao"], c.categoria)
            ids = db.ids_por_chave(empresa_id, l["tipo"], chave=l["chave"])
            regra = {"tipo": "descricao_igual", "chave": l["chave"]}
        else:
            palavra = chave_descricao(c.palavra_chave or "")
            if not palavra:
                raise HTTPException(422, "Informe a palavra-chave.")
            if palavra not in l["chave"]:
                raise HTTPException(422, f"'{c.palavra_chave}' não aparece na descrição deste lançamento.")
            regras.corrigir("", c.categoria, palavra_chave=palavra)
            ids = db.ids_por_chave(empresa_id, l["tipo"], contem=palavra)
            regra = {"tipo": "contem_palavra", "chave": palavra}
        db.salvar_regras(empresa_id, regras)
        n = db.recategorizar(empresa_id, ids, c.categoria, grupo)
        return {"atualizados": n, "categoria": c.categoria, "regra_criada": regra}

    # ----------------------------------------------------------- análise
    def _analise(empresa_id: int, hoje: Optional[date]):
        e = empresa_ou_404(empresa_id)
        df = db.dataframe(empresa_id)
        if df.empty:
            raise HTTPException(409, "Envie uma planilha antes de pedir a análise.")
        m = calcular(df, hoje=hoje, saldo_inicial=e["saldo_inicial"])
        d = diagnosticar(m, usar_ia=ia, setor=e["setor"])
        return e, m, d

    @app.get("/empresas/{empresa_id}/analise")
    def analise(empresa_id: int, hoje: Optional[date] = None):
        _, m, d = _analise(empresa_id, hoje)
        m = {**m, "projecao": {k: v for k, v in m["projecao"].items() if k != "serie_diaria"}}
        return {"metricas": m, "diagnostico": d.model_dump()}

    @app.get("/empresas/{empresa_id}/painel", response_class=HTMLResponse)
    def painel(empresa_id: int, hoje: Optional[date] = None):
        e, m, d = _analise(empresa_id, hoje)
        return gerar_html({"metricas": m, "diagnostico": d.model_dump()}, e["nome"], e["setor"])

    return app


app = create_app(os.environ.get("FLUXO_DB", "dados/fluxocaixa.db"),
                 os.environ.get("FLUXO_UPLOADS", "dados/uploads"))
