# ADR 2026-09 — Pivot #11: Odoo primero por partners, operador AU/NZ como rama de fracaso

**Estado:** vigente desde el 2026-09-05 · **Revisión:** semana 6 (puerta) y semana 15 (corte) · **Plan completo:** `~/.claude/plans/lee-todo-el-proyecto-enchanted-orbit.md` (fuera del repo a propósito).

## 1. Qué es esto y por qué se escribe

1. Es el pivot número 11 en seis meses y se nombra así para que no se repita sin que se note.
2. Revierte la decisión del 2026-08-05 (software self-serve, beachhead AU/NZ vía Cin7/Unleashed, imagen Docker como puerta), que ya había revertido el pivot a agencia de julio.
3. Lo motiva un diagnóstico verificado el 2026-09-05 (53 tools, 0 clientes, 0 mails, 108 prospectos sin contactar) más el norte declarado ese día: Kern es un gerente de operaciones que conecta y unifica sistemas y escribe con aprobación.
4. Carril E (semanas 1-6): Odoo primero, en español, por partners, porque es el único circuito leer → decidir → escribir con aprobación que existe en código y tiene laboratorio verde.
5. Carril A (desde la semana 7, sólo si la puerta no abre): operador AU/NZ con el marcador de servicio realizado como gancho. No se rediscute.
6. Puerta de la semana 6, con la tabla delante: ≥1 partner presentó un cliente conectable (Odoo.sh / on-prem / Custom, con Compras activo). Si sí, fase 2-E. Si no, A.
7. Frenos mecánicos en las dos ramas: `tests/test_registry_frozen.py` (53 tools y contrato de packs), un PR abierto a la vez, ≤4 h/semana de supervisión de agentes, `offers.py` intacto hasta el primer pago.
8. Congelado hasta el primer pago: MCP como canal, LATAM masivo, Kern Alerts, videos, Shopify OAuth, grafo nuevo, cualquier tool #54. Amazon FBA entra sólo por conteo (≥3 pedidos en 30 contactos).
9. Corte de la semana 15: <120 toques ⇒ el repo se congela 90 días. ≥120 toques sin pago ⇒ el motivo dominante de no-compra decide qué cambia (gancho o ICP), no el producto.

## 2. Decisión del usuario del 2026-09-05 (registrada una vez)

Objeción presentada: "mails antes que commits" (la fase 0 pedía `git log` vacío hasta 15 mails). El usuario la escuchó y decidió arrancar el código igual, más una evaluación de `nosilo_core` antes del PR-3. Orden de PRs: PR-1 (este freno) → PR-0 (circuito Odoo: aprobación en reorder points, neteo en tránsito con un solo lector, MOQ del proveedor, refresco de catálogo) → PR-2 → PR-A → PR-4 → PR-3. Los mails siguen siendo del usuario y `kern-fase0/contar.ps1` sigue siendo la vara.

## 3. Cómo se levanta el freno (y registro de cada vez que se levantó)

Sumar o quitar una tool, o un campo del contrato de packs, es un diff sobre las tuplas de `tests/test_registry_frozen.py` en el mismo PR, con el motivo en el commit y una fila acá. La tool #54 no existe hasta que un cliente pago la pida; las columnas del entregable sí pueden crecer sin tocar esto.

| Fecha | Qué cambió | Motivo | PR |
|---|---|---|---|
| 2026-09-05 | Se congela el conjunto de 53 tools y el contrato de packs | Freno mecánico #1 del plan | #330 |
| 2026-09-12 | Se suma `repricing` (57 → 58) | **Segunda excepción explícita a la regla de este documento**, decidida por el operador el 2026-09-12. Su motivo, textual: *"es motor que ya existe"* — `jobs/repricing.py` se construyó y probó entero en el PR-18 del plan 3.0 y quedó sin ningún punto de entrada; es el hallazgo #6 de la auditoría del 13-jul, todavía abierto al re-verificarlo el 12-sep. Lo que entra al registro es exposición de motor existente, no motor nuevo. El wrapper NO aplica nada (guard de AST: nunca importa `apply_repricing` ni `run_channel_repricing`), prepara contra una copia en memoria de los precios que declara el propio cliente, siempre devuelve HANDOFF, queda fuera de MCP y no toca la máquina de autonomía. **Igual que la fila anterior: no hay cliente pago que la pida**, no hay señal de mercado que la motive, y no mueve el cuello de botella de outreach del diagnóstico del 5-sep. La regla no distingue motor nuevo de exposición de motor; esa distinción la hizo el operador, a sabiendas. | #352 |
| 2026-09-15 | Se suman `shopify_replenishment` y `channel_replenishment` (58 → 60) | **Tercera excepción explícita a la regla de este documento** ("la tool #54 no existe hasta que un cliente pago la pida"), pedida por el operador el 2026-09-15 con la objeción ya planteada y escuchada. **No hay cliente pago que las pida**, no hay señal de mercado que las motive, y no mueve el cuello de botella de outreach del diagnóstico del 5-sep — igual que las dos filas anteriores. Lo que entra es el camino de inventario/reposición de CANAL: leer stock y ventas de Shopify o Mercado Libre y cruzarlos contra el ERP (Odoo o Unleashed), que es el primer job del repo que cruza dos conectores. El motor estaba construido desde agosto en #238 y llevaba 156 commits sin mergear. Dos cosas se ADAPTARON al mergear, y conviene saberlas: `demand_by_sku` vuelve a ser pública porque dos módulos la consumen, y el camino de demo de Unleashed se ELIMINÓ — su `api_key` firma cada request, así que un stand-in offline aprobaría el único paso que falla en producción; sin credenciales ahora se niega y remite a `erp='odoo'`, que sí conserva su demo. | #238 |
| 2026-09-12 | Se suman `audit_evidence`, `import_case`, `iso_ops`, `scor_iso_audit` (53 → 57) | **Excepción explícita a la regla de este documento** ("la tool #54 no existe hasta que un cliente pago la pida"). Decisión directa del operador el 2026-09-12, sin cliente pago asociado — no hay señal de mercado que la motive. El trabajo (catálogo ISO reclasificado contra lo que el motor mide de verdad, papel de trabajo MUS, y el pack de importación con el bug de convención decimal corregido) ya estaba construido, corregido y verificado antes de esta fila; la excepción es solo dejarlo entrar al registro en vez de mantenerlo en ramas verdes sin mergear. No cambia el diagnóstico del 5-sep (0 clientes, 0 mails) ni resuelve el cuello de botella de outreach que ese diagnóstico señaló. | #336, #342, #343 |

## 4. Hallazgos que esperan su puerta (aparcados con fecha)

Ver sección 8 del plan: libros → doctrina (columna "regla aplicada y por qué" tras el primer pago), Khan/Palamariu (tabla de OC en camino y conteo de proveedores por SKU tras el primer pago), adaptación a cadenas con distinto poder (perfil de posición de 5 preguntas), Amazon FBA (peldaño 2 por conteo).
