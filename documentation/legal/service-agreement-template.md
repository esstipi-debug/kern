# PLANTILLA — Acuerdo de Servicios

> ## ⚠️ BORRADOR — NO USAR CON UN CLIENTE PAGANDO SIN REVISIÓN LEGAL
>
> Este documento es un **punto de partida** redactado para reflejar con
> precisión cómo funciona Linchpin hoy (alcance, garantía de QA, manejo de
> datos, *writeback*) — no es asesoramiento legal ni un contrato listo para
> firmar. Cada cláusula marcada `[REVISAR CON ABOGADO: ...]` necesita el ojo
> de un abogado real, familiarizado con la jurisdicción donde operás, antes
> de usarse con un cliente que paga. Las cláusulas sin esa marca son
> mayormente descriptivas (qué hace el producto) y tienen menos riesgo legal,
> pero igual conviene que un abogado les pase una lectura completa la primera
> vez.
>
> Ver también [dpa-lite.md](dpa-lite.md) (el anexo de datos que complementa
> este acuerdo) y el ítem E7 en
> [09 · Checklist de Lanzamiento](../operator/09_checklist_lanzamiento.md).

---

## 1 · Partes

Este Acuerdo de Servicios ("**Acuerdo**") se celebra entre:

- **[NOMBRE DEL OPERADOR / RAZÓN SOCIAL]** ("**Linchpin**" o "**el
  Proveedor**"), `[REVISAR CON ABOGADO: definir si el operador contrata a
  título personal, como monotributista/autónomo, o bajo una sociedad — cambia
  quién firma y qué responsabilidad patrimonial aplica]`.
- **[NOMBRE DEL CLIENTE]** ("**el Cliente**"), representado por
  **[NOMBRE Y CARGO DEL FIRMANTE]**.

Fecha de inicio: **[FECHA]**. Paquete contratado: **[NOMBRE DEL PAQUETE —
ver documentation/paquetes/]**.

> **Este documento es solo para venta directa** (Linchpin factura
> directamente al Cliente). **No uses este documento tal cual para un
> cliente que llegó referido por un partner, ni para un cliente de un
> partner white-label** (ver
> [partner-odoo.md](../paquetes/partner-odoo.md)) — en ambos modelos de
> partner, el Cliente le paga al partner, no a Linchpin, y en el modelo
> white-label el Cliente ni siquiera debería ver el nombre "Linchpin" en
> ningún documento. Ese escenario necesita su propio acuerdo (partner ↔
> Linchpin, y partner ↔ su cliente final) — hoy no existe una plantilla
> para eso (ver la nota en
> [09 · Checklist de Lanzamiento](../operator/09_checklist_lanzamiento.md)).

## 2 · Objeto y alcance del servicio

El Proveedor entrega al Cliente los análisis y entregables correspondientes
al paquete **[NOMBRE DEL PAQUETE]**, según el alcance publicado en su
one-pager comercial (`documentation/paquetes/[archivo].md`), incluyendo:

- Las herramientas específicas incluidas en el paquete (ver la tabla
  "Recibís" del one-pager correspondiente).
- La cadencia acordada (única, mensual, quincenal, etc. — ver el one-pager).
- Un documento ejecutivo consolidado más el entregable completo de cada
  herramienta individual del paquete (reporte + planilla de trabajo).

**Fuera de alcance**, salvo acuerdo escrito adicional: cualquier
herramienta o análisis no listado en el paquete contratado; asesoramiento
legal, aduanero, impositivo o regulatorio (ver la Sección 6); ejecución de
decisiones comerciales (negociar con proveedores, aprobar compras, fijar
precios) — el Proveedor entrega el análisis y la recomendación, **el
Cliente decide y ejecuta**.

`[REVISAR CON ABOGADO: confirmar que esta sección referencia correctamente
el paquete específico contratado antes de cada firma — copiar/pegar el
alcance exacto del one-pager en vez de solo linkearlo, para que el contrato
sea autocontenido]`

## 3 · Garantía de calidad (QA)

Cada análisis entregado pasa una compuerta de control de calidad (QA)
automática antes de emitirse. Si un solo análisis del paquete no la pasa,
el paquete completo no se entrega — el Proveedor nunca entrega números a
medias ni resultados parcialmente validados.

Esta garantía es sobre el **proceso** de validación (consistencia interna,
rangos plausibles, trazabilidad de cada cifra a su fuente), no una garantía
de resultado de negocio — ver la Sección 5 (Límite de responsabilidad).

## 4 · Honorarios y facturación

**Si el paquete es de precio fijo** (todos excepto Sprint de Liquidación):
el Cliente paga **[MONTO — ver el one-pager]** según la cadencia del
paquete (`[único / mensual / quincenal]`). `[REVISAR CON ABOGADO: definir
método de pago, moneda, tratamiento de mora, y si corresponde un anticipo
antes de empezar el sprint]`.

**Si el paquete es Sprint de Liquidación** (precio contingente): el Cliente
recibe una **estimación** de honorarios al arrancar el sprint — es una
proyección, no una factura. El honorario final se factura sobre el
**recupero real** de cash, nunca sobre la proyección inicial, a una tasa de
**[10–20]%** acordada por adelantado (piso de USD 1.500, aplicado solo
cuando efectivamente se recupera cash — nunca por debajo de la tasa
calculada). **Si el sprint no recupera nada de cash, el honorario es cero
— sin excepción, sin piso.** Esto no es negociable por contrato individual:
es una regla dura de la calculadora del motor (`src/contingent_fee.py`) y
coincide exactamente con la promesa pública del one-pager de venta
(`documentation/paquetes/sprint-liquidacion.md`: "si no se recupera nada,
no se cobra nada") — este Acuerdo nunca debe redactarse de forma que la
contradiga. Si se acuerda un anticipo opcional antes de empezar el sprint,
ese anticipo se acredita contra el honorario final calculado, no es un
cargo aparte (ver [07 · Setup de Venta](../operator/07_setup_venta.md)).

`[REVISAR CON ABOGADO: cláusula de mora, moneda de facturación si el
Cliente opera en un país distinto, y tratamiento impositivo (IVA/retenciones
según jurisdicción)]`

## 5 · Límite de responsabilidad

`[REVISAR CON ABOGADO — esta es la cláusula de mayor riesgo del documento]`

Los entregables del Proveedor son **recomendaciones analíticas** basadas en
los datos que el Cliente suministra. El Proveedor no garantiza un resultado
de negocio específico (ahorro, recupero de cash, nivel de servicio
alcanzado) — la garantía de la Sección 3 cubre el proceso de validación, no
el resultado comercial de seguir (o no seguir) la recomendación.

El Cliente es responsable de: (a) la exactitud de los datos que suministra
(el Proveedor no audita ni corrige datos fuente más allá de lo que la
auditoría de calidad de datos del paquete, si está incluida, detecta
explícitamente); (b) toda decisión comercial tomada a partir del análisis
(negociar, comprar, liquidar, fijar precio); (c) validar cualquier
recomendación contra su propio criterio de negocio antes de ejecutarla.

`[REVISAR CON ABOGADO: definir un tope de responsabilidad — la práctica
habitual en contratos de consultoría es limitar la responsabilidad total del
Proveedor a los honorarios efectivamente cobrados en los últimos
[N] meses/el ciclo en curso, excluyendo dolo o negligencia grave. Sin este
tope, la exposición del Proveedor es indefinida]`

## 6 · Fuera del alcance: asesoramiento legal, aduanero e impositivo

Linchpin señala explícitamente cuándo una decisión requiere asesoramiento
legal, aduanero o impositivo especializado (ver el enrutamiento a
`legal / agente aduanal licenciado` en `src/escalation.py`), pero **no
brinda ese asesoramiento**. Cualquier recomendación relacionada con
clasificación arancelaria, cumplimiento normativo, contratos con terceros o
implicancias impositivas debe ser validada por un profesional matriculado
antes de actuar sobre ella.

## 7 · Datos del Cliente y confidencialidad

El tratamiento de los datos que el Cliente suministra (qué se procesa, con
qué finalidad, dónde se envía, cuánto se retiene) está descrito en detalle
en el anexo [dpa-lite.md](dpa-lite.md), que forma parte integral de este
Acuerdo.

Ambas partes se comprometen a mantener confidencial la información
comercial no pública compartida en el marco de este Acuerdo (precios,
datos operativos, estrategia). `[REVISAR CON ABOGADO: definir plazo de la
obligación de confidencialidad — ¿sobrevive la terminación del contrato?
¿Por cuánto tiempo?]`

## 8 · Propiedad intelectual

El motor de análisis de Linchpin (el código fuente del repositorio) se
distribuye bajo licencia MIT (ver [LICENSE](../../LICENSE)) y no se
relicencia ni se transfiere por este Acuerdo. Los **entregables producidos
específicamente para el Cliente** (los reportes, planillas y
recomendaciones de su análisis) son propiedad del Cliente una vez
entregados y pagados en su totalidad.

`[REVISAR CON ABOGADO: confirmar que esta distinción entre "motor" (MIT,
no transferible) y "entregable" (propiedad del Cliente) es la intención
comercial correcta, y agregar una cláusula de licencia de uso si el
Proveedor quisiera retener algún derecho sobre los entregables — p. ej.
usarlos de forma anonimizada como caso de estudio]`

## 9 · Escritura sobre sistemas del Cliente (*writeback*)

Si el paquete contratado incluye *writeback* hacia el sistema del Cliente
(por ejemplo, reposición Odoo — ver `src/connectors/odoo.py`), el motor
soporta este mecanismo de seguridad:

- Ningún cambio se aplica directamente. Todo cambio propuesto se prepara
  primero como un *changeset* de solo lectura (dry-run).
- Los cambios se clasifican por nivel de riesgo: **de solo lectura**,
  **reversibles** (se pueden deshacer, p. ej. modificar un punto de
  reorden) e **irreversibles** (no se pueden deshacer de forma segura).
- Un cambio clasificado como **irreversible** siempre requiere aprobación
  explícita del Cliente antes de aplicarse — es una regla dura del motor
  (`src/writeback.py`), no una opción que se pueda desactivar.
- Todo cambio aplicado queda auditado y es reversible cuando es
  técnicamente posible (ver `src/writeback.py`).

`[REVISAR CON ABOGADO / OPERADOR — LEER ANTES DE PROMETERLE ESTO A UN
CLIENTE: el conector de Odoo (`src/connectors/odoo.py`), tal como está
hoy, clasifica TODOS sus cambios como "reversibles" — incluida la creación
de una orden de compra — y **por defecto los aplica automáticamente sin
pedir aprobación** (`auto_apply_reversible=True` es el valor por defecto
de `apply_restock` y `apply_draft_purchase_orders`). Es decir: la garantía
de "el Cliente revisa antes de que exista cualquier efecto real" NO es el
comportamiento por defecto de la integración Odoo tal como está desplegada
hoy — para que lo sea, el operador tiene que pasar
`auto_apply_reversible=False` explícitamente y confirmar en una corrida de
prueba que efectivamente pide aprobación antes de aplicar. No prometas
esta garantía en una firma sin haber verificado esa configuración primero.
Definir además quién es responsable si un cambio aprobado por el Cliente
resulta perjudicial una vez aplicado.]`

## 10 · Vigencia y terminación

`[REVISAR CON ABOGADO: definir plazo inicial, renovación automática o no,
preaviso de terminación (sugerido: 30 días para paquetes mensuales), y
qué pasa con un ciclo ya facturado/en curso al momento de la terminación]`

## 11 · Ley aplicable y jurisdicción

`[REVISAR CON ABOGADO: completar según la jurisdicción real del Proveedor
y, si corresponde, negociar con el Cliente. No dejar en blanco al firmar]`

## 12 · Firmas

| | Nombre | Cargo | Fecha | Firma |
|---|---|---|---|---|
| **Proveedor** | | | | |
| **Cliente** | | | | |
