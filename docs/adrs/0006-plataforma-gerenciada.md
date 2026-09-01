# ADR 0006 — PostgreSQL, S3/KMS e Redis gerenciados

- Status: aceito
- Data: 2026-09-01

## Decisão

Substituir SQLite, filesystem e fila em processo por PostgreSQL via asyncpg, object storage S3 compatível com KMS e Redis persistente com ARQ. API e workers são deploys independentes. Um outbox transacional evita perder jobs quando a publicação falha após o commit.

## Consequências

O runtime passa a exigir infraestrutura externa e migração Alembic. Em troca, suporta múltiplas réplicas, armazenamento compartilhado, recuperação de fila e escalabilidade horizontal.

