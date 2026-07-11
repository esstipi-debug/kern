# 09 · Checklist de Lanzamiento — Linchpin 2.0

> Todo lo que queda para que la superficie de venta cobre de verdad. Cada ítem
> es una acción de **login humano** que ningún agente puede hacer por vos
> (crear cuenta, generar un link de pago, publicar en un directorio). Se va
> completando a medida que cada épica de Linchpin 2.0 (E1-E8) aterriza — este
> archivo se actualiza en el mismo PR que agrega los ítems, no después.

## Venta (E1 — superficie de venta, ver [07 · Setup de Venta](07_setup_venta.md))

- [ ] Crear cuenta Calendly y configurar `CALENDLY_URL` (`fly secrets set`)
- [ ] Crear al menos 1 Stripe Payment Link (`STRIPE_LINK_DIAGNOSTICO_ARRANQUE`
      o `STRIPE_LINK_STARTER_FUNDAMENTOS` primero — son los puntos de entrada
      más comunes)
- [ ] Configurar `OPERATOR_NAME`, `OPERATOR_BIO`, `OPERATOR_EMAIL`
- [ ] Configurar `OPERATOR_LINKEDIN`
- [ ] Subir una foto a `webapp/static/operator/` (PR) y configurar
      `OPERATOR_PHOTO_URL=/static/operator/<archivo>`
- [ ] Visitar `https://linchpin.fly.dev/paquetes` en producción y revisar que
      los 8 paquetes, precios y CTAs se vean bien

## Distribución (ya preparado, pendiente de tu login — ver
[GTM_SUBMISSIONS.md](../GTM_SUBMISSIONS.md))

- [ ] Registrar en el MCP registry oficial (`mcp-publisher login github` +
      `publish`)
- [ ] Reclamar/publicar en Glama
- [ ] Reclamar/publicar en Smithery
- [ ] Reclamar/publicar en PulseMCP
- [ ] Publicar el módulo Odoo en el Apps Store (ver
      `odoo_addon/linchpin_dry_run/`)

## Funnel demo (E2 — mini-reporte, ya en código)

- [ ] En Fly: `fly secrets set LINCHPIN_LEAD_REPORTS_DIR=/data/leads` para que
      los mini-reportes de leads sobrevivan a los redeploys (sin esto viven en
      el filesystem efímero del contenedor)
- [ ] En Fly: `fly secrets set LINCHPIN_RATE_LIMIT=<N>` — `/api/demo-scan` es
      público sin autenticación (como `/api/leads`, es el propio imán de
      leads) y el rate limit viene **apagado por defecto**; sin esto, cualquiera
      puede scriptear un email nuevo por request. Hay un tope duro adicional
      (`MAX_LEAD_DIRS` en `webapp/app.py`) como defensa en profundidad, pero no
      reemplaza configurar el rate limit real
- [ ] Rutina operativa: tras cada demo corrido, revisar
      `deliverables/leads/<email>/` (o `/data/leads/` en Fly) — ahí queda el
      mini-reporte y el **borrador** de email de seguimiento; enviarlo a mano
      (Linchpin nunca manda correo automáticamente)

## Sprint de Liquidación (E3, ya en código)

- [ ] Nada obligatorio — el paquete `liquidacion` corre con los mismos CSVs
      del Diagnóstico. Opcional: configurar `STRIPE_LINK_SPRINT_LIQUIDACION`
      si querés cobrar una seña por adelantado (ver la nota de precio
      contingente en [07 · Setup de Venta](07_setup_venta.md#2--stripe-payment-links-uno-por-paquete))
- [ ] Rutina operativa: al cerrar cada sprint, correr
      `--measure <ventas_post_liquidacion.csv>` y revisar el anexo de cierre
      antes de facturar (el honorario real, nunca la estimación inicial)

## Modo Partner / White-Label (E6, ya en código)

- [ ] Nada obligatorio hasta que se firme un partner real (integrador Odoo o
      consultora — ver [partner-odoo.md](../paquetes/partner-odoo.md) para el
      pitch y los dos modelos). El módulo Odoo (`odoo_addon/linchpin_dry_run/`)
      ya tiene su sección "For partners" enlazando al one-pager.
- [ ] Al firmar un partner: acordar por escrito el modelo (rev-share 20% o
      white-label a tarifa fija mensual, con su monto) y quién factura a quién
      — no hay plantilla de contrato de partner todavía (a diferencia del
      `service-agreement-template.md` de cliente final en E7); redactarlo a
      mano la primera vez.
- [ ] Cargar su `branding` en `client_profile` (nombre, `logo_url`, y
      `primary_color` en formato `#RRGGBB` — ver el snippet de ejemplo en
      [partner-odoo.md](../paquetes/partner-odoo.md#cómo-se-aplica-tu-marca-branding)).
      Sin esto, sus decks salen con la marca Linchpin por defecto — no rompe
      nada, pero no es lo pactado.
- [ ] Rutina operativa: la primera corrida de un partner nuevo, revisar el
      deck generado (`.md` y `.xlsx` del paquete consolidado) antes de que se
      lo pase a su cliente — confirmar que el logo (si configuró uno) resuelve
      bien y que el pie dice su marca, no "Linchpin".

## Pendiente segun épicas futuras (se completa cuando esa épica aterrice)

- [ ] E4/E5 — sin acciones humanas esperadas (i18n y citation-gate son
      puramente de código)
- [ ] E7 — hacer revisar el `service-agreement-template.md` y `dpa-lite.md`
      por un abogado real antes de usarlos con un cliente pagando (marcados
      `[REVISAR CON ABOGADO]` en el propio documento)
- [ ] E8 — ninguna, es solo tooling interno

## Regla permanente (se codifica formalmente en HANDOFF.md cuando E8 aterrice)

Cuando exista `PIPELINE.md` en la raíz del repo con un deal activo, ese trabajo
tiene prioridad sobre cualquier ítem de este checklist o cualquier épica de
Linchpin 2.0.
