# documentation/legal/ — plantillas legales (E7)

> ⚠️ **Ninguno de estos documentos está listo para firmarse con un cliente
> que paga.** Son borradores redactados para reflejar con precisión cómo
> funciona Linchpin hoy (alcance comercial, garantía de QA, manejo de
> datos, *writeback*) — no asesoramiento legal. Cada uno tiene sus propias
> cláusulas marcadas `[REVISAR CON ABOGADO: ...]` que necesitan el ojo de
> un abogado real, familiarizado con tu jurisdicción, antes de usarse.
> Ver el ítem E7 en
> [09 · Checklist de Lanzamiento](../operator/09_checklist_lanzamiento.md).

| Documento | Qué cubre |
|---|---|
| [service-agreement-template.md](service-agreement-template.md) | Acuerdo de servicios con el cliente final: alcance del paquete, garantía de QA, honorarios (precio fijo y contingente), límite de responsabilidad, *writeback*, vigencia |
| [dpa-lite.md](dpa-lite.md) | Anexo de tratamiento de datos: qué se procesa, subencargados (Anthropic/Fly.io), transmisión/retención, derechos del cliente, notificación de incidentes |

Ambos documentos se mantienen consistentes con el código real del repo
(`SECURITY.md`, `src/writeback.py`, `scm_agent/llm.py`) — si alguno de esos
cambia de forma que afecte una afirmación hecha acá, actualizá el documento
correspondiente en el mismo PR.
