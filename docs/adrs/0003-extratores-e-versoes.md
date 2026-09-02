# ADR 0003 — Strategy Pattern e versionamento explícito

- Status: aceito
- Data: 2026-09-01

## Contexto

Fornecedor, modelo, contrato e prompt mudarão; resultados precisam ser reproduzíveis e auditáveis.

## Decisão

Definir `DocumentExtractor`, selecionar a estratégia por configuração e registrar estratégia/modelo/prompt em cada tentativa e documento. Prompts publicados são imutáveis.

As implementações disponíveis são `mock`, `http-vision` e `openai`. A estratégia `openai` usa cliente assíncrono, fixa a rastreabilidade no prompt `document_extraction_v2`, exige JSON e transforma falhas externas ou de parse em resultado de confiança zero para conferência. PDFs são rasterizados na primeira página fora do event loop.

## Consequências

Trocas de fornecedor não contaminam a API. Toda nova estratégia precisa mapear seu retorno para `ExtractionResult` e passar pelo conjunto de avaliação. O uso de um provedor externo com PII depende de base legal, contrato de operador, configuração de retenção e avaliação de impacto LGPD.
