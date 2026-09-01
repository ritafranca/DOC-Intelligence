# ADR 0004 — Claim atômico com lease

- Status: aceito
- Data: 2026-09-01

## Contexto

Dois operadores podem pedir o mesmo item; locks longos também podem abandonar trabalho quando o navegador fecha.

## Decisão

Reservar o próximo documento com update condicional atômico e expiração. Heartbeats renovam o lease; o submit repete operador e validade na condição.

## Consequências

Não há edição simultânea válida. Uma sessão parada libera o item automaticamente, e saves tardios recebem HTTP 409.

