# ADR 0002 — Deduplicação por SHA-256

- Status: aceito
- Data: 2026-09-01

## Contexto

Nomes de arquivo não são confiáveis e o mesmo conteúdo é reenviado por canais e atendentes diferentes.

## Decisão

Calcular SHA-256 durante o upload e impor unicidade no banco. Reenvios retornam o registro existente sem criar novo job.

## Consequências

Arquivos byte a byte idênticos são deduplicados de forma segura. Fotos visualmente iguais recomprimidas terão hashes diferentes; deduplicação perceptual fica fora desta fatia por risco de falso positivo.

