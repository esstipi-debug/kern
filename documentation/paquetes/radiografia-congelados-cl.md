# Radiografía del Congelado — dónde estás fuera de mercado y qué veda te cierra la compra

> Informe único de **5 días hábiles** · **CLP $390.000** (pago único)
> Para distribuidoras chilenas de alimentos congelados que venden a HORECA.
> **No te pedimos ningún dato.** Todo sale de información pública.

## Qué recibes

Tres piezas, cada dato con su procedencia trazable:

1. **Matriz de posición de precios** (`matriz_posicion.xlsx`) — cada producto
   de tu catálogo contra el de tus competidores online, normalizado a **precio
   por kilo** (no por envase, que es donde se esconden las comparaciones
   falsas), con un índice de posición por línea: más barato / en línea / más
   caro que la mediana del mercado. Las lecturas dudosas van en una sección
   aparte, nunca mezcladas con las confiables.
2. **Calendario de exposición a vedas** (`calendario_vedas.xlsx`) — qué
   especies de tu catálogo entran en veda y cuándo, con el número de decreto
   de SUBPESCA por fila, y la distinción entre veda **biológica** (cierra la
   captura) y **extractiva** (prohíbe además transportar, procesar,
   comercializar y **almacenar**). De ahí sale la ventana de compra
   anticipada: qué conviene tener en cámara antes de cada cierre.
3. **Reporte ejecutivo** (`informe.md`) — hallazgos priorizados, cobertura del
   análisis declarada de frente, y una sección **Fuentes** que cita, por cada
   precio, de dónde salió y cuándo se observó.

## Por qué no te pedimos datos

Tu catálogo online ya publica precio, formato y disponibilidad. Los de tus
competidores también. Con eso alcanza para la comparación, así que no hay
planilla que armar, ni reunión de levantamiento, ni acceso a tu sistema.

Es deliberado: el momento en que un diagnóstico pide "mándame tus datos de
venta" es el momento en que se muere. Este no lo pide.

## Cómo trabajamos

- **Día 1–2:** leemos tu catálogo y el de los competidores del segmento,
  normalizamos todo a precio por kilo y descartamos lo que no se puede
  comparar honestamente. Solo endpoints públicos, respetando el `robots.txt`
  de cada sitio — nunca evasión de bloqueos.
- **Día 3:** cruzamos tu catálogo contra el registro de decretos de vedas
  vigentes.
- **Día 4–5:** armamos el informe y te lo presentamos en una sesión de 45
  minutos.

## La garantía

Si el informe no encuentra al menos **3% de brecha accionable** en alguna
línea de tu catálogo, **no cobramos**. No pagas por un informe que te dice que
todo está bien.

## Lo que este informe NO hace

Preferimos decirlo antes que decepcionarte después:

- **No te dice a cuánto subir.** Para eso hacen falta tus volúmenes y tus
  costos, que acá no tenemos. El informe muestra dónde está la brecha y de qué
  tamaño es; cuánto mover cada precio es la conversación siguiente.
- **No dictamina legalidad.** Reporta **exposición** a vedas y la obligación de
  acreditar origen legal que esa exposición implica. Si cumples o no con esa
  obligación no es observable desde afuera, y no vamos a fingir que sí.
- **No es monitoreo.** Es una fotografía de una semana. Si la quieres viva,
  eso es lo que sigue.

## Qué sigue después

Dos caminos, según lo que el informe encuentre:

- **Escudo de Veda** — CLP $180.000/mes. La misma lectura corriendo todas las
  semanas: alerta cuando se abre una ventana de compra anticipada, cuando
  cambia un decreto, y cuando un competidor te pasa por debajo en una línea.
- **[Diagnóstico de Posición de Precios](diagnostico-posicion-precios.md)** —
  USD 2.000–3.500. Cuando ya quieres la conversación de "a cuánto subir": ahí
  sí entran tus volúmenes y tus costos, y el análisis pasa de posición a
  decisión de precio.

## Nota interna (no enviar al prospecto)

Esta sección es **peldaño 0** de la escalera comercial, no un competidor del
paquete 9. La diferencia estructural:

| | Radiografía (peldaño 0) | Diagnóstico de Posición de Precios (9) |
|---|---|---|
| Precio | CLP $390.000 (~USD 415) | USD 2.000–3.500 |
| Duración | 5 días | 2 semanas |
| Qué pide al cliente | **nada** | el `competitors.csv` con el mapeo SKU ↔ URL |
| Cómo homologa | matching automático sobre texto libre en español | el mapeo del cliente YA ES la coincidencia confirmada |
| Alcance extra | calendario de vedas | ledger export, sesión ejecutiva |

El costo marginal de producirla es casi cero una vez que el pipeline corre, y
su función es convertir un prospecto frío en uno que ya pagó algo — que es el
único predictor real de que vuelva a pagar. El precio está fijado para ser
compra por impulso de un dueño, no una decisión de comité.

Alcance técnico y restricciones de licencia:
`docs/superpowers/specs/2026-07-24-radiografia-congelados-chile-design.md`.
