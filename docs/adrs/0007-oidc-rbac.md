# ADR 0007 — OIDC individual e RBAC no servidor

- Status: aceito
- Data: 2026-09-01

## Decisão

Usar Authorization Code com PKCE na SPA e validar assinatura, issuer, audience e expiração do access token na API. O ator é o claim `sub`; roles controlam envio, leitura, conferência e administração.

## Consequências

Chaves compartilhadas deixam de identificar operadores. O IdP precisa mapear grupos para roles e manter JWKS disponível. Controles visuais nunca substituem autorização server-side.

