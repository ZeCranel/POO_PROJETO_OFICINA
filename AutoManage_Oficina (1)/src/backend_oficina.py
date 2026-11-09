import hashlib
import hmac
import re
import secrets
import sqlite3
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "oficina.db"
SCHEMA_PATH = BASE_DIR / "schema_oficina.sql"


class StatusOS(str, Enum):
    ABERTA = "ABERTA"
    EM_ANALISE = "EM_ANALISE"
    AGUARDANDO_PECAS = "AGUARDANDO_PECAS"
    EM_EXECUCAO = "EM_EXECUCAO"
    PRONTO = "PRONTO"
    FINALIZADA = "FINALIZADA"


class Pessoa(ABC):

    def __init__(self, nome: str, cpf: str, telefone: str):
        self._nome = nome
        self._cpf = cpf
        self._telefone = telefone

    @abstractmethod
    def exibir_detalhes(self) -> dict:
        ...


class Cliente(Pessoa):
    def __init__(self, nome, cpf, telefone, endereco=None, id_cliente=None):
        super().__init__(nome, cpf, telefone)
        self._endereco = endereco
        self._id = id_cliente

    def exibir_detalhes(self) -> dict:
        return {
            "tipo": "Cliente", "idCliente": self._id, "nome": self._nome,
            "cpf": self._cpf, "telefone": self._telefone, "endereco": self._endereco,
        }


class Mecanico(Pessoa):
    def __init__(self, nome, cpf, telefone, especialidade, id_mecanico=None):
        super().__init__(nome, cpf, telefone)
        self._especialidade = especialidade
        self._id = id_mecanico

    def exibir_detalhes(self) -> dict:
        return {
            "tipo": "Mecanico", "idMecanico": self._id, "nome": self._nome,
            "cpf": self._cpf, "telefone": self._telefone,
            "especialidade": self._especialidade,
        }


class ItemOS:
    """Item da OS (peça ou serviço). É composição de OrdemServico."""

    def __init__(self, descricao: str, quantidade: int, valor_unitario: float):
        self.descricao = descricao
        self.quantidade = quantidade
        self.valor_unitario = valor_unitario

    def calcular_subtotal(self) -> float:
        return round(self.quantidade * self.valor_unitario, 2)


class OrdemServico:
    TRANSICOES = {
        StatusOS.ABERTA: [StatusOS.EM_ANALISE],
        StatusOS.EM_ANALISE: [StatusOS.AGUARDANDO_PECAS, StatusOS.EM_EXECUCAO],
        StatusOS.AGUARDANDO_PECAS: [StatusOS.EM_EXECUCAO],
        StatusOS.EM_EXECUCAO: [StatusOS.AGUARDANDO_PECAS, StatusOS.PRONTO],
        StatusOS.PRONTO: [StatusOS.FINALIZADA],
        StatusOS.FINALIZADA: [],
    }

    def __init__(self, numero: int, status: StatusOS, itens: list):
        self.numero = numero
        self.status = status
        self.itens = itens

    def calcular_total(self) -> float:
        return round(sum(i.calcular_subtotal() for i in self.itens), 2)

    def pode_ir_para(self, novo: StatusOS) -> bool:
        return novo in self.TRANSICOES[self.status]


