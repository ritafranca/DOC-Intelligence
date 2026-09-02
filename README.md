# DOC Intelligence

## A ideia nasceu de um problema real do escritório: 

O DOC Intelligence existe para poupar o pessoal do atendimento jurídico de passar o dia digitando, na mão, dados de RGs, CNHs, certidões e outros documentos que chegam por WhatsApp, e-mail ou balcão.

Na prática, esses arquivos quase nunca chegam bonitos. A foto está torta, o CPF ficou meio borrado, o nome do arquivo é `WhatsApp Image 2026...jpeg` e, por garantia, a mesma pessoa mandou o documento cinco vezes. O sistema recebe esse material, identifica duplicidades, tenta descobrir o tipo do documento, extrai os campos úteis e propõe um nome de arquivo que faça sentido.

Quando a confiança da extração fica abaixo de 85%, o documento não é tratado como pronto. Ele vai para uma fila de conferência humana, onde um operador vê o original ao lado dos campos extraídos, corrige o que for necessário e aprova o resultado.

O objetivo não é tirar a decisão das mãos de quem trabalha com o processo. É eliminar a digitação repetitiva e deixar a pessoa cuidar das exceções que realmente precisam de atenção.

## A entrega: 

Esta entrega cobre o caminho completo de um documento, sem fingir que já resolve todos os casos do mundo:

1. uma aplicação interna envia uma imagem ou PDF;
2. a API valida o arquivo e calcula seu SHA-256;
3. se o conteúdo já chegou antes, o processamento existente é reaproveitado;
4. o trabalho entra em uma fila, sem deixar a requisição esperando o OCR ou o modelo responder;
5. o extrator classifica o documento, coleta os campos e calcula a confiança;
6. o resultado termina como concluído ou segue para conferência humana;
7. a interface permite acompanhar, corrigir e consultar o que já foi processado.

A gente focou no caminho feliz, mas tratou os problemas que aparecem na vida real. Uma chamada multimodal pode levar de 5 a 40 segundos, falhar ou simplesmente não responder. Por isso, a extração não acontece dentro da requisição de upload. Reenvios são deduplicados pelo conteúdo, não pelo nome do arquivo. A fila de conferência usa uma reserva temporária para dois atendentes não corrigirem o mesmo documento ao mesmo tempo.

### O que foi utilizado: 

- **FastAPI, Pydantic e SQLAlchemy assíncrono:** API, validação e persistência.
- **React, Tailwind CSS e Framer Motion:** interface de upload, acompanhamento, conferência, acervo e administração de usuários.
- **PaddleOCR + OpenCV:** extração local, em CPU, sem mandar o documento para terceiros.
- **OpenAI Vision:** estratégia opcional para comparação ou uso futuro. Ela fica desligada no modo local e exige uma chave própria.
- **PostgreSQL, Redis/ARQ e S3:** caminho preparado para o ambiente completo, com banco gerenciado, fila durável e arquivos criptografados.
- **SQLite e worker embutido:** usados somente no modo de demonstração e nos testes, para facilitar a avaliação local.
- **JWT local e RBAC:** login individual no ambiente de demonstração. Em produção, o projeto continua preparado para OIDC.

## Rodando na sua máquina: 
O caminho abaixo é o mais simples para testar. Ele usa SQLite, armazenamento local e um consumidor embutido. Você não precisa subir PostgreSQL, Redis ou Docker para essa demonstração.

### 1. Confira a versão do Python

Use Python 3.11, 3.12 ou 3.13. O PaddlePaddle ainda não tem suporte para Python 3.14 neste projeto.

```powershell
py -0p
```

### 2. Entre na pasta certa

Este passo evita o clássico erro de tentar criar o ambiente virtual dentro de `C:\Windows\System32`.

```powershell
Set-Location -LiteralPath "C:\Users\maria\OneDrive\Documentos\ChatGPT\LEITOR-DOC"
```

### 3. Crie o ambiente virtual e instale tudo

Os comandos abaixo chamam o Python do ambiente diretamente. Assim, não dependemos da política de execução do `Activate.ps1`.

```powershell
py -3.11 -m venv .venv311
& ".\.venv311\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv311\Scripts\python.exe" -m pip install -r ".\requirements.txt"
```

Na primeira execução do PaddleOCR, os modelos são baixados para o cache local. Isso pode levar alguns minutos. Depois que os arquivos estiverem no cache, a extração local não precisa enviar o documento para um provedor externo.

