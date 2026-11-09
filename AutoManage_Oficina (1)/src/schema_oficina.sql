PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Cliente (
    idCliente INTEGER PRIMARY KEY AUTOINCREMENT,
    nome      TEXT NOT NULL,
    telefone  TEXT NOT NULL,
    cpf       TEXT NOT NULL UNIQUE,
    endereco  TEXT,
    ativo     INTEGER NOT NULL DEFAULT 1          
);

CREATE TABLE IF NOT EXISTS Veiculo (
    placa     TEXT PRIMARY KEY,
    modelo    TEXT NOT NULL,
    marca     TEXT NOT NULL,
    ano       INTEGER NOT NULL,
    idCliente INTEGER NOT NULL,
    ativo     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (idCliente) REFERENCES Cliente(idCliente)
);

CREATE TABLE IF NOT EXISTS Mecanico (
    idMecanico    INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    cpf           TEXT NOT NULL UNIQUE,
    telefone      TEXT NOT NULL,
    especialidade TEXT NOT NULL,
    ativo         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Peca (
    idPeca            INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao         TEXT NOT NULL,
    quantidadeEstoque INTEGER NOT NULL DEFAULT 0 CHECK (quantidadeEstoque >= 0),
    precoUnitario     REAL NOT NULL CHECK (precoUnitario > 0)
);

CREATE TABLE IF NOT EXISTS Servico (
    idServico   INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao   TEXT NOT NULL,
    valorPadrao REAL NOT NULL CHECK (valorPadrao > 0)
);

CREATE TABLE IF NOT EXISTS Orcamento (
    numeroOrcamento INTEGER PRIMARY KEY AUTOINCREMENT,
    dataCriacao     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dataValidade    TEXT NOT NULL,
    descricao       TEXT,
    valorTotal      REAL NOT NULL CHECK (valorTotal > 0),
    status          TEXT NOT NULL DEFAULT 'PENDENTE'
                    CHECK (status IN ('PENDENTE', 'APROVADO', 'REJEITADO')),
    placaVeiculo    TEXT NOT NULL,
    FOREIGN KEY (placaVeiculo) REFERENCES Veiculo(placa)
);

CREATE TABLE IF NOT EXISTS OrdemServico (
    numeroOS        INTEGER PRIMARY KEY AUTOINCREMENT,
    dataAbertura    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dataConclusao   TEXT,
    status          TEXT NOT NULL DEFAULT 'ABERTA'
                    CHECK (status IN ('ABERTA', 'EM_ANALISE', 'AGUARDANDO_PECAS',
                                      'EM_EXECUCAO', 'PRONTO', 'FINALIZADA')),
    valorTotal      REAL NOT NULL DEFAULT 0,
    observacoes     TEXT,
    placaVeiculo    TEXT NOT NULL,
    numeroOrcamento INTEGER NOT NULL UNIQUE,      
    FOREIGN KEY (placaVeiculo)    REFERENCES Veiculo(placa),
    FOREIGN KEY (numeroOrcamento) REFERENCES Orcamento(numeroOrcamento)
);

CREATE TABLE IF NOT EXISTS OrdemServico_Mecanico (
    numeroOS   INTEGER NOT NULL,
    idMecanico INTEGER NOT NULL,
    PRIMARY KEY (numeroOS, idMecanico),
    FOREIGN KEY (numeroOS)   REFERENCES OrdemServico(numeroOS) ON DELETE CASCADE,
    FOREIGN KEY (idMecanico) REFERENCES Mecanico(idMecanico)
);

CREATE TABLE IF NOT EXISTS ItemOS (
    idItem        INTEGER PRIMARY KEY AUTOINCREMENT,
    numeroOS      INTEGER NOT NULL,
    idPeca        INTEGER,
    idServico     INTEGER,
    quantidade    INTEGER NOT NULL CHECK (quantidade >= 1),
    valorUnitario REAL NOT NULL CHECK (valorUnitario >= 0),
    FOREIGN KEY (numeroOS)  REFERENCES OrdemServico(numeroOS) ON DELETE CASCADE,
    FOREIGN KEY (idPeca)    REFERENCES Peca(idPeca),
    FOREIGN KEY (idServico) REFERENCES Servico(idServico),
    CHECK ((idPeca IS NOT NULL AND idServico IS NULL) OR
           (idPeca IS NULL AND idServico IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS Usuario (
    idUsuario INTEGER PRIMARY KEY AUTOINCREMENT,
    login     TEXT NOT NULL UNIQUE,
    senhaHash TEXT NOT NULL,
    nome      TEXT NOT NULL
);