@contextmanager
def conexao():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar_banco():
    with conexao() as con:
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Usuário padrão de demonstração (criado só se a tabela estiver vazia)
        if con.execute("SELECT COUNT(*) FROM Usuario").fetchone()[0] == 0:
            con.execute("INSERT INTO Usuario (login, senhaHash, nome) VALUES (?,?,?)",
                        ("admin", gerar_hash_senha("admin123"), "Administrador"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    inicializar_banco()
    yield


app = FastAPI(title="API AutoManage - Oficina Mecânica", lifespan=lifespan)


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _buscar(con, sql, params, msg):
    row = con.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=msg)
    return row


def _cliente_ativo(con, id_cliente):
    return _buscar(con, "SELECT * FROM Cliente WHERE idCliente=? AND ativo=1",
                   (id_cliente,), "Cliente não encontrado.")


def _veiculo_ativo(con, placa):
    return _buscar(con, "SELECT * FROM Veiculo WHERE placa=? AND ativo=1",
                   (placa,), "Veículo não encontrado.")


def _mecanico_ativo(con, id_mecanico):
    return _buscar(con, "SELECT * FROM Mecanico WHERE idMecanico=? AND ativo=1",
                   (id_mecanico,), "Mecânico não encontrado.")


def _os_ou_404(con, numero_os):
    return _buscar(con, "SELECT * FROM OrdemServico WHERE numeroOS=?",
                   (numero_os,), "Ordem de Serviço não encontrada.")


def _orcamento_ou_404(con, numero):
    return _buscar(con, "SELECT * FROM Orcamento WHERE numeroOrcamento=?",
                   (numero,), "Orçamento não encontrado.")


def _itens_da_os(con, numero_os):
    return con.execute(
        """SELECT i.idItem, i.idPeca, i.idServico, i.quantidade, i.valorUnitario,
                  COALESCE(p.descricao, s.descricao) AS descricao
           FROM ItemOS i
           LEFT JOIN Peca p ON p.idPeca = i.idPeca
           LEFT JOIN Servico s ON s.idServico = i.idServico
           WHERE i.numeroOS = ?""", (numero_os,)).fetchall()


def _dominio_os(con, os_row) -> OrdemServico:
    itens = [ItemOS(r["descricao"], r["quantidade"], r["valorUnitario"])
             for r in _itens_da_os(con, os_row["numeroOS"])]
    return OrdemServico(os_row["numeroOS"], StatusOS(os_row["status"]), itens)


def _recalcular_total(con, numero_os):
    os_row = _os_ou_404(con, numero_os)
    total = _dominio_os(con, os_row).calcular_total()
    con.execute("UPDATE OrdemServico SET valorTotal=? WHERE numeroOS=?", (total, numero_os))


def _detalhe_os(con, numero_os) -> dict:
    os_row = _os_ou_404(con, numero_os)
    itens = []
    for r in _itens_da_os(con, numero_os):
        item = ItemOS(r["descricao"], r["quantidade"], r["valorUnitario"])
        itens.append({
            "idItem": r["idItem"],
            "tipo": "PECA" if r["idPeca"] is not None else "SERVICO",
            "descricao": r["descricao"],
            "quantidade": r["quantidade"],
            "valorUnitario": r["valorUnitario"],
            "subtotal": item.calcular_subtotal(),
        })
    mecanicos = [dict(m) for m in con.execute(
        """SELECT m.idMecanico, m.nome, m.especialidade
           FROM OrdemServico_Mecanico om JOIN Mecanico m ON m.idMecanico = om.idMecanico
           WHERE om.numeroOS = ?""", (numero_os,)).fetchall()]
    dados = dict(os_row)
    dados["valorTotal"] = _dominio_os(con, os_row).calcular_total()
    dados["mecanicos"] = mecanicos
    dados["itens"] = itens
    return dados


def _so_digitos_cpf(v: str) -> str:
    digitos = re.sub(r"\D", "", v)
    if len(digitos) != 11:
        raise ValueError("CPF deve ter 11 dígitos.")
    return digitos


class ClienteIn(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    telefone: str = Field(min_length=8, max_length=20)
    cpf: str
    endereco: Optional[str] = Field(default=None, max_length=255)

    @field_validator("cpf")
    @classmethod
    def valida_cpf(cls, v):
        return _so_digitos_cpf(v)


class MecanicoIn(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    telefone: str = Field(min_length=8, max_length=20)
    cpf: str
    especialidade: str = Field(min_length=2, max_length=50)

    @field_validator("cpf")
    @classmethod
    def valida_cpf(cls, v):
        return _so_digitos_cpf(v)


def _valida_ano(v: int) -> int:
    if not 1900 <= v <= date.today().year + 1:
        raise ValueError("Ano do veículo inválido.")
    return v


class VeiculoIn(BaseModel):
    placa: str
    modelo: str = Field(min_length=1, max_length=50)
    marca: str = Field(min_length=1, max_length=50)
    ano: int
    id_cliente: int

    @field_validator("placa")
    @classmethod
    def valida_placa(cls, v):
        v = v.strip().upper().replace("-", "")
        # Aceita padrão antigo (ABC1234) e Mercosul (ABC1D23)
        if not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", v):
            raise ValueError("Placa inválida. Use ABC1234 ou ABC1D23.")
        return v

    @field_validator("ano")
    @classmethod
    def valida_ano(cls, v):
        return _valida_ano(v)


class VeiculoUpdate(BaseModel):
    modelo: str = Field(min_length=1, max_length=50)
    marca: str = Field(min_length=1, max_length=50)
    ano: int

    @field_validator("ano")
    @classmethod
    def valida_ano(cls, v):
        return _valida_ano(v)


class PecaIn(BaseModel):
    descricao: str = Field(min_length=2, max_length=100)
    quantidade_estoque: int = Field(ge=0)
    preco_unitario: float = Field(gt=0)


class EntradaEstoque(BaseModel):
    quantidade: int = Field(gt=0)


class ServicoIn(BaseModel):
    descricao: str = Field(min_length=2, max_length=100)
    valor_padrao: float = Field(gt=0)


class OrcamentoIn(BaseModel):
    placa_veiculo: str
    descricao: Optional[str] = Field(default=None, max_length=255)
    valor_total: float = Field(gt=0)
    data_validade: date

    @field_validator("placa_veiculo")
    @classmethod
    def valida_placa(cls, v):
        return v.strip().upper().replace("-", "")


class OrdemServicoIn(BaseModel):
    numero_orcamento: int
    observacoes: Optional[str] = Field(default=None, max_length=255)


class MecanicoNaOS(BaseModel):
    id_mecanico: int


class TipoItem(str, Enum):
    PECA = "PECA"
    SERVICO = "SERVICO"


class ItemOSIn(BaseModel):
    tipo: TipoItem
    id_item: int  
    quantidade: int = Field(default=1, ge=1)
    valor_unitario: Optional[float] = Field(default=None, ge=0)  


class AtualizacaoStatus(BaseModel):
    novo_status: StatusOS


SESSOES = {}  
DURACAO_SESSAO = timedelta(hours=8)
esquema_bearer = HTTPBearer(auto_error=False)  


def gerar_hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + h.hex()


def conferir_senha(senha: str, armazenado: str) -> bool:
    salt_hex, hash_hex = armazenado.split("$")
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(h.hex(), hash_hex)


def exigir_login(cred: HTTPAuthorizationCredentials = Depends(esquema_bearer)):
    """Dependência que protege todas as rotas do sistema."""
    sessao = SESSOES.get(cred.credentials) if cred else None
    if sessao is None or sessao["expira"] < datetime.now():
        if cred:
            SESSOES.pop(cred.credentials, None)
        raise HTTPException(status_code=401, detail="Não autenticado. Faça login.")
    return sessao["usuario"]


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=50)
    senha: str = Field(min_length=1, max_length=100)


@app.get("/", include_in_schema=False)
def pagina_inicial():
    return FileResponse(BASE_DIR / "index.html")


@app.post("/login", tags=["Autenticação"])
def fazer_login(dados: LoginIn):
    with conexao() as con:
        u = con.execute("SELECT * FROM Usuario WHERE login=?", (dados.login,)).fetchone()
    if u is None or not conferir_senha(dados.senha, u["senhaHash"]):
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    token = secrets.token_hex(32)
    SESSOES[token] = {"usuario": u["login"], "nome": u["nome"],
                      "expira": datetime.now() + DURACAO_SESSAO}
    return {"token": token, "nome": u["nome"]}


@app.post("/logout", tags=["Autenticação"])
def fazer_logout(cred: HTTPAuthorizationCredentials = Depends(esquema_bearer)):
    if cred:
        SESSOES.pop(cred.credentials, None)
    return {"mensagem": "Sessão encerrada."}


rotas = APIRouter(dependencies=[Depends(exigir_login)])



# ---------- Clientes ----------
@rotas.post("/clientes", status_code=201, tags=["Clientes"])
def cadastrar_cliente(c: ClienteIn):
    with conexao() as con:
        try:
            cur = con.execute(
                "INSERT INTO Cliente (nome, telefone, cpf, endereco) VALUES (?,?,?,?)",
                (c.nome, c.telefone, c.cpf, c.endereco))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Já existe um cliente com este CPF.")
        return {"mensagem": "Cliente cadastrado com sucesso.", "idCliente": cur.lastrowid}


@rotas.get("/clientes", tags=["Clientes"])
def listar_clientes():
    with conexao() as con:
        return [dict(r) for r in con.execute("SELECT * FROM Cliente WHERE ativo=1").fetchall()]


@rotas.get("/clientes/{id_cliente}", tags=["Clientes"])
def consultar_cliente(id_cliente: int):
    with conexao() as con:
        r = _cliente_ativo(con, id_cliente)
        return Cliente(r["nome"], r["cpf"], r["telefone"], r["endereco"],
                       r["idCliente"]).exibir_detalhes()


@rotas.put("/clientes/{id_cliente}", tags=["Clientes"])
def editar_cliente(id_cliente: int, c: ClienteIn):
    with conexao() as con:
        _cliente_ativo(con, id_cliente)
        try:
            con.execute(
                "UPDATE Cliente SET nome=?, telefone=?, cpf=?, endereco=? WHERE idCliente=?",
                (c.nome, c.telefone, c.cpf, c.endereco, id_cliente))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Já existe um cliente com este CPF.")
        return {"mensagem": "Cliente atualizado com sucesso."}


@rotas.delete("/clientes/{id_cliente}", tags=["Clientes"])
def excluir_cliente(id_cliente: int):
    """Exclusão lógica. Bloqueada se o cliente ainda tiver veículos ativos."""
    with conexao() as con:
        _cliente_ativo(con, id_cliente)
        qtd = con.execute("SELECT COUNT(*) FROM Veiculo WHERE idCliente=? AND ativo=1",
                          (id_cliente,)).fetchone()[0]
        if qtd:
            raise HTTPException(status_code=409,
                                detail="Cliente possui veículos ativos. Exclua os veículos primeiro.")
        con.execute("UPDATE Cliente SET ativo=0 WHERE idCliente=?", (id_cliente,))
        return {"mensagem": "Cliente excluído (exclusão lógica)."}


# ---------- Veículos ----------
@rotas.post("/veiculos", status_code=201, tags=["Veículos"])
def cadastrar_veiculo(v: VeiculoIn):
    with conexao() as con:
        _cliente_ativo(con, v.id_cliente)
        try:
            con.execute(
                "INSERT INTO Veiculo (placa, modelo, marca, ano, idCliente) VALUES (?,?,?,?,?)",
                (v.placa, v.modelo, v.marca, v.ano, v.id_cliente))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Já existe um veículo com esta placa.")
        return {"mensagem": "Veículo cadastrado com sucesso.", "placa": v.placa}


@rotas.get("/veiculos", tags=["Veículos"])
def listar_veiculos(id_cliente: Optional[int] = None):
    with conexao() as con:
        if id_cliente is None:
            rows = con.execute("SELECT * FROM Veiculo WHERE ativo=1").fetchall()
        else:
            rows = con.execute("SELECT * FROM Veiculo WHERE ativo=1 AND idCliente=?",
                               (id_cliente,)).fetchall()
        return [dict(r) for r in rows]


@rotas.get("/veiculos/{placa}", tags=["Veículos"])
def consultar_veiculo(placa: str):
    with conexao() as con:
        return dict(_veiculo_ativo(con, placa.upper()))


@rotas.put("/veiculos/{placa}", tags=["Veículos"])
def editar_veiculo(placa: str, v: VeiculoUpdate):
    with conexao() as con:
        _veiculo_ativo(con, placa.upper())
        con.execute("UPDATE Veiculo SET modelo=?, marca=?, ano=? WHERE placa=?",
                    (v.modelo, v.marca, v.ano, placa.upper()))
        return {"mensagem": "Veículo atualizado com sucesso."}


@rotas.delete("/veiculos/{placa}", tags=["Veículos"])
def excluir_veiculo(placa: str):
    """Exclusão lógica. Bloqueada se houver OS não finalizada para o veículo."""
    placa = placa.upper()
    with conexao() as con:
        _veiculo_ativo(con, placa)
        abertas = con.execute(
            "SELECT COUNT(*) FROM OrdemServico WHERE placaVeiculo=? AND status != 'FINALIZADA'",
            (placa,)).fetchone()[0]
        if abertas:
            raise HTTPException(status_code=409, detail="Veículo possui OS em andamento.")
        con.execute("UPDATE Veiculo SET ativo=0 WHERE placa=?", (placa,))
        return {"mensagem": "Veículo excluído (exclusão lógica)."}


@rotas.get("/veiculos/{placa}/historico", tags=["Veículos"])
def historico_manutencao(placa: str):
    """Histórico de manutenção do veículo (problema citado no relatório)."""
    placa = placa.upper()
    with conexao() as con:
        _veiculo_ativo(con, placa)
        rows = con.execute(
            """SELECT numeroOS, dataAbertura, dataConclusao, status, valorTotal
               FROM OrdemServico WHERE placaVeiculo=? ORDER BY numeroOS""", (placa,)).fetchall()
        return {"placa": placa, "ordens_servico": [dict(r) for r in rows]}


# ---------- Mecânicos ----------
@rotas.post("/mecanicos", status_code=201, tags=["Mecânicos"])
def cadastrar_mecanico(m: MecanicoIn):
    with conexao() as con:
        try:
            cur = con.execute(
                "INSERT INTO Mecanico (nome, cpf, telefone, especialidade) VALUES (?,?,?,?)",
                (m.nome, m.cpf, m.telefone, m.especialidade))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Já existe um mecânico com este CPF.")
        return {"mensagem": "Mecânico cadastrado com sucesso.", "idMecanico": cur.lastrowid}


@rotas.get("/mecanicos", tags=["Mecânicos"])
def listar_mecanicos():
    with conexao() as con:
        return [dict(r) for r in con.execute("SELECT * FROM Mecanico WHERE ativo=1").fetchall()]


@rotas.get("/mecanicos/{id_mecanico}", tags=["Mecânicos"])
def consultar_mecanico(id_mecanico: int):
    with conexao() as con:
        r = _mecanico_ativo(con, id_mecanico)
        return Mecanico(r["nome"], r["cpf"], r["telefone"], r["especialidade"],
                        r["idMecanico"]).exibir_detalhes()


@rotas.put("/mecanicos/{id_mecanico}", tags=["Mecânicos"])
def editar_mecanico(id_mecanico: int, m: MecanicoIn):
    with conexao() as con:
        _mecanico_ativo(con, id_mecanico)
        try:
            con.execute(
                "UPDATE Mecanico SET nome=?, telefone=?, cpf=?, especialidade=? WHERE idMecanico=?",
                (m.nome, m.telefone, m.cpf, m.especialidade, id_mecanico))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Já existe um mecânico com este CPF.")
        return {"mensagem": "Mecânico atualizado com sucesso."}


@rotas.delete("/mecanicos/{id_mecanico}", tags=["Mecânicos"])
def excluir_mecanico(id_mecanico: int):
    """Exclusão lógica. Bloqueada se o mecânico estiver em OS não finalizada."""
    with conexao() as con:
        _mecanico_ativo(con, id_mecanico)
        em_uso = con.execute(
            """SELECT COUNT(*) FROM OrdemServico_Mecanico om
               JOIN OrdemServico o ON o.numeroOS = om.numeroOS
               WHERE om.idMecanico=? AND o.status != 'FINALIZADA'""",
            (id_mecanico,)).fetchone()[0]
        if em_uso:
            raise HTTPException(status_code=409, detail="Mecânico está em OS em andamento.")
        con.execute("UPDATE Mecanico SET ativo=0 WHERE idMecanico=?", (id_mecanico,))
        return {"mensagem": "Mecânico excluído (exclusão lógica)."}


# ---------- Peças e Serviços (catálogos) ----------
@rotas.post("/pecas", status_code=201, tags=["Peças"])
def cadastrar_peca(p: PecaIn):
    with conexao() as con:
        cur = con.execute(
            "INSERT INTO Peca (descricao, quantidadeEstoque, precoUnitario) VALUES (?,?,?)",
            (p.descricao, p.quantidade_estoque, p.preco_unitario))
        return {"mensagem": "Peça cadastrada com sucesso.", "idPeca": cur.lastrowid}


@rotas.get("/pecas", tags=["Peças"])
def listar_pecas():
    with conexao() as con:
        return [dict(r) for r in con.execute("SELECT * FROM Peca").fetchall()]


@rotas.patch("/pecas/{id_peca}/estoque", tags=["Peças"])
def repor_estoque(id_peca: int, e: EntradaEstoque):
    with conexao() as con:
        _buscar(con, "SELECT * FROM Peca WHERE idPeca=?", (id_peca,), "Peça não encontrada.")
        con.execute("UPDATE Peca SET quantidadeEstoque = quantidadeEstoque + ? WHERE idPeca=?",
                    (e.quantidade, id_peca))
        nova = con.execute("SELECT quantidadeEstoque FROM Peca WHERE idPeca=?",
                           (id_peca,)).fetchone()[0]
        return {"mensagem": "Estoque atualizado.", "quantidadeEstoque": nova}


@rotas.post("/servicos", status_code=201, tags=["Serviços"])
def cadastrar_servico(s: ServicoIn):
    with conexao() as con:
        cur = con.execute("INSERT INTO Servico (descricao, valorPadrao) VALUES (?,?)",
                          (s.descricao, s.valor_padrao))
        return {"mensagem": "Serviço cadastrado com sucesso.", "idServico": cur.lastrowid}


@rotas.get("/servicos", tags=["Serviços"])
def listar_servicos():
    with conexao() as con:
        return [dict(r) for r in con.execute("SELECT * FROM Servico").fetchall()]


# ---------- Orçamentos ----------
@rotas.post("/orcamentos", status_code=201, tags=["Orçamentos"])
def criar_orcamento(o: OrcamentoIn):
    with conexao() as con:
        _veiculo_ativo(con, o.placa_veiculo)
        cur = con.execute(
            """INSERT INTO Orcamento (dataCriacao, dataValidade, descricao, valorTotal, placaVeiculo)
               VALUES (?,?,?,?,?)""",
            (_agora(), o.data_validade.isoformat(), o.descricao, o.valor_total, o.placa_veiculo))
        return {"mensagem": "Orçamento criado (status PENDENTE).", "numeroOrcamento": cur.lastrowid}


@rotas.get("/orcamentos", tags=["Orçamentos"])
def listar_orcamentos(status: Optional[str] = None):
    with conexao() as con:
        if status:
            rows = con.execute("SELECT * FROM Orcamento WHERE status=?",
                               (status.upper(),)).fetchall()
        else:
            rows = con.execute("SELECT * FROM Orcamento").fetchall()
        return [dict(r) for r in rows]


@rotas.get("/orcamentos/{numero}", tags=["Orçamentos"])
def consultar_orcamento(numero: int):
    with conexao() as con:
        return dict(_orcamento_ou_404(con, numero))


@rotas.put("/orcamentos/{numero}/aprovar", tags=["Orçamentos"])
def aprovar_orcamento(numero: int):
    with conexao() as con:
        o = _orcamento_ou_404(con, numero)
        if o["status"] != "PENDENTE":
            raise HTTPException(status_code=409, detail=f"Orçamento já está {o['status']}.")
        if o["dataValidade"] < date.today().isoformat():
            raise HTTPException(status_code=409, detail="Orçamento vencido; crie um novo.")
        con.execute("UPDATE Orcamento SET status='APROVADO' WHERE numeroOrcamento=?", (numero,))
        return {"mensagem": "Orçamento aprovado."}


@rotas.put("/orcamentos/{numero}/rejeitar", tags=["Orçamentos"])
def rejeitar_orcamento(numero: int):
    with conexao() as con:
        o = _orcamento_ou_404(con, numero)
        if o["status"] != "PENDENTE":
            raise HTTPException(status_code=409, detail=f"Orçamento já está {o['status']}.")
        con.execute("UPDATE Orcamento SET status='REJEITADO' WHERE numeroOrcamento=?", (numero,))
        return {"mensagem": "Orçamento rejeitado."}


# ---------- Ordens de Serviço ----------
@rotas.post("/os", status_code=201, tags=["Ordens de Serviço"])
def abrir_ordem_servico(dados: OrdemServicoIn):
    """Regra: só abre OS a partir de um orçamento APROVADO (e só uma OS por orçamento)."""
    with conexao() as con:
        o = _orcamento_ou_404(con, dados.numero_orcamento)
        if o["status"] != "APROVADO":
            raise HTTPException(status_code=409,
                                detail="A OS só pode ser aberta a partir de um orçamento APROVADO.")
        ja_existe = con.execute("SELECT 1 FROM OrdemServico WHERE numeroOrcamento=?",
                                (dados.numero_orcamento,)).fetchone()
        if ja_existe:
            raise HTTPException(status_code=409, detail="Este orçamento já gerou uma OS.")
        cur = con.execute(
            """INSERT INTO OrdemServico (dataAbertura, status, valorTotal, observacoes,
                                         placaVeiculo, numeroOrcamento)
               VALUES (?, 'ABERTA', 0, ?, ?, ?)""",
            (_agora(), dados.observacoes, o["placaVeiculo"], dados.numero_orcamento))
        return {"mensagem": "Ordem de Serviço aberta.", "os": _detalhe_os(con, cur.lastrowid)}


@rotas.get("/os", tags=["Ordens de Serviço"])
def listar_os(status: Optional[StatusOS] = None):
    with conexao() as con:
        if status:
            rows = con.execute("SELECT * FROM OrdemServico WHERE status=?",
                               (status.value,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM OrdemServico").fetchall()
        return [dict(r) for r in rows]


@rotas.get("/os/{numero_os}", tags=["Ordens de Serviço"])
def consultar_os(numero_os: int):
    with conexao() as con:
        return _detalhe_os(con, numero_os)


@rotas.post("/os/{numero_os}/mecanicos", status_code=201, tags=["Ordens de Serviço"])
def atribuir_mecanico(numero_os: int, dados: MecanicoNaOS):
    with conexao() as con:
        os_row = _os_ou_404(con, numero_os)
        if os_row["status"] == StatusOS.FINALIZADA.value:
            raise HTTPException(status_code=409, detail="OS já finalizada.")
        _mecanico_ativo(con, dados.id_mecanico)
        try:
            con.execute("INSERT INTO OrdemServico_Mecanico (numeroOS, idMecanico) VALUES (?,?)",
                        (numero_os, dados.id_mecanico))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Mecânico já está atribuído a esta OS.")
        return {"mensagem": "Mecânico atribuído à OS.", "os": _detalhe_os(con, numero_os)}


@rotas.post("/os/{numero_os}/itens", status_code=201, tags=["Ordens de Serviço"])
def adicionar_item(numero_os: int, item: ItemOSIn):
    """Adiciona peça (baixa o estoque) ou serviço à OS e recalcula o valor total."""
    with conexao() as con:
        os_row = _os_ou_404(con, numero_os)
        if os_row["status"] in (StatusOS.PRONTO.value, StatusOS.FINALIZADA.value):
            raise HTTPException(status_code=409,
                                detail="Não é possível alterar itens de uma OS pronta/finalizada.")
        if item.tipo == TipoItem.PECA:
            peca = _buscar(con, "SELECT * FROM Peca WHERE idPeca=?", (item.id_item,),
                           "Peça não encontrada.")
            if peca["quantidadeEstoque"] < item.quantidade:
                raise HTTPException(
                    status_code=409,
                    detail=f"Estoque insuficiente (disponível: {peca['quantidadeEstoque']}). "
                           "Considere mudar a OS para AGUARDANDO_PECAS.")
            valor = item.valor_unitario if item.valor_unitario is not None else peca["precoUnitario"]
            con.execute("UPDATE Peca SET quantidadeEstoque = quantidadeEstoque - ? WHERE idPeca=?",
                        (item.quantidade, item.id_item))
            con.execute(
                """INSERT INTO ItemOS (numeroOS, idPeca, idServico, quantidade, valorUnitario)
                   VALUES (?, ?, NULL, ?, ?)""", (numero_os, item.id_item, item.quantidade, valor))
        else:
            serv = _buscar(con, "SELECT * FROM Servico WHERE idServico=?", (item.id_item,),
                           "Serviço não encontrado.")
            valor = item.valor_unitario if item.valor_unitario is not None else serv["valorPadrao"]
            con.execute(
                """INSERT INTO ItemOS (numeroOS, idPeca, idServico, quantidade, valorUnitario)
                   VALUES (?, NULL, ?, ?, ?)""", (numero_os, item.id_item, item.quantidade, valor))
        _recalcular_total(con, numero_os)
        return {"mensagem": "Item adicionado.", "os": _detalhe_os(con, numero_os)}


@rotas.put("/os/{numero_os}/status", tags=["Ordens de Serviço"])
def atualizar_status_os(numero_os: int, atualizacao: AtualizacaoStatus):
    with conexao() as con:
        os_row = _os_ou_404(con, numero_os)
        dominio = _dominio_os(con, os_row)
        novo = atualizacao.novo_status
        if not dominio.pode_ir_para(novo):
            permitidos = [s.value for s in OrdemServico.TRANSICOES[dominio.status]]
            raise HTTPException(
                status_code=409,
                detail=f"Transição inválida: {dominio.status.value} -> {novo.value}. "
                       f"Permitido a partir de {dominio.status.value}: {permitidos or 'nenhum (OS encerrada)'}.")
        if novo == StatusOS.EM_EXECUCAO:
            tem_mecanico = con.execute(
                "SELECT 1 FROM OrdemServico_Mecanico WHERE numeroOS=?", (numero_os,)).fetchone()
            if not tem_mecanico:
                raise HTTPException(status_code=409,
                                    detail="Atribua ao menos um mecânico antes de iniciar a execução.")
        con.execute("UPDATE OrdemServico SET status=? WHERE numeroOS=?", (novo.value, numero_os))
        if novo in (StatusOS.PRONTO, StatusOS.FINALIZADA) and os_row["dataConclusao"] is None:
            con.execute("UPDATE OrdemServico SET dataConclusao=? WHERE numeroOS=?",
                        (_agora(), numero_os))
        return {"mensagem": f"Status alterado para {novo.value}.", "os": _detalhe_os(con, numero_os)}


app.include_router(rotas)
