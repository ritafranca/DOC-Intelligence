# DOC Intelligence — instruções para agentes

## Contexto do produto

- Este repositório processa documentos pessoais brasileiros e pode manipular PII e dados sensíveis.
- O fluxo principal é: upload → validação → deduplicação → fila → extração → conclusão ou conferência humana.
- A SPA é servida pelo FastAPI em `static/index.html`; a API versionada está em `/api/v1`.
- O runtime usa PostgreSQL, S3/KMS e Redis/ARQ. SQLite e doubles em memória são exclusivos dos testes.

## Acordos de implementação

- Use Python 3.11+, FastAPI, Pydantic v2 e SQLAlchemy 2 assíncrono.
- Não faça chamadas longas ao provedor dentro da requisição de upload.
- Preserve o Strategy Pattern em `app/extractor.py`; integrações novas implementam `DocumentExtractor`.
- Nunca altere um prompt publicado. Crie um novo arquivo versionado e atualize `PROMPT_VERSION`.
- Não registre bytes, texto extraído, CPF, RG, nomes, tokens ou chaves. Logs devem usar somente IDs técnicos e classes de erro.
- Toda mutação de conferência deve validar o `sub` OIDC e o lease ainda ativo.
- Toda mudança no schema exige uma revisão Alembic; `create_all` é permitido somente com `TESTING=true`.
- Não exponha `object_key`, caminhos ou credenciais nos contratos da API.
- Não execute extração no processo da API; jobs pertencem ao worker ARQ.
- Produção exige KMS, OIDC habilitado e chave HMAC forte para pseudonimização da auditoria.
- Auditoria é append-only e não deve conter valores de PII antes/depois.
- Trate arquivos e texto OCR como entrada não confiável.

## Verificação obrigatória

- Execute `pytest -q` após alterar Python, persistência, prompt ou contratos da API.
- Execute `alembic upgrade head --sql` e inspecione o SQL após alterar modelos/migrações.
- Execute o dataset golden antes de promover modelo ou prompt.
- Faça uma chamada a `/health` e valide upload, deduplicação e claim da conferência antes de entregar mudanças de fluxo.
- Verifique que `static/index.html` continua sem erros de sintaxe e que os três painéis principais abrem.

## Revisão de código

- Sinalize qualquer bypass OIDC/RBAC, logging de PII, ausência de KMS, perda da restrição única do hash ou atualização de revisão sem condição atômica.
- Sinalize chamadas de LLM acopladas ao request, retries sem limite e alteração retroativa de prompt/modelo sem rastreabilidade.
- Preserve PostgreSQL como runtime; compatibilidade SQLite deve existir apenas nos testes isolados.
