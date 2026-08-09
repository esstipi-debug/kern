# UX Masiva sin Friccion — Kern

## Objetivo
Entrada masiva sin friccion: que cualquier visitante vea valor en <30s sin registro, y que el paso a pago sea obvio.

## Cambios minimos implementados
1. `GET /api/demo-data` en `webapp/app.py`: retorna primeras 20 filas de `data/sample_demand.csv` como JSON `{rows,count,source}`. Frontend en `GET /` puede precargar demo si no hay datos subidos. Reversible: borrar endpoint.
2. Banner ROI en `webapp/paquetes_page.py` (`GET /paquetes`): div superior con input `¿Cuanto stock tenes?` -> `Kern libera 20% = $X. Cuesta $79. ROI Yx`. Usa `webapp/static/roi-calculator.js:calculateROI(inventory)`.
3. `webapp/static/roi-calculator.js`: funcion pura `calculateROI(inventory)->{freed,cost,roi}`. Incluir con `<script src="/static/roi-calculator.js"></script>` donde se necesite (ya incluido en `/paquetes` via `_ROI_BANNER_SCRIPT`).

## Flujo completo
```
Gratis (sin registro) -> email gate -> $79 -> $290
```

| Etapa | Ruta | Friccion eliminada | Metrica |
|-------|------|-------------------|---------|
| 0 Gratis sin registro | `GET /` + `GET /api/demo-data` | Demo precargado: ve portfolio con sample_portfolio sin subir nada. Cero clicks para primer "aha". | % visitantes que ven valor sin subir CSV (target >70%) |
| 1 Email gate | `GET /demo` + `POST /api/leads` | Gate solo despues de ver teaser; localStorage recuerda email, no pide dos veces. | tasa gate: visitantes -> leads (target 25-35%) |
| 2 $79 entry | `GET /paquetes` banner ROI | Calculadora instantanea personaliza el valor ("tu stock $50k -> liberas $10k, ROI 126x"). CTA Stripe/mailto ya resuelto. | CTR banner->checkout (target >8%), conversion $79 (target 3-5% de leads) |
| 3 $290 upsell | `/paquetes/{slug}` o follow-up email draft | Post-compra $79, oferta $290 (profundizacion: multi-almacen, DDMRP). Email draft ya generado en `deliverables/leads/`. | take-rate $290 sobre compradores $79 (target 20-30%) |

## Metricas de friccion eliminada
- Tiempo a primer valor: antes requeria upload; ahora 0s via `/api/demo-data`.
- Campos antes de valor: 0 (vs 1-2 tipico).
- Calculadora ROI: 1 input numerico, feedback en `input` event, sin submit.
- Reversibilidad: todos los cambios son aditivos; borrar `_ROI_BANNER_HTML/_ROI_BANNER_SCRIPT` y endpoint revierte.

## Siguiente iteracion (no implementada)
- En `webapp/static/app.js`: si `GET /api/portfolio` da 0 SKUs, fallback automatico a `GET /api/demo-data` y render con badge "Datos de ejemplo — sube tu CSV para personalizar".
- Test A/B del banner copy y posicion.
