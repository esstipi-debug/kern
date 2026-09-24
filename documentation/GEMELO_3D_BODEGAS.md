# Servicio de Gemelo Digital 3D para Bodegas y Centros de Distribución

> Documento de trabajo — Peak Qual · Septiembre 2026
> Estado: idea en etapa beta. Precios y plazos son estimaciones a validar con los primeros clientes.

---

## 1. Resumen

Servicio que escanea y modela en 3D bodegas, centros de distribución **y sus patios**, para medir el espacio real, detectar espacio perdido y evaluar si conviene automatizar (robots, racks, rediseño de layout).

**Posicionamiento:** *"Antes de invertir en robots o en más metros cuadrados, mide lo que ya tienes."*

El valor no está en el escaneo (que es cada vez más un commodity), sino en el **análisis**: cruzar el modelo 3D con los datos de operación (WMS, SKUs, rotación) y traducirlo en decisiones y en dinero.

---

## 2. Problema que resuelve

- El suelo para bodegas es cada vez más caro y los patios se ocupan "a ojo".
- Las empresas quieren automatizar, pero no saben si su bodega está preparada ni si la inversión se paga.
- Muchas automatizaciones fracasan o quedan subutilizadas por falta de un estudio previo serio.
- Los integradores de robótica hacen levantamientos parciales y orientados a vender su propio equipo.

---

## 3. Cliente objetivo

| Segmento | Por qué le sirve | Prioridad |
|---|---|---|
| CD medianos y grandes (retail, e-commerce, consumo masivo) | Presión por espacio y automatización | Alta |
| Operadores 3PL | Venden espacio: cada m² recuperado es ingreso | Alta |
| Empresas con patios de almacenaje (contenedores, áridos, madera, maquinaria) | Patios subutilizados, inventario a granel sin medir | Alta |
| Dueños y corredoras de bodegas industriales | Modelo exacto para arrendar, vender o tasar | Media |
| Pymes con bodegas pequeñas | Pagan poco y rara vez automatizan | Baja |

**Geografía:** el mercado grande está en la Región Metropolitana (Pudahuel, Lo Aguirre, Quilicura, San Bernardo). Punta Arenas sirve para el beta y clientes locales (Zona Franca, puerto, industria).

---

## 4. Escalera de productos

El diagnóstico es la puerta de entrada; cada peldaño vende más al mismo cliente.

### 4.1 Diagnóstico de espacio (producto de entrada)
- Gemelo 3D de bodega + patio.
- Capacidad real de almacenaje por zona, alturas mal aprovechadas, espacio perdido.
- Recomendaciones rápidas y ahorro estimado en m² y en dinero.

### 4.2 Estudio de factibilidad de automatización
- Ancho de pasillos, estado del piso, pendientes, alturas libres, obstáculos.
- Qué tipo de robot calza (AMR, shelf-to-person, tote-to-person, etc.) y cuántos.
- Qué hay que modificar antes (piso, electricidad, red, racks).
- Caso de negocio: inversión vs. ahorro, plazo de retorno.

### 4.3 Rediseño de layout y slotting
- Cruce del gemelo con datos del WMS (SKUs, rotación, dimensiones).
- Propuesta de nuevo layout y ubicación de productos.
- Apoyo en la implementación de los cambios. (Conecta con **Kern**.)

### 4.4 Representante del cliente en la implementación
- Supervisar al integrador cuando el cliente compra robots: plazos, especificaciones, pruebas de aceptación.
- Pocos lo ofrecen; se apoya en experiencia real de implementación de WMS.

### 4.5 Servicio recurrente
- Re-escaneo periódico para comparar cambios.
- Volumetría mensual de pilas en patios (inventario a granel).
- Inspección de racks y pisos comparando escaneos en el tiempo.

### 4.6 Plataforma de software (fase posterior)
- Visor web del gemelo con los análisis, por suscripción.
- Stack propio: Node.js + React + PostgreSQL/Prisma.
- Se construye **solo** cuando los peldaños anteriores muestren qué quieren ver los clientes.

---

## 5. Capacidades

### 5.1 Captura de datos

