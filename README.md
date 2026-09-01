# DOC Intelligence

Serviço interno de inteligência documental com PostgreSQL, object storage criptografado, fila durável, workers independentes, OIDC/RBAC, Alembic, governança LGPD e avaliações versionadas.

## Arquitetura de execução

- **API:** FastAPI; valida e persiste o upload, grava o outbox e responde sem esperar o LLM.
- **Banco:** PostgreSQL gerenciado via SQLAlchemy Async/asyncpg.
- **Arquivos:** S3 compatível, sempre com SSE; produção exige KMS.
- **Fila:** Redis persistente + ARQ. API e workers são processos separados.
- **Identidade:** OIDC Authorization Code + PKCE na SPA; JWT e roles validados novamente na API.
- **Schema:** somente Alembic altera o banco do runtime.
- **Governança:** retenção, legal hold, descarte e auditoria append-only.
- **Qualidade:** datasets golden com métricas por campo/modelo/prompt.

## Ambiente local equivalente

Requisitos: Python 3.11+, Docker Desktop e portas 5432, 6379, 8080, 9000 e 9001 livres.

> O arquivo `.env` antigo usava SQLite. Faça uma cópia dele se necessário e gere um novo a partir do exemplo.

```powershell
Set-Location -LiteralPath "C:\Users\maria\OneDrive\Documentos\ChatGPT\LEITOR-DOC"

Copy-Item ".env.example" ".env" -Force
& ".\.venv\Scripts\python.exe" -m pip install -r ".\requirements.txt"

docker compose up -d
& ".\.venv\Scripts\python.exe" -m alembic upgrade head
```

Inicie a API:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8765 --env-file ".env"
```

Em outro PowerShell, inicie o worker independente:

```powershell
Set-Location -LiteralPath "C:\Users\maria\OneDrive\Documentos\ChatGPT\LEITOR-DOC"
& ".\.venv\Scripts\python.exe" -m arq app.worker.WorkerSettings
```

Abra [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Interface de atendimento

A SPA é servida pela própria API e oferece quatro áreas conforme o RBAC do usuário:

- **Receber:** upload em lote por arrastar e soltar, validação local, até três envios simultâneos e indicação de duplicidade.
- **Acompanhar:** visão operacional atualizada automaticamente com fila, processamento, falhas e itens aguardando conferência.
- **Conferir:** documento original e campos extraídos lado a lado, reserva exclusiva do item, correção, aprovação ou rejeição.
- **Acervo:** busca por nome, tipo e valores extraídos, filtros por canal/status e painel com original, resultado e rastreabilidade.

Usuários demonstrativos do Keycloak:

| Usuário | Senha | Permissões |
|---|---|---|
| `atendimento` | `Atendimento123!` | receber e ler |
| `conferente` | `Conferente123!` | receber, ler e conferir |
| `administrador` | `Administrador123!` | todas |

Essas credenciais existem apenas para desenvolvimento e devem ser removidas fora do ambiente local.
O MinIO local usa uma chave estática exclusivamente para exercitar SSE; produção exige KMS gerenciado.

## Serviços gerenciados em produção

Configure os mesmos contratos com serviços do provedor escolhido:

```ini
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://usuario:senha@host:5432/docintelligence?ssl=require
REDIS_URL=rediss://usuario:senha@host:6379/0
S3_ENDPOINT_URL=
S3_REGION=sa-east-1
S3_BUCKET=empresa-doc-intelligence
S3_SSE_ALGORITHM=aws:kms
S3_SSE_KMS_KEY_ID=arn-ou-id-da-chave
S3_AUTO_CREATE_BUCKET=false
OIDC_ISSUER=https://identidade.empresa/realms/interno
OIDC_AUDIENCE=doc-intelligence-api
OIDC_CLIENT_ID=doc-intelligence-spa
AUTH_DISABLED=false
AUDIT_HMAC_KEY=segredo-aleatorio-com-pelo-menos-32-caracteres
```

A validação de startup impede produção com SQLite, autenticação desabilitada, chave de auditoria de desenvolvimento ou storage sem KMS.

## RBAC

| Role | Permissão |
|---|---|
| `document.submit` | enviar documentos |
| `document.read` | listar, consultar e visualizar conteúdo |
| `document.review` | reservar e conferir documentos |
| `document.admin` | retry, retenção, legal hold, descarte e auditoria |

O identificador do operador vem do claim `sub`; não é aceito do formulário ou do navegador.

## Migrações

```powershell
& ".\.venv\Scripts\python.exe" -m alembic current
& ".\.venv\Scripts\python.exe" -m alembic upgrade head
```

A primeira migração cria documentos, tentativas, outbox, auditoria e execuções de avaliação. No PostgreSQL, um trigger impede update/delete da auditoria.

## Avaliações do modelo

Não versione amostras reais com PII. Monte o dataset em armazenamento controlado e execute:

```powershell
& ".\.venv\Scripts\python.exe" -m evals.runner ".\evals\datasets\golden-v1.json" --persist --output ".\data\eval-report.json"
```

O relatório contém acurácia de tipo, acurácia por campo, confiança média, taxa automática, falso aceite e recall da revisão, identificados por dataset, Strategy, modelo e prompt.

## Verificação

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

Os testes usam SQLite e storage/fila em memória **somente como doubles de teste**. O runtime normal rejeita SQLite.

## Documentação

- [Arquitetura e fatos A–G](docs/ARCHITECTURE.md)
- [Política operacional LGPD](docs/LGPD_POLICY.md)
- [ADRs](docs/adrs)
- [Avaliações](evals/README.md)
