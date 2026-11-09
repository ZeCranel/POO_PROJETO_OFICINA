# AutoManage - Sistema para Oficina Mecânica

Sistema web com front-end (HTML/JavaScript), API em Python (FastAPI) e banco SQLite.
O arquivo `oficina.db` é criado automaticamente na primeira execução, a partir do `schema_oficina.sql`.

## Como executar (Windows)

1. Extraia o zip e abra um terminal **dentro da pasta onde estão `src` e `requirements.txt`**
   (no Explorador de Arquivos: botão direito em espaço vazio -> "Abrir no Terminal").
2. Rode:

```
cd src
python -m pip install -r ..\requirements.txt
python -m uvicorn backend_oficina:app --reload
```

3. Abra no navegador: **http://127.0.0.1:8000/**
4. Entre com o usuário de demonstração: **admin** / **admin123**

Para parar o servidor: `Ctrl + C` no terminal.
Para zerar os dados: pare o servidor e apague o arquivo `src/oficina.db`.

## Passo a passo de uso

1. Clientes -> Veículos
2. Mecânicos, Peças e Serviços
3. Orçamentos -> **Aprovar**
4. Ordens de Serviço -> abrir OS a partir do orçamento aprovado -> **Gerenciar**
5. Na OS: atribuir mecânico, adicionar peças/serviços (peça baixa o estoque) e avançar o status até Finalizada.
   Em Veículos, o botão **Histórico** mostra a manutenção do veículo.

## Fluxo de status da OS

ABERTA -> EM_ANALISE -> (EM_EXECUCAO <-> AGUARDANDO_PECAS) -> PRONTO -> FINALIZADA

## Documentação da API (opcional)

http://127.0.0.1:8000/docs - todas as rotas, exceto `/login`, exigem autenticação.
Para testar por lá: execute `POST /login`, copie o `token` da resposta, clique em **Authorize** e cole o token.

## Regras de negócio implementadas

- CPF único e com 11 dígitos; placa no padrão antigo ou Mercosul; veículo precisa de cliente existente.
- Exclusão lógica de cliente, veículo e mecânico (bloqueada se houver vínculo ativo).
- Orçamento: pendente -> aprovado/rejeitado; orçamento vencido não pode ser aprovado.
- A OS só abre a partir de orçamento aprovado (um orçamento gera no máximo uma OS).
- Mudanças de status seguem o fluxo acima; para iniciar a execução é preciso ter mecânico atribuído.
- Adicionar peça à OS baixa o estoque (bloqueia se insuficiente); o valor total da OS é recalculado.
- Login com senha protegida por hash (PBKDF2) e sessão por token (8 horas).
