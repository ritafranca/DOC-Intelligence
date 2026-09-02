# DOC Intelligence

Serviço interno de inteligência documental com PostgreSQL, object storage criptografado, fila durável, workers independentes, OIDC/RBAC, Alembic, governança LGPD e avaliações versionadas.

## Arquitetura de execução

- **API:** FastAPI; valida e persiste o upload, grava o outbox e responde sem esperar o LLM.
- **Banco:** PostgreSQL gerenciado via SQLAlchemy Async/asyncpg.
- **Arquivos:** S3 compatível, sempre com SSE; produção exige KMS.
- **Fila:** Redis persistente + ARQ. API e workers são processos separados.
- **Identidade:** JWT local com usuários persistidos no desenvolvimento; OIDC Authorization Code + PKCE em produção.
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

Sem Docker, uma demonstração local pode usar SQLite, storage local e um consumidor embutido. Esse modo é exclusivo para dados fictícios e nunca substitui Redis/ARQ, PostgreSQL e S3/KMS em produção:

```powershell
$env:TESTING = "true"
$env:ENVIRONMENT = "test"
$env:AUTH_DISABLED = "false"
$env:AUTH_PROVIDER = "local"
$env:JWT_SECRET_KEY = "dev-only-change-this-jwt-secret-now"
$env:DEMO_AUTOPROCESS = "true"
$env:DATABASE_URL = "sqlite+aiosqlite:///./data/ui-demo.db"
$env:DATA_DIR = "./data/ui-demo-storage"
$env:EXTRACTOR_STRATEGY = "local"
& ".\.venv311\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Nesse modo, o startup cria de forma idempotente o administrador `admin@doc.local`, com senha `admin`. Troque `DEFAULT_ADMIN_PASSWORD` e `JWT_SECRET_KEY` antes de usar um ambiente compartilhado. Administradores podem criar novos acessos pela área **Gerenciar acessos**.

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
AUTH_PROVIDER=oidc
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

Para gerar um dataset reproduzível de RGs totalmente fictícios e avaliar diretamente a estratégia OpenAI:

```powershell
& ".\.venv\Scripts\python.exe" ".\scripts\generate_mock_dataset.py" --count 20 --seed 20260902
$env:OPENAI_API_KEY = "chave-do-projeto"
& ".\.venv\Scripts\python.exe" ".\scripts\evaluate_model.py" --concurrency 2 --output ".\tests\dataset\reports\openai-gpt-4o.json"
```

O segundo comando faz uma chamada cobrada por imagem. As amostras têm marca d'água de documento fictício; os artefatos gerados ficam fora do Git e os gabaritos seguem exatamente o contrato do prompt v2. Use `--min-field-accuracy` e `--min-document-type-accuracy` como quality gates da CI.

O workflow `DOC Intelligence synthetic evaluation` executa os testes sem custo em push e pull request. A etapa com o modelo real roda somente por acionamento manual no GitHub Actions e exige o secret `OPENAI_API_KEY`; o relatório fica disponível como artefato por 30 dias.

## Extração local e offline

O padrão do projeto é `EXTRACTOR_STRATEGY=local`: PaddleOCR em CPU, pré-processamento OpenCV e heurísticas versionadas. O runtime requer Python 3.11 a 3.13, pois o PaddlePaddle ainda não publica wheel para Python 3.14.

```powershell
py -3.11 -m venv .venv311
& ".\.venv311\Scripts\python.exe" -m pip install -r ".\requirements.txt"
$env:EXTRACTOR_STRATEGY = "local"
& ".\.venv311\Scripts\python.exe" -m arq app.worker.WorkerSettings
```

Na primeira preparação, o PaddleOCR baixa os pesos oficiais para o cache local. Depois disso, a inferência de imagens é offline e nenhum conteúdo documental é enviado a terceiros. Para uma instalação isolada da internet, copie previamente o cache de modelos para o host. PDFs usam `pdf2image`/Poppler na primeira página, com fallback local para PyMuPDF.

O executor local usa um único job de OCR por processo por padrão, evitando saturar o i7 de quatro núcleos. Ajuste `LOCAL_OCR_CPU_THREADS` e `LOCAL_OCR_EXECUTOR_WORKERS` apenas após medir o dataset golden.

Avalie o OCR local contra os documentos fictícios sem custo de API:

```powershell
& ".\.venv311\Scripts\python.exe" ".\scripts\evaluate_model.py" --strategy local --concurrency 1
```

## Extração multimodal com OpenAI

A chamada ao modelo acontece exclusivamente no worker ARQ. Para habilitar a estratégia real, configure o segredo fora do Git:

```ini
EXTRACTOR_STRATEGY=openai
OPENAI_API_KEY=chave-do-projeto
OPENAI_MODEL=gpt-4o
OPENAI_TIMEOUT_SECONDS=50
OPENAI_MAX_RETRIES=2
```

A estratégia `openai` usa o prompt imutável `document_extraction_v2`, envia JPEG/PNG como data URL Base64 e converte somente a primeira página de PDFs para PNG. Timeout, rate limit, erro de transporte ou JSON inválido produzem confiança `0.0` e encaminhamento para conferência humana. O provedor deve estar formalmente aprovado como operador de dados antes do uso com documentos reais.

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