| Método | Uso | Nivel |
|---|---|---|
| Planos CAD/PDF + export del WMS | Análisis sin escanear; arranque del beta | Beta, costo $0 |
| Fotogrametría con celular (Polycam, RealityScan) | Aprender el pipeline, modelos aproximados | Beta |
| iPhone Pro con LiDAR | Interiores, escala de pasillos y racks | Beta |
| Dron con fotogrametría | Patios completos, techos, terreno | Beta (contratado) |
| Escáner LiDAR de mano (propio o comercial) | Interiores con precisión de centímetros | Profesional |
| Dron LiDAR de interiores (tipo Elios 3) | Naves muy altas, partes superiores de racks | Solo arriendo puntual |

### 5.2 Procesamiento y análisis
- Nube de puntos → plano 2D con racks, pasillos, columnas y alturas (Open3D, Python).
- Fotos de dron → modelo 3D y ortomosaico (WebODM, open source).
- Unión de interior + patio en un solo modelo usando puntos de control comunes.
- Cálculo de capacidad (pallets, contenedores, m³) por zona.
- Simulación de circulación de robots (Isaac Sim u otra herramienta).
- Cruce con datos de inventario para slotting.

### 5.3 Entregables tipo
- Modelo 3D navegable.
- Plano 2D acotado.
- Informe con: capacidad actual vs. potencial, espacio perdido, recomendaciones priorizadas, ahorro estimado.
- (Si aplica) informe de factibilidad de automatización con caso de negocio.

### 5.4 Ventaja propia
- 11+ años en operaciones de supply chain y certificación CSCP.
- Experiencia en implementación de WMS en un CD de 50.000+ SKUs.
- Capacidad de construir el software propio (Claude Code, stack propio).
- Herramienta de optimización de inventario (Kern) complementaria.

---

## 6. Limitaciones

### 6.1 Técnicas
- **El dron no sirve para interiores:** sin GPS, con racks, vigas y poca luz, la fotogrametría falla y el vuelo es riesgoso.
- **El LiDAR del iPhone tiene alcance corto** y menos precisión que un escáner profesional; sirve para beta, no para mediciones críticas.
- **El gemelo sirve para planificar, no para operar robots:** cada robot arma su propio mapa al instalarse.
- **Los datos sintéticos (simulación) son demasiado limpios:** los escaneos reales traen ruido, zonas tapadas, reflejos del film y racks a medio llenar. Siempre validar con escaneos reales.
- **Construir un dron de interiores propio** es un proyecto de ingeniería de meses; no es la ruta del beta.
- **Viento en Magallanes:** limita los días de vuelo de dron para patios.

### 6.2 Operacionales
- Escanear una bodega en operación exige coordinar horarios, grúas horquilla y seguridad del personal.
- El modelo envejece: las bodegas cambian y el gemelo requiere re-escaneos.
- Los planos y datos del WMS son confidenciales → firmar NDA con cada cliente.

### 6.3 Regulatorias
- Volar dron en Chile requiere registro y permisos de la DGAC según peso y zona.
- Para operar comercialmente hay que formalizarse (o subcontratar pilotos con credencial vigente).

### 6.4 Comerciales
- Los integradores de robótica ya regalan una versión simple del levantamiento como parte de su preventa: el diagnóstico debe ser claramente superior.
- **Neutralidad:** si se cobra comisión a un fabricante, el diagnóstico deja de ser neutral. Definir el modelo y ser transparente con el cliente.
- Distancia: el mercado grande está en Santiago y la base en Punta Arenas.
- Las pymes con bodegas pequeñas rara vez compran automatización.

---

## 7. Precios estimados

> Estimaciones propias, no tarifas publicadas. UF ≈ $40.000. Referencia de mercado encontrada: servicios de dron para viñedos cobran entre 1 y 2 UF por hectárea.

| Producto | Precio estimado | Modelo de cobro |
|---|---|---|
| Diagnóstico de espacio | 20–80 UF | Fijo por bodega |
| Factibilidad de automatización | 80–250 UF | Fijo, o financiado por integrador |
| Rediseño de layout y slotting | 100–400 UF | Por proyecto |
| Representante en implementación | 40–100 UF/mes | Mensual, 3 a 6 meses |
| Re-escaneo y volumetría | 10–30 UF/mes | Suscripción por sitio |
| Plataforma de software | 3–15 UF/mes | Suscripción por sitio |

