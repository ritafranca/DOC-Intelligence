# ADR 0001 — Processamento assíncrono após ingestão

- Status: aceito
- Data: 2026-09-01

## Contexto

O provedor multimodal leva 5–40 s, falha esporadicamente e cobra por chamada. Manter a requisição aberta elevaria timeout, repetição pelo cliente e custo.

## Decisão

Persistir o documento como `PENDING`, responder imediatamente e processar por workers com concorrência, timeout, retry limitado e recuperação no startup.

## Consequências

A API passa a ser eventual e o cliente deve consultar o estado. A fila local é adequada à fatia executável; produção requer broker durável para múltiplas réplicas.

