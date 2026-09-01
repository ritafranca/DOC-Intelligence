# ADR 0003 — Strategy Pattern e versionamento explícito

- Status: aceito
- Data: 2026-09-01

## Contexto

Fornecedor, modelo, contrato e prompt mudarão; resultados precisam ser reproduzíveis e auditáveis.

## Decisão

Definir `DocumentExtractor`, selecionar a estratégia por configuração e registrar estratégia/modelo/prompt em cada tentativa e documento. Prompts publicados são imutáveis.

## Consequências

Trocas de fornecedor não contaminam a API. Toda nova estratégia precisa mapear seu retorno para `ExtractionResult` e passar pelo conjunto de avaliação.

