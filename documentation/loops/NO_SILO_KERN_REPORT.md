# Nosilo KERN Loop Report

**Corrida:** 20260818-133047
**Timestamp:** 2026-08-18T13:31:16.902198+00:00
**main:** `140558f (origin/main)`
**Duracion:** 29953 ms

## Puntaje

- **8.80 / 10.0**  (completo)

- Tendencia: +1.20 vs 20260810-131353

| Componente | Peso | Obtenido | Detalle |
|---|---|---|---|
| higiene | 1.0 | 1.00 | 0 desvios estaticos |
| bateria | 6.0 | 4.80 | 60.0% (12.0/20) sobre techo 75% |
| capacidades | 3.0 | 3.00 | 5 passed, 10 warnings in 20.89s |

## Simworld (alarma de regresion, no puntua)

- `forced-L0`: captura -0.446  *(vencido 2026-09-18: hoy -0,6019)*
- `forced-L1`: captura +0.437  *(vencido 2026-09-18: hoy -0,0180)*

> **Vencido el 2026-09-18.** Estos numeros se midieron con una funcion de costo que valuaba la venta perdida a `margin` mientras `procurement` ya cobraba `unit_cost`: restaba el costo unitario DOS VECES por unidad servida, asi que servir era contablemente peor que no servir en el 42,86% del catalogo, y la captura premiaba sub-servir. Corregido en `7aa08c5` (`shortage = lost * price`). A la MISMA escala, los valores pasan a **-0,6019 (L0)** y **-0,0180 (L1)**, con el techo sirviendo 0,974 en vez de 0,565. La linea base sobre 25 mundos, con su lectura completa, esta en [`KERN_CAPTURA_BAJO_DISRUPCION.md`](KERN_CAPTURA_BAJO_DISRUPCION.md).


Config con semilla fija: {"seed": 4, "n_skus": 8, "n_suppliers": 2, "weeks": 12, "levels": [0, 1], "lanes": ["forced"]}

## Desvios

Total 0 — criticos/altos 0

Ninguno.
