# Proyecto de Red, Almacén y Operación

> **USD 8.000 – 18.000** (pago único) · 4–8 semanas
> Para el momento de inflexión estructural: abres una nueva bodega, rediseñas
> tu red de distribución, o tu operación de planta/mostrador ya no aguanta el
> volumen actual.

## Qué recibes

Un **estudio ejecutivo consolidado** más seis análisis completos, cada uno con su
reporte y su archivo de trabajo:

1. **Ubicación óptima de la nueva instalación** — dónde abrir tu próximo centro
   de distribución, bodega o punto de venta, comparado cuantitativamente contra
   tu ubicación actual (si la tienes).
2. **Selección de modo de transporte** — parcel, LTL, FTL o intermodal, envío
   por envío, con el ahorro proyectado frente a tu operación actual y el punto
   de quiebre entre modos (a partir de qué volumen conviene cambiar).
3. **Diseño de bodega navegable en 3D** — edificio, andenes, patio, racks y
   pasillos generados a partir de tus parámetros de sitio (dimensiones,
   niveles, número de andenes) — un visor 3D que puedes recorrer, no un plano
   estático.
4. **Slotting de bodega** — qué SKU va en qué zona (A/B/C según frecuencia de
   picking) y qué productos conviene ubicar juntos, a partir de tu propio
   historial de pedidos.
5. **Dimensionamiento de personal en estaciones de servicio** — cuántas
   personas necesitas en cada punto (recepción, mostrador, call center) para el
   nivel de servicio que buscas, balanceando el costo de esperar contra el
   costo de contratar.
6. **Secuenciación de trabajos de planta/taller** — en qué orden correr los
   trabajos para minimizar atrasos o tiempo total de flujo.

Cada número del estudio es trazable a su fuente y está fundamentado en
literatura de supply chain citada en el propio documento.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática antes
de emitirse. Si un solo análisis no la pasa, el estudio completo no se entrega.

## Cómo trabajamos

- **Semanas 1–2:** recibimos tus datos, corremos los seis análisis y validamos
  contigo los supuestos (dimensiones de sitio, parámetros de servicio).
- **Semanas 3–6:** iteramos el diseño de bodega y la red contigo — este es el
  tramo donde más valor agrega la conversación, no solo el algoritmo.
- **Semana final:** presentación ejecutiva con el estudio consolidado y el
  plan de implementación.

## Qué te pedimos para arrancar

Cinco archivos (te ayudamos a extraerlos si no los tienes armados):

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `ubicaciones.csv` | Puntos de demanda (clientes/tiendas/CDs) | `x, y` (+ nombre, peso) |
| `envios.csv` | Historial de envíos | `weight_kg, distance_km` |
| `lineas_pedido.csv` | Líneas de pedido (pedido x SKU) | `order_id, product_id` |
| `estaciones.csv` | Estaciones de servicio | `station, arrival_rate, service_rate` |
| `trabajos.csv` | Trabajos a secuenciar | `job, processing_time` |

El diseño de bodega no requiere un archivo — se define directamente contigo
(dimensiones de sitio, edificio, racks, andenes) en la primera sesión.

## Qué sigue después

Este es un proyecto puntual: se cobra una vez por el estudio y la
implementación. Si además quieres que la operación resultante (inventario,
reposición, red) se mantenga y optimice mes a mes, ese es el alcance de los
planes **Growth** (USD 4.000/mes) o **Scale** (USD 7.500/mes) — y este proyecto
ya deja el diseño de red y bodega listo para operar bajo cualquiera de los dos.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