**Reglas de precio:**
1. Cobrar por valor (m² recuperados, arriendo evitado), no por horas.
2. Primeros clientes con descuento, no gratis; a cambio, testimonio y permiso para usar el caso.
3. Con integradores, acordar el pago antes del primer referido (tarifa fija por estudio o comisión por venta cerrada).
4. Nunca cobrar el diagnóstico por debajo del costo del piloto de dron + tiempo propio.

---

## 8. Hardware y costos

> Solo equipos nuevos. Precios de septiembre de 2026, confirmar antes de comprar.

### 8.1 Ruta recomendada: no comprar nada al inicio
- Beta con planos + export WMS: **$0**.
- Captura de patio: **contratar piloto de dron** por trabajo.
- Captura de interior: celular propio o servicio de escaneo contratado.

### 8.2 Nivel beta (si se decide comprar)

| Equipo | Precio | Uso |
|---|---|---|
| iPhone 18 Pro 256 GB | $1.549.990 | LiDAR interior + apps de escaneo |
| DJI Mini 5 Pro | $1.449.990 | Fotogrametría de patios (verificar apps de mapeo compatibles) |
| Software (Polycam, WebODM, Open3D) | Gratis o bajo costo | Procesamiento |

### 8.3 Escáner LiDAR de mano propio (cuando haya clientes)

| Componente | Precio aprox. |
|---|---|
| LiDAR Livox Mid-360 | US$730–815 |
| NVIDIA Jetson Orin | US$250–900 |
| Montaje, baterías, cables | US$300–500 |
| **Total** | **~US$1.500–2.000** |

Software open source: FAST-LIO2 para SLAM. Compite con escáneres comerciales de ~£10.500.

### 8.4 Nivel profesional (referencia)

| Equipo | Precio |
|---|---|
| DJI Matrice 4E (mapeo) | desde ~$6.050.000 |
| XGRIDS Lixel K1 (escáner de mano) | ~£10.499 (sin distribuidor en Chile) |
| Flyability Elios 3 (dron de interiores) | ~US$70.000 · arriendo ~US$2.000/mes |

---

## 9. Alianzas

### 9.1 Captura (para no invertir en equipo)
- **Pilotos de dron y empresas de topografía** (Punta Arenas y Santiago): ellos capturan, Peak Qual analiza. Reparto de ingreso.
  - Pedir: fotos crudas, vuelo en grilla con alto traslape, puntos de control en terreno, credencial DGAC vigente.
- **Empresas con escáneres LiDAR de minería:** equipos caros con tiempo ocioso; se les abre el mercado logístico.
- **Universidad de Magallanes u otras:** equipos, estudiantes en práctica, proyectos de investigación.

### 9.2 Canal de venta (robots y racks)

| Aliado | Por qué | Ángulo |
|---|---|---|
| **Mindugar + Geekplus** | Alianza nueva (mayo 2026) de racks chilenos + robots líderes globales | Necesitan estudios de sitio en cada venta |
| **STG + Sistemo** | Integradores establecidos de robotización de almacenes | Externalizar sus levantamientos |
| Fabricantes sin representante fuerte en Chile (Hai Robotics, Locus, Hikvision Robotics, OTTO, ForwardX) | Necesitan socios locales para preventa | Contactar gerentes LATAM por LinkedIn |

**Cómo presentarse:** llegar con el beta terminado (bodega real modelada + informe), no con la idea.
**Mensaje:** *"Te entrego clientes pre-calificados con el estudio de sitio listo; tú cierras la venta más rápido."*
**Preferir:** alianzas con varios fabricantes para mantener la neutralidad.

---

## 10. Roadmap

