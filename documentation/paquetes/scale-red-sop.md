# Scale — Red, S&OP y Mando Ejecutivo

> **USD 3.200 / mes** (flat, sin variable — certidumbre de presupuesto) · ciclo quincenal + S&OP mensual
> Para empresas mid-market con red real (2+ plantas o centros de distribución) que
> ya superaron el alcance de un solo almacén y necesitan gobernar la cadena
> completa, no solo el inventario.

## Qué recibes

**El catálogo completo de Kern** — todo lo del plan Growth (pronóstico, ABC-XYZ,
política de inventario, reposición conectada a tu ERP, pricing, costo de servir,
exactitud de inventario, red de distribución, proveedores, riesgo) **más 9
capacidades de mando ejecutivo**:

- **Ciclo S&OP mensual** — el ritual que define este plan: proyección de demanda
  vs. oferta bajo 3 estrategias (chase/level/hybrid), con el gap de costo,
  servicio y capital de trabajo cuantificado para forzar una decisión ejecutiva
  en la junta mensual.
- **Ubicación de nuevas instalaciones** — dónde abrir el próximo centro de
  distribución o punto de venta, con el ahorro cuantificado frente a tu
  ubicación actual.
- **Selección de modo de transporte** — parcel/LTL/FTL/intermodal, envío por
  envío, con el ahorro frente al modo actual y el punto de quiebre entre modos.
- **Diseño de bodega en 3D** — un layout navegable (edificio, andenes, racks,
  pasillos) generado a partir de tus parámetros de sitio.
- **Slotting de bodega** — qué SKU va en qué zona (A/B/C) y qué productos
  conviene ubicar juntos, según tu propio historial de picking.
- **Dimensionamiento de personal en estaciones de servicio** — cuántas personas
  necesitas en mostrador, recepción o call center para el nivel de servicio que
  quieres, con el costo de espera vs. el costo de personal balanceado.
- **Secuenciación de planta/taller** — en qué orden correr los trabajos para
  minimizar atrasos o tiempo de flujo.
- **Control de proyectos por valor ganado (EVM)** — qué proyectos activos están
  atrasados o sobre presupuesto, con el peor caso identificado primero.
- **Diagnóstico de liderazgo del equipo (modelo CHAIN)** — perfil, arquetipo y la
  palanca prioritaria de desarrollo, trimestral.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática. Si uno
solo de los análisis ejecutados falla, el paquete completo no se emite ese ciclo.

## Qué te pedimos

El núcleo es el mismo que Growth (ventas, maestro, planilla u Odoo, stock,
finanzas, pedidos). Las 9 capacidades nuevas se activan enviando su archivo
cuando corresponda — ninguna es obligatoria si esa parte de tu operación no
aplica todavía:

| Archivo | Activa | Cuándo lo mandas |
|---|---|---|
| `ubicaciones.csv` | Ubicación de instalaciones | Al evaluar un nuevo sitio |
| `envios.csv` | Selección de transporte | Si gestionas fletes propios |
| (parámetros de sitio) | Diseño de bodega 3D | Al rediseñar una bodega — se define contigo, no es un archivo |
| `lineas_pedido.csv` | Slotting | Si operas bodega propia |
| `estaciones.csv` | Dimensionamiento de personal | Si tienes mostrador/recepción/call center |
| `trabajos.csv` | Secuenciación de planta | Si programas taller/producción |
| `valor_ganado.csv` | Control de proyectos (EVM) | Si tienes proyectos activos |
| `liderazgo.csv` | Diagnóstico de liderazgo | Trimestral — lo relevamos nosotros con tu equipo |

El ciclo S&OP mensual reutiliza tu `ventas.csv` — no pide un archivo adicional.

## Cómo se ve el ciclo

1. Ciclo quincenal: el núcleo operativo (inventario, reposición, pricing, costo de
   servir) se revisa cada 2 semanas.
2. Una vez al mes: el ciclo S&OP completo — convocamos a demanda, oferta y
   finanzas y forzamos la decisión sobre el plan.
3. Las capacidades situacionales (red, bodega, personal, proyectos) se activan
   el ciclo que corresponda, cuando mandas su archivo.
4. Cada cambio a un sistema de registro (planilla, Odoo) queda staged, reversible
   y auditado — nada se aplica sin tu aprobación.

## El camino típico

Este plan es para clientes que vienen operando en **Growth** y necesitan
gobernar una red real, no solo un almacén. El salto natural desde acá, cuando
la relación madura (6–18 meses), es el **Retainer Ejecutivo Fraccional**
(USD 4.500/mes) — mismo alcance analítico, cadencia semanal y
escalamiento con SLA para un mandato de VP/COO fraccional. No es una opción
de entrada: se ofrece como upgrade a un cliente Scale existente.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
