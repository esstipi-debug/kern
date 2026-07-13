# [CONCEPTO — no es una sección comercial registrada] Auditoría de Fricción Operacional

> **Estado:** borrador de venta, no forma parte de las 7 secciones oficiales
> de [MONETIZATION_BRIEF.md](../MONETIZATION_BRIEF.md) ni está registrado en
> `scm_agent/package_specs.py`. Sirve hoy como **discovery manual** (el
> operador arma el estimado con los engines existentes + los benchmarks
> citados abajo) y como abre-puertas hacia el **Proyecto de Red, Almacén y
> Operación** ([proyecto-red-almacen.md](proyecto-red-almacen.md), USD
> 8.000–18.000). Si se decide productizar de verdad, precio/alcance deben
> fijarse primero en `MONETIZATION_BRIEF.md` (fuente de verdad comercial) y
> luego reflejarse aquí — igual que las 7 secciones existentes.

## El hook

> "La mayoría de auditorías solo miden los datos que ya tienes. Nosotros
> además cuantificamos las pérdidas operacionales que tu sistema nunca
> registra — el carrito que falla, el pasillo congestionado, el recorrido
> que le quita horas a tu equipo cada semana."

## Por qué es defendible (no es un número inventado)

La cadena de costo detrás de "el desplazamiento importa" está corroborada
por fuentes independientes de ingeniería industrial (Georgia Tech Supply
Chain & Logistics Institute / Tompkins, BLS):

```
Mano de obra  ~= 45-60% del costo operativo del CD
Picking       ~= >50% de esa mano de obra
Desplazamiento~= ~50-55% del tiempo de picking
---------------------------------------------------------
=> el desplazamiento del picker es ~10-15% del costo TOTAL del CD
=> de eso, 20-40% es recuperable tipicamente via slotting/layout
   (casos documentados: 32-70% de reduccion de recorrido)
```

Proxies del lado "ánimo" (fatiga/equipo malo -> más errores, ausentismo,
rotación):

| Eslabón | Cifra de referencia |
|---|---|
| Rotación anual en almacén | ~49% (BLS 2023) |
| Costo de reemplazar 1 trabajador | USD 4.000-10.000 (hasta ~18.600 todo incluido) |
| Ausentismo | ~3,4%; USD 150-300 por no-show |
| Costo por mis-pick | USD 10-50 cada uno |
| Fatiga/ergonomía -> intención de renuncia | 73% consideró renunciar por "deuda ergonómica"; 36% faltó por dolor/agotamiento (encuesta ProGlove) |

**El caveat que protege la credibilidad (no negociable):** cada cifra de
arriba es un *benchmark de industria*, no una *medición del cliente
específico*. El entregable se presenta siempre como una **estimación con
rango y supuestos explícitos** — nunca disfrazada de medición exacta. Esto
es lo mismo que ya hace Kern en el resto del catálogo (todo número
citado + compuerta de QA), aplicado a un dominio donde la mayoría de
proveedores no se molesta en distinguir estimar de medir.

## Metodología (de dónde se toma prestado el rigor)

- **7 desperdicios Lean (Muda)** — Motion / Waiting / Transportation /
  Defects como el andamiaje de diagnóstico.
- **PMTS/MTM/MOST** (sistemas de tiempo predeterminado) y **Estándares de
  Trabajo Ingenierizados (ELS)** — el método detrás de "el desplazamiento
  debería tomar X, toma Y". Es el mismo lenguaje que usan los Labor
  Management Systems enterprise (que cuestan USD 250k-1M por instalación) —
  aquí se toma prestado el rigor, no el precio.

## Cómo se captura el dato (v1 — sin hardware nuevo)

Estas fricciones no viven en ningún CSV — viven en la cabeza del operario.
Protocolo:

1. **Notas de voz intencionadas** respondiendo prompts dirigidos: *"¿qué te
   hizo perder tiempo hoy? ¿cuántas veces? ¿cuánto tiempo cada vez? ¿a
   cuántas personas afecta?"*
2. **Mini time-study** — observar 2-3 pickers durante una hora.
3. **Estructurar** la respuesta contra un esquema de drivers de fricción
   (`tipo, frecuencia/dia, minutos_por_evento, headcount_afectado`) — mismo
   patrón que ya usa `src/voice/doc_reader.py` (no estructurado -> campos
   estructurados vía LLM), aplicado a un esquema nuevo en vez de
   documentos logísticos.
4. **Calcular:** `Σ(eventos/dia * min/evento * costo_laboral_cargado/min) * dias`,
   más el delta de recorrido vía `slotting`/`warehouse_layout` cuando hay
   datos de pedidos/layout.

Consentido e intencional -> limpio en privacidad (a diferencia de grabar
audio ambiental, que dispara las mismas leyes de escucha/GDPR que
descartamos para el ángulo Flipper).

## Instrumentación (v2 — solo si el cliente lo pide y lo financia)

Reemplazar benchmarks por constantes medidas del sitio: sensor de
movimiento en el carrito (IMU/beacon, sin PII) o cámara anonimizada/
agregada (solo trayectorias, nunca personas identificables). Esto es un
**upsell de rigor**, no un requisito — no se construye antes de que un
cliente real lo pida.

## Engines de Kern que ya cubren esto

`slotting` (COI + afinidad) · `queuing` (congestión/espera) ·
`warehouse_layout` (distancias/pasillos/andenes) · `cost_to_serve` (costo
laboral por pedido) · `capacity_planning` / `dea` (comparar eficiencia entre
zonas/turnos). Todos ya registrados y forman parte de **Proyecto de Red,
Almacén y Operación** — esta auditoría es el discovery que abre la puerta a
ese proyecto de ticket alto, con el mismo rol de embudo que el escaneo
gratis de stock muerto tiene para el Diagnóstico de Arranque.

## Fuentes (benchmarks citados arriba)

Georgia Tech SCL Institute / Tompkins (tiempo de desplazamiento del
picker) · BLS (rotación, ausentismo transporte y almacenamiento) ·
GEODIS, Lucas Systems, Optioryx (reducción de recorrido por slotting) ·
ProGlove ergonomics survey (deuda ergonómica, intención de renuncia) ·
Buske, Locus Robotics (mano de obra como % del costo operativo del CD).
