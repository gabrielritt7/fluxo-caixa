"""Armazenamento (SQLite no protótipo; as tabelas mapeiam 1:1 para Postgres/Supabase).

Tudo é isolado por empresa: nenhuma consulta de lançamento roda sem empresa_id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .classificador import RegrasEmpresa, Regra, chave_descricao

ESQUEMA = """
CREATE TABLE IF NOT EXISTS empresas (
  id INTEGER PRIMARY KEY, nome TEXT NOT NULL, setor TEXT, saldo_inicial REAL,
  regras_json TEXT NOT NULL DEFAULT '{}', criada_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY, empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  nome_arquivo TEXT NOT NULL, caminho TEXT NOT NULL, status TEXT NOT NULL,
  previa_json TEXT, resultado_json TEXT, criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mapeamentos (
  empresa_id INTEGER NOT NULL, assinatura TEXT NOT NULL, mapeamento_json TEXT NOT NULL,
  PRIMARY KEY (empresa_id, assinatura)
);
CREATE TABLE IF NOT EXISTS lancamentos (
  id INTEGER PRIMARY KEY, empresa_id INTEGER NOT NULL, upload_id INTEGER NOT NULL,
  data TEXT NOT NULL, descricao TEXT NOT NULL, valor REAL NOT NULL, tipo TEXT NOT NULL,
  categoria_original TEXT, vencimento TEXT, status TEXT NOT NULL, origem TEXT,
  categoria TEXT NOT NULL, grupo TEXT NOT NULL, fonte_categoria TEXT NOT NULL,
  confianca TEXT NOT NULL, chave TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lanc_empresa ON lancamentos(empresa_id, data);
CREATE INDEX IF NOT EXISTS ix_lanc_chave ON lancamentos(empresa_id, chave);
"""

COLUNAS_LANC = ["data", "descricao", "valor", "tipo", "categoria_original", "vencimento",
                "status", "origem", "categoria", "grupo", "fonte_categoria", "confianca"]


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


class Armazem:
    def __init__(self, caminho: str | Path):
        self.caminho = str(caminho)
        if self.caminho != ":memory:":
            Path(self.caminho).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.caminho, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._trava = threading.Lock()
        with self._trava:
            self._con.executescript(ESQUEMA)

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._trava:
            cur = self._con.execute(sql, args)
            self._con.commit()
            return cur.fetchall()

    def _ins(self, sql: str, args=()) -> int:
        with self._trava:
            cur = self._con.execute(sql, args)
            self._con.commit()
            return cur.lastrowid

    # ------------------------------------------------------------- empresas
    def criar_empresa(self, nome: str, setor: str | None, saldo_inicial: float | None) -> dict:
        i = self._ins("INSERT INTO empresas (nome, setor, saldo_inicial, criada_em) VALUES (?,?,?,?)",
                      (nome, setor, saldo_inicial, _agora()))
        return self.empresa(i)

    def empresa(self, empresa_id: int) -> dict | None:
        r = self._q("SELECT id, nome, setor, saldo_inicial, criada_em FROM empresas WHERE id=?", (empresa_id,))
        return dict(r[0]) if r else None

    def listar_empresas(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, nome, setor, saldo_inicial, criada_em FROM empresas ORDER BY id")]

    def atualizar_empresa(self, empresa_id: int, **campos) -> dict:
        permitidos = {k: v for k, v in campos.items() if k in ("nome", "setor", "saldo_inicial")}
        if permitidos:
            sets = ", ".join(f"{k}=?" for k in permitidos)
            self._q(f"UPDATE empresas SET {sets} WHERE id=?", (*permitidos.values(), empresa_id))
        return self.empresa(empresa_id)

    def regras(self, empresa_id: int) -> RegrasEmpresa:
        r = self._q("SELECT regras_json FROM empresas WHERE id=?", (empresa_id,))
        d = json.loads(r[0]["regras_json"]) if r else {}
        return RegrasEmpresa([Regra(**x) for x in d.get("regras", [])], d.get("categorias_planilha", {}))

    def salvar_regras(self, empresa_id: int, regras: RegrasEmpresa) -> None:
        from dataclasses import asdict
        d = {"regras": [asdict(r) for r in regras.regras], "categorias_planilha": regras.categorias_planilha}
        self._q("UPDATE empresas SET regras_json=? WHERE id=?", (json.dumps(d, ensure_ascii=False), empresa_id))

    # ---------------------------------------------------------------- uploads
    def criar_upload(self, empresa_id: int, nome: str, caminho: str, status: str, previa: dict) -> int:
        return self._ins("INSERT INTO uploads (empresa_id, nome_arquivo, caminho, status, previa_json, criado_em)"
                         " VALUES (?,?,?,?,?,?)",
                         (empresa_id, nome, caminho, status, json.dumps(previa, ensure_ascii=False, default=str), _agora()))

    def upload(self, upload_id: int) -> dict | None:
        r = self._q("SELECT * FROM uploads WHERE id=?", (upload_id,))
        if not r:
            return None
        d = dict(r[0])
        d["previa"] = json.loads(d.pop("previa_json") or "null")
        d["resultado"] = json.loads(d.pop("resultado_json") or "null")
        return d

    def listar_uploads(self, empresa_id: int) -> list[dict]:
        rows = self._q("SELECT id, nome_arquivo, status, criado_em, resultado_json FROM uploads "
                       "WHERE empresa_id=? ORDER BY id DESC", (empresa_id,))
        return [{**{k: r[k] for k in ("id", "nome_arquivo", "status", "criado_em")},
                 "resultado": json.loads(r["resultado_json"] or "null")} for r in rows]

    def concluir_upload(self, upload_id: int, resultado: dict) -> None:
        self._q("UPDATE uploads SET status='processado', resultado_json=? WHERE id=?",
                (json.dumps(resultado, ensure_ascii=False, default=str), upload_id))

    def excluir_upload(self, upload_id: int) -> int:
        n = self._q("SELECT COUNT(*) n FROM lancamentos WHERE upload_id=?", (upload_id,))[0]["n"]
        self._q("DELETE FROM lancamentos WHERE upload_id=?", (upload_id,))
        self._q("DELETE FROM uploads WHERE id=?", (upload_id,))
        return n

    # ----------------------------------------------------- mapeamentos salvos
    def mapeamento_salvo(self, empresa_id: int, assinatura: str) -> dict | None:
        r = self._q("SELECT mapeamento_json FROM mapeamentos WHERE empresa_id=? AND assinatura=?",
                    (empresa_id, assinatura))
        return json.loads(r[0]["mapeamento_json"]) if r else None

    def salvar_mapeamento(self, empresa_id: int, assinatura: str, mapeamento: dict) -> None:
        self._q("INSERT OR REPLACE INTO mapeamentos VALUES (?,?,?)",
                (empresa_id, assinatura, json.dumps(mapeamento, ensure_ascii=False)))

    # ------------------------------------------------------------- lançamentos
    def inserir_lancamentos(self, empresa_id: int, upload_id: int, df: pd.DataFrame) -> dict:
        """Insere sem duplicar: a mesma planilha (ou uma versão atualizada dela)
        pode ser enviada de novo. Um lançamento igual (data, valor, tipo e
        descrição) que já existe só tem o status/vencimento atualizado — assim
        "pendente" vira "pago" quando o dono atualiza a planilha. Conta-se por
        multiplicidade: duas vendas iguais no mesmo dia continuam sendo duas."""
        existentes: dict[tuple, list[int]] = {}
        for r in self._q("SELECT id, data, valor, tipo, chave FROM lancamentos WHERE empresa_id=? ORDER BY id",
                         (empresa_id,)):
            existentes.setdefault((r["data"], round(r["valor"], 2), r["tipo"], r["chave"]), []).append(r["id"])
        novos, atualizados, linhas = 0, 0, []
        for _, l in df.iterrows():
            chave = chave_descricao(l["descricao"])
            k = (_iso(l["data"]), round(float(l["valor"]), 2), l["tipo"], chave)
            if existentes.get(k):
                lid = existentes[k].pop(0)
                self._q("UPDATE lancamentos SET status=?, vencimento=? WHERE id=?",
                        (l["status"], _iso(l["vencimento"]), lid))
                atualizados += 1
                continue
            linhas.append((empresa_id, upload_id, _iso(l["data"]), str(l["descricao"]), float(l["valor"]),
                           l["tipo"], l["categoria_original"], _iso(l["vencimento"]), l["status"], l["origem"],
                           l["categoria"], l["grupo"], l["fonte_categoria"], l["confianca"], chave))
            novos += 1
        with self._trava:
            self._con.executemany(
                "INSERT INTO lancamentos (empresa_id, upload_id, data, descricao, valor, tipo, categoria_original,"
                " vencimento, status, origem, categoria, grupo, fonte_categoria, confianca, chave)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", linhas)
            self._con.commit()
        return {"novos": novos, "ja_existiam": atualizados}

    def lancamentos(self, empresa_id: int, categoria: str | None = None, confianca: str | None = None,
                    busca: str | None = None, limite: int = 100, deslocamento: int = 0) -> tuple[list[dict], int]:
        filtros, args = ["empresa_id=?"], [empresa_id]
        if categoria:
            filtros.append("categoria=?"); args.append(categoria)
        if confianca:
            filtros.append("confianca=?"); args.append(confianca)
        if busca:
            filtros.append("chave LIKE ?"); args.append(f"%{chave_descricao(busca)}%")
        onde = " AND ".join(filtros)
        total = self._q(f"SELECT COUNT(*) n FROM lancamentos WHERE {onde}", args)[0]["n"]
        rows = self._q(f"SELECT id, {', '.join(COLUNAS_LANC)} FROM lancamentos WHERE {onde} "
                       "ORDER BY data DESC, id DESC LIMIT ? OFFSET ?", (*args, limite, deslocamento))
        return [dict(r) for r in rows], total

    def lancamento(self, empresa_id: int, lancamento_id: int) -> dict | None:
        r = self._q(f"SELECT id, chave, {', '.join(COLUNAS_LANC)} FROM lancamentos WHERE empresa_id=? AND id=?",
                    (empresa_id, lancamento_id))
        return dict(r[0]) if r else None

    def recategorizar(self, empresa_id: int, ids: list[int], categoria: str, grupo: str) -> int:
        if not ids:
            return 0
        marcas = ",".join("?" * len(ids))
        self._q(f"UPDATE lancamentos SET categoria=?, grupo=?, fonte_categoria='regra_usuario', confianca='alta' "
                f"WHERE empresa_id=? AND id IN ({marcas})", (categoria, grupo, empresa_id, *ids))
        return len(ids)

    def ids_por_chave(self, empresa_id: int, tipo: str, chave: str | None = None,
                      contem: str | None = None) -> list[int]:
        if contem is not None:
            rows = self._q("SELECT id, chave FROM lancamentos WHERE empresa_id=? AND tipo=?", (empresa_id, tipo))
            from .classificador import _contem
            return [r["id"] for r in rows if _contem(r["chave"], contem)]
        return [r["id"] for r in self._q("SELECT id FROM lancamentos WHERE empresa_id=? AND tipo=? AND chave=?",
                                         (empresa_id, tipo, chave))]

    def dataframe(self, empresa_id: int) -> pd.DataFrame:
        rows = self._q(f"SELECT {', '.join(COLUNAS_LANC)} FROM lancamentos WHERE empresa_id=?", (empresa_id,))
        df = pd.DataFrame([dict(r) for r in rows], columns=COLUNAS_LANC)
        for c in ("data", "vencimento"):
            df[c] = pd.to_datetime(df[c])
        return df

    def resumo_categorias(self, empresa_id: int) -> list[dict]:
        rows = self._q("SELECT categoria, tipo, COUNT(*) n, SUM(valor) total, "
                       "SUM(CASE WHEN confianca='baixa' THEN 1 ELSE 0 END) baixa "
                       "FROM lancamentos WHERE empresa_id=? GROUP BY categoria, tipo ORDER BY total DESC",
                       (empresa_id,))
        return [dict(r) for r in rows]