| Fase | Plazo | Objetivo | Criterio de salida |
|---|---|---|---|
| 0. Beta | 1–3 meses | 1–2 bodegas modeladas gratis (planos + WMS + dron contratado) | Plano y reporte en X horas por bodega, error de medidas < Y cm |
| 1. Venta de diagnósticos | 3–6 meses | 3–5 clientes pagados (Punta Arenas + Santiago) | Precio validado, 2 testimonios |
| 2. Alianzas | 6–12 meses | Acuerdos con 1–2 integradores/fabricantes | Primer referido pagado |
| 3. Recurrencia y software | 12+ meses | Suscripciones de re-escaneo + visor web | Ingreso mensual recurrente |

---

## 11. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| Nadie paga por el diagnóstico | Cobrar algo desde el primer cliente; medir disposición a pagar temprano |
| Integradores regalan el levantamiento | Diferenciarse con análisis de datos + caso de negocio + neutralidad |
| Inversión en hardware sin clientes | Subcontratar captura hasta tener demanda probada |
| Beta que se estira indefinidamente | Criterio de salida definido desde el inicio |
| Pérdida de credibilidad por comisiones | Transparencia y alianzas con varios fabricantes |
| Mercado lejos (Santiago) | Socio local o viajes agrupados por varias visitas |

---

## 12. Métricas a seguir

- Horas por bodega (captura + procesamiento + informe).
- Precisión de medidas vs. medición manual.
- m² recuperados identificados por diagnóstico.
- Tasa de conversión diagnóstico → factibilidad → proyecto.
- Referidos pagados por aliados.
- Ingreso recurrente mensual.

---

## 13. Próximos pasos

- [ ] Conseguir una bodega (Punta Arenas) con plano + export de inventario para el beta.
- [ ] Contactar 1–2 pilotos de dron con credencial DGAC.
- [ ] Armar el pipeline: plano/nube de puntos → análisis → informe.
- [ ] Definir el criterio de salida del beta (horas y precisión).
- [ ] Preparar el caso del beta como material de presentación.
- [ ] Contactar a Mindugar/Geekplus y STG/Sistemo con el caso terminado.

---

## Fuentes de referencia

- Alianza Geekplus–Mindugar (mayo 2026): https://inmobiliare.com/geekplus-mindugar-automatizacion-naves-industriales-latam/
- Alianza STG–Sistemo: https://www.semanariolocal.cl/?p=13590
- Precios servicio dron (1–2 UF/ha): https://www.mch.cl/codelco-mega-y-moller-perez-cotapos-el-variado-grupo-de-empresas-que-usa-drones-en-sus-negocios/
- Flyability Elios 3 con LiDAR: https://dronedj.com/2023/10/25/flyability-elios-indoor-drone-lidar/
- Livox Mid-360: https://www.ebay.com/itm/156900478912

---

## Anexo A — Qué parte ya existe en Kern (verificado contra el código, 2026-09-24)

El documento cuenta a Kern como ventaja (5.4) y como conexión del peldaño 4.3. Este
anexo dice hasta dónde llega esa conexión **hoy**, con el archivo que lo respalda, para
no vender como hecho lo que el motor no hace.

**Nombre que confunde.** En Kern, la herramienta `digital_twin` (`src/digital_twin.py`)
es un simulador de red multi-escalón (proveedor → CD → tienda), no un modelo espacial.
El gemelo de este servicio corresponde a `warehouse/` ("spatial twin") y a la herramienta
`warehouse_layout`. Igual con `simulation`: simula una política de inventario, no la
circulación de robots (eso sigue siendo Isaac Sim u otra, fuera de Kern).

### A.1 Cobertura por peldaño

