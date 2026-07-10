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
      los 7 paquetes, precios y CTAs se vean bien

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
- [ ] Rutina operativa: tras cada demo corrido, revisar
      `deliverables/leads/<email>/` (o `/data/leads/` en Fly) — ahí queda el
      mini-reporte y el **borrador** de email de seguimiento; enviarlo a mano
      (Linchpin nunca manda correo automáticamente)

## Pendiente segun épicas futuras (se completa cuando esa épica aterrice)

- [ ] E3 — nada de login humano identificado aún (Sprint de Liquidación corre
      con los mismos CSVs del Diagnóstico)
- [ ] E4/E5 — sin acciones humanas esperadas (i18n y citation-gate son
      puramente de código)
- [ ] E6 — si se firma un partner Odoo real: acordar rev-share/tarifa fija y
      cargar su `branding` en `client_profile`
- [ ] E7 — hacer revisar el `service-agreement-template.md` y `dpa-lite.md`
      por un abogado real antes de usarlos con un cliente pagando (marcados
      `[REVISAR CON ABOGADO]` en el propio documento)
- [ ] E8 — ninguna, es solo tooling interno

## Regla permanente (se codifica formalmente en HANDOFF.md cuando E8 aterrice)

Cuando exista `PIPELINE.md` en la raíz del repo con un deal activo, ese trabajo
tiene prioridad sobre cualquier ítem de este checklist o cualquier épica de
Linchpin 2.0.
