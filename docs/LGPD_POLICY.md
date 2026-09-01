# Política operacional LGPD — DOC Intelligence

Esta política técnica deve ser revisada pelo encarregado/DPO e jurídico da organização. Ela não substitui definição formal de finalidade e base legal.

## Princípios aplicados

- Minimização: armazenar somente arquivo, campos necessários e metadados operacionais.
- Finalidade: cada integração deve informar finalidade e política de retenção aplicável.
- Necessidade: respostas brutas do LLM e texto OCR integral não são persistidos.
- Segurança: TLS, OIDC/RBAC, KMS, redes privadas e auditoria.
- Prestação de contas: ações humanas e administrativas são atribuídas ao `sub` OIDC.

## Retenção

O default é `RETENTION_DAYS=365`. Cada documento recebe `retention_until` no ingresso. Administradores podem alterar o prazo apenas com motivo; o motivo é armazenado como HMAC, não em texto aberto.

O job diário seleciona, com `FOR UPDATE SKIP LOCKED`, até 200 documentos vencidos sem legal hold, apaga o objeto e minimiza o registro para um tombstone `PURGED`.

## Legal hold

`legal_hold=true` bloqueia descarte automático e manual. Ativação e remoção exigem role administrativa, motivo e evento de auditoria. O processo organizacional deve definir quem autoriza e quando revisar holds abertos.

## Descarte

O descarte remove:

- objeto criptografado;
- hash do conteúdo;
- nomes original e sugerido;
- tipo e campos extraídos;
- erros e claim de revisão.

Permanecem ID técnico, datas, tamanho, MIME, canal, status `PURGED` e auditoria mínima para prestação de contas.

## Auditoria

`audit_events` é append-only no PostgreSQL por trigger. Registra ator, roles, ação, nomes dos campos alterados, metadados não pessoais e IP pseudonimizado por HMAC. Não registra valores antes/depois, observação livre nem conteúdo.

Acesso à auditoria exige `document.admin`. Exportação, retenção e envio ao SIEM devem seguir política corporativa.

## Direitos do titular e incidentes

O procedimento organizacional deve mapear identificadores de atendimento para os IDs técnicos, validar identidade do solicitante e decidir acesso, correção, oposição ou eliminação conforme base legal e obrigações de conservação.

Incidentes devem preservar logs técnicos, bloquear acesso, avaliar escopo, acionar segurança/DPO e seguir os prazos regulatórios aplicáveis. Chaves e tokens potencialmente expostos devem ser rotacionados.

## Terceiros

Antes do uso produtivo do LLM e do provedor de nuvem, registrar operador/suboperador, região, transferência internacional, retenção, uso para treinamento, SLA de exclusão e controles contratuais.

