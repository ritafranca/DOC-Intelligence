# ADR 0008 — Retenção automatizada, auditoria mínima e evals versionados

- Status: aceito
- Data: 2026-09-01

## Decisão

Aplicar prazo de retenção por documento, legal hold, descarte automatizado e auditoria append-only sem valores pessoais. Modelos e prompts só avançam após comparação no mesmo dataset golden, com métricas por campo e falso aceite.

## Consequências

O sistema reduz dados após a finalidade e deixa evidência operacional. A organização precisa custodiar datasets de avaliação com acesso restrito e aprovar formalmente retenção/base legal.