| Peldaño | Qué ya hace Kern | Qué no hace |
|---|---|---|
| 4.1 Diagnóstico de espacio | `slotting` calcula utilización = cubo almacenado / volumen útil cuando recibe `warehouse_volume` (`jobs/slotting_job.py`). `warehouse_layout` arma un modelo 3D con capacidad por slot (`warehouse/generator.py`). | No lee nubes de puntos, planos CAD/PDF ni ortomosaicos. La utilización es un solo número para toda la bodega, no por zona. Ver A.2 antes de usarla. |
| 4.2 Factibilidad de automatización | `queuing` dimensiona estaciones (p. ej. puestos goods-to-person) si se conocen tasas de llegada y de servicio. | Nada evalúa si un robot calza (ancho de pasillo, piso, altura libre) ni calcula inversión vs. ahorro o plazo de retorno. Hoy el caso de negocio se arma fuera de Kern. |
| 4.3 Layout y slotting | `slotting`: zona A/B/C por Cube-per-Order Index (Kallina & Lynn 1976) más co-ubicación por *lift* de afinidad, desde líneas de pedido (`order_id`, `product_id`; volumen opcional). `abc_xyz` clasifica por valor y variabilidad. `warehouse_layout` entrega un visor 3D navegable. | El slotting asigna **zona**, no ubicación física: el paso "1b" del roadmap de `warehouse/README.md` (poner SKUs reales en los slots del layout) no está construido. El generador solo produce racks uniformes (mismo largo, orientación y ancho de pasillo): no representa una bodega real con zonas mixtas. |
| 4.4 Representante en implementación | `earned_value` consolida control de plazo y costo por paquete de trabajo: sirve para seguir al integrador. | La supervisión es servicio humano. `acceptance_sampling` es muestreo de recepción de lotes, no pruebas de aceptación de un sistema robótico. |
| 4.5 Servicio recurrente | — | No hay comparación entre escaneos ni volumetría de pilas. |
| 4.6 Plataforma de software | `warehouse/html_export.py` ya genera un visor 3D autocontenido (Three.js, sin build) y la webapp lo sirve en `GET /warehouse` y `GET /api/warehouse` (FastAPI). | Ver A.3. |

### A.2 Advertencia: la utilización del slotting con volumen unitario

`jobs/slotting_job.py` toma la columna de volumen (`unit_volume`, `volume`,
`required_space`, `cube`…) y la usa **tal cual** como espacio requerido del SKU; no la
multiplica por las unidades almacenadas (`src/space.py` tiene `required_space()` para eso,
pero el job no la llama). Si se le pasa el volumen **unitario** de un export de WMS, la
utilización sale absurdamente baja. Probado: tres SKU de 0,02 / 0,05 / 0,10 m³ contra una
bodega de 1.000 m³ dan **0,017 %** de utilización. Mientras no se corrija, la columna debe
traer el cubo total almacenado por SKU (volumen unitario × unidades en stock), o el número
no se muestra al cliente.

### A.3 Decisiones que este documento toca y el repo ya tomó

- **Stack del visor (4.6).** El documento propone Node.js + React + PostgreSQL/Prisma.
  Kern es Python/FastAPI y ya tiene un visor 3D. No es una objeción; es un dato para
  cuando se decida: dos stacks son dos mantenciones.
- **Superposición con un paquete existente.**
  [`paquetes/proyecto-red-almacen.md`](paquetes/proyecto-red-almacen.md) ya vende "Diseño
  de bodega navegable en 3D" y "Slotting de bodega" dentro de un proyecto de USD
  8.000–18.000. El peldaño 4.3 (100–400 UF) se superpone en alcance: conviene decidir si el
  gemelo escaneado es un insumo de ese paquete o un producto aparte antes de cotizar ambos
  al mismo cliente.
- **Son complementarios, no redundantes.** El 3D de ese paquete se genera "a partir de tus
  parámetros de sitio": es paramétrico, no medido. El escaneo aporta justo lo que le falta
  (dimensiones, niveles, módulos, bahías y anchos de pasillo reales).

### A.4 El puente más corto hacia la ruta beta

La ruta beta de 8.1 (planos + export WMS, $0) calza con Kern casi sin código nuevo: las
medidas del plano entran como parámetros de `warehouse_layout`, y el export del WMS entra
a `slotting` con el cubo total por SKU. Lo que falta, en orden de dependencia:

1. Corregir la semántica de volumen del slotting (A.2).
2. Capacidad y utilización **por zona**, no solo total (lo pide 4.1).
3. Poner los SKUs en los slots del layout (roadmap 1b de `warehouse/`).
4. Un adaptador nube de puntos / plano → parámetros medidos (Open3D, 5.2), para que el
   modelo deje de ser una grilla uniforme.
