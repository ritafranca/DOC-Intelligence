# ADR 0005 — SQLite local, PostgreSQL e object storage em produção

- Status: substituído pelo ADR 0006
- Data: 2026-09-01

## Contexto

A demonstração deve iniciar com pouca infraestrutura, enquanto o ambiente real precisa escalar, compartilhar arquivos e atender controles LGPD.

## Decisão

Usar SQLAlchemy assíncrono com SQLite e filesystem como defaults locais. Manter contratos compatíveis com PostgreSQL; recomendar object storage criptografado e broker durável em produção.

## Consequências

O projeto roda com um comando local. Deploy com múltiplas réplicas exige migração de blob e fila, Alembic e gestão de segredos/identidade.