### 4. Configure o modo de demonstração

Execute estes comandos na mesma janela do PowerShell:

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
$env:PROMPT_VERSION = "local_ocr_rules_v1"
```

### 5. Suba o back-end

```powershell
& ".\.venv311\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Se aparecer `Uvicorn running on http://127.0.0.1:8765`, está tudo certo. Deixe essa janela aberta.

O banco SQLite é criado automaticamente na pasta `data/`. O usuário administrador também é criado no primeiro startup:

```text
E-mail: admin@doc.local
Senha: admin
```

Essas credenciais são só para desenvolvimento. Se o ambiente for compartilhado com alguém, troque `DEFAULT_ADMIN_PASSWORD` e `JWT_SECRET_KEY`.

### 6. Abra o front-end

O React já é servido pelo próprio FastAPI. Não existe um segundo servidor de front-end nem um `npm start` escondido nesta versão.

Abra:

[http://127.0.0.1:8765](http://127.0.0.1:8765)

Não abra `static/index.html` diretamente com `file:///`. Nesse modo o navegador não conversa corretamente com a API e costuma mostrar `Failed to fetch`.

### Se você quiser subir a arquitetura completa

O modo acima é ótimo para demonstração. Para trabalhar com PostgreSQL, Redis, MinIO e Keycloak locais, use o ambiente Docker e aplique as migrações antes de iniciar a API:

```powershell
Copy-Item ".env.example" ".env" -Force
docker compose up -d
& ".\.venv311\Scripts\python.exe" -m alembic upgrade head
& ".\.venv311\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8765 --env-file ".env"
```

O `.env.example` mantém o login JWT local por padrão. Para exercitar o Keycloak, altere `AUTH_PROVIDER` para `oidc` e use o emissor configurado no arquivo.

Em outro PowerShell, inicie o worker:

```powershell
Set-Location -LiteralPath "C:\Users\maria\OneDrive\Documentos\ChatGPT\LEITOR-DOC"
& ".\.venv311\Scripts\python.exe" -m arq app.worker.WorkerSettings
```


## Como testar sem usar o RG de ninguém: 

O que escolhi testar e por quê: cobrimos o fluxo que mais pode causar problema operacional (upload, validação, deduplicação, autenticação, permissões, reserva concorrente da conferência, correção humana e descarte). Também validamos o contrato dos extratores e as métricas por campo. Esses testes dão segurança para mudar OCR, prompt ou modelo sem descobrir a regressão só depois que um atendente abrir um documento real.

Rode a suíte automatizada com:

```powershell
& ".\.venv311\Scripts\python.exe" -m pytest -q
```

### Dataset sintético

Documento pessoal real não deve ir para o repositório, para um print de bug ou para um dataset improvisado. Para testar a extração sem atropelar a LGPD, o projeto gera RGs fictícios com Faker e Pillow. A imagem recebe nomes, CPF, RG, datas e filiação inventados; ao lado dela fica um JSON com o gabarito exato.

Coloque o fundo do documento fictício em `templates/rg_blank.jpg` e gere as amostras:

```powershell
& ".\.venv311\Scripts\python.exe" ".\scripts\generate_mock_dataset.py" --count 20 --seed 20260902
```

Depois, avalie o OCR local:

```powershell
& ".\.venv311\Scripts\python.exe" ".\scripts\evaluate_model.py" --strategy local --concurrency 1
```

O relatório mostra a taxa de acerto por campo. Isso é mais útil do que uma nota única: dá para saber, por exemplo, se o modelo está ótimo em CPF e ruim em nome da mãe.

Os arquivos gerados ficam em:

```text
tests/dataset/images/
tests/dataset/ground_truth/
```

Essas pastas devem continuar contendo somente material fictício. Sem exceção e sem aquele “é só para testar rapidinho”.

## Mapa rápido para quem vai mexer no código

```text
app/                 API, autenticação, persistência, fila e extratores
static/index.html    SPA React
migrations/          revisões Alembic
prompts/             prompts versionados
scripts/             geração do dataset e avaliação
tests/               testes automatizados e dados sintéticos
docs/                arquitetura, ADRs e política LGPD
```

Se você alterar Python, persistência, prompt ou contrato da API, rode `pytest -q`. Se mexer no banco, crie uma migração e confira o SQL do Alembic. Se trocar OCR, modelo ou prompt, rode o dataset sintético antes de chamar a mudança de melhoria.
