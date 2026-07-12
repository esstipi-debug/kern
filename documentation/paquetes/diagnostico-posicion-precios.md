# Diagnóstico de Posición de Precios — ¿dónde estás caro y dónde barato frente a la competencia?

> Sprint único de **2 semanas** · **USD 2.000 – 3.500** (pago único)
> Para empresas que venden productos comparables online y necesitan saber,
> con evidencia y no a ojo, dónde su precio está fuera de mercado.

## Qué recibes

Tres entregables, cada dato con su procedencia trazable:

1. **Matriz de posición de precios** (`price_position_matrix.xlsx`) — tu
   precio contra el de cada competidor confirmado, un índice de posición por
   producto (más barato / en línea / más caro que el promedio de mercado), y
   una sección aparte para las lecturas en cuarentena o descartadas — nunca
   mezcladas con las confiables.
2. **Reporte ejecutivo** (`report.md`) — hallazgos priorizados, KPIs de
   cobertura y frescura, y una sección **Fuentes** que cita, por cada precio
   de competencia, su tier de adquisición, extractor y versión, confianza y
   fecha de observación — la procedencia total es el diferenciador, no un
   detalle técnico.
3. **Export del ledger** (`ledger_export.csv`) — cada observación aceptada,
   lista para cargar en tu propia hoja de cálculo o BI.

**Garantía de calidad:** el reporte solo se emite si al menos el 60% de tus
productos tienen una lectura de competencia confirmada. Si la cobertura no
llega, no hay entregable a medias — te decimos exactamente qué referencias
faltan y por qué.

## Cómo trabajamos

Es un **modo one-shot**: vos nos das el mapeo producto ↔ competidor (tu SKU
y la URL de la ficha de producto del competidor) — ese mapeo YA ES la
coincidencia confirmada, no hace falta un proceso de matching automático
para este sprint.

- **Semana 1:** recibimos tu archivo de referencias, corremos la
  adquisición (extracción estructurada de cada página, respetando
  robots.txt y ToS de cada sitio — nunca evasión anti-bot) y la compuerta
  de sanidad de datos (precios inválidos descartados, saltos de precio sin
  confirmar puestos en cuarentena).
- **Semana 2:** sesión ejecutiva de hallazgos — dónde estás caro, dónde
  barato, y qué referencias conviene agregar para subir cobertura la
  próxima vez.

## Qué te pedimos para arrancar

Un solo archivo (`competitors.csv`):

| Columna | Contenido | Obligatoria |
|---|---|---|
| `product_id` | Tu SKU | Sí |
| `competitor_url` | URL de la ficha de producto del competidor | Sí |
| `our_price` | Tu precio actual para ese producto | Recomendada |
| `currency` | Moneda si la página no la declara | Opcional |

Un producto con varios competidores es simplemente varias filas con el
mismo `product_id`. No hace falta credenciales de ningún marketplace ni
acceso a ningún sistema del competidor — solo URLs públicas de producto.

## Escaneo gratis (lead magnet)

Si todavía no estás seguro de si vale la pena: mandanos 3–5 URLs de
competidores y te devolvemos una matriz de posición **teaser** (parcial, sin
las lecturas en cuarentena) gratis, sin compromiso.

## Qué sigue después

El diagnóstico es una foto de un momento. Si querés que el monitoreo de
precios de la competencia corra de forma continua — con alertas cuando un
competidor cambia de precio o entra en promoción — ese es el alcance del
**add-on de monitoreo continuo** (Growth/Scale), que se conecta al mismo
motor de este diagnóstico y alimenta el Control Tower.
