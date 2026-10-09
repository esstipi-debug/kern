# Kern — barrido de neteo

> Generado por `python scripts/barrido_neteo.py --markdown`. No se edita a mano: la
> tabla de abajo la congela `tests/test_barrido_neteo.py`, igual que
> `tests/test_bateria_puntaje_publicado.py` congela el puntaje de la batería.

**7/14 emisores descuentan el en-transito (46 tools no emiten cantidad a pedir)**

## Qué mide

Una sola pregunta, sobre cada tool del registro que pone un número de compra en manos
de un operador: **¿ese número descuenta lo que ya viene en camino?** Un plan que ignora
una OC ya emitida manda a comprar dos veces lo mismo, y el error no se ve en ninguna
parte hasta que llega el segundo camión.

No es la batería. La batería (`src/bateria/`) pregunta *«¿Kern puede?»* y T05 puntúa el
**mejor** de tres entregables de reposición; eso es correcto para una vara y ciego para
el producto, cuya pregunta es la contraria: *«¿cuál de los emisores **no** puede?»*. Por
eso H4 —el tool central, `inventory_optimization`, dimensionando `s` y `Q` sin mirar
jamás el estante— vivió meses con la batería en verde: nunca fue candidato de T05, y las
tres hermanas (`shopify_replenishment`, `channel_replenishment`, `drp`) tampoco. **Un
tool que nadie sondea no se puede poner rojo.**

T05 no se duplicó: ahora **consume** estas mismas sondas
(`src/bateria/barrido_neteo.py::sondas_t05`), así que «netea» está definido una sola vez
en el repo. El puntaje publicado no se movió (65,00% · 13/20).

## Cómo se mide

La disciplina es la de `tests/test_neteo_en_transito.py` (kern#251), y son dos reglas:

1. **Que un campo exista no acredita nada.** Cada sonda corre el emisor **dos veces**
   —con y sin unidades ya emitidas— y exige que el valor **se mueva**. Ninguna sonda
   mira nombres de campo. Un `on_order` agregado al dataclass y nunca cableado sale
   exactamente igual que no tenerlo.
2. **La ausencia se declara.** Un tool sin canal de entrada sale `sin canal` **con
   razón escrita**, nunca «no netea»: son cosas distintas y la diferencia decide qué hay
   que construir. Y la razón no se cree sola — si mañana aparece un campo con nombre de
   en-tránsito, la sonda pasa sola a `sin sonda`, este documento queda desactualizado y
   el test se pone rojo. Un canal nuevo no entra callado.

Cuatro veredictos, cuatro cosas distintas:

| Veredicto | Qué significa | Qué hay que hacer |
|---|---|---|
| `SI descuenta` | hay canal, la sonda lo movió y la cantidad bajó | nada |
| `NO descuenta` | hay canal, la sonda lo movió y la cantidad **no** bajó | cablearlo: la brecha está en el job |
| `sin sonda` | hay canal y nadie escribió la sonda | escribir la sonda antes de acreditar nada |
| `sin canal` | no hay por dónde entrar el en-tránsito | decidir si el tool lo necesita; la brecha es de diseño |

**Clasificar es obligatorio.** `tests/test_barrido_neteo.py` recorre
`build_default_registry()` y falla si un tool no está ni en `EMISORES` ni en
`NO_EMISORES` — el mismo patrón que `tests/test_tool_data_contract.py`. Un tool nuevo
tiene que declarar si emite un número de compra y, si lo emite, con qué sonda se prueba.

## El barrido

8/14 emisores descuentan el en-transito (46 tools no emiten cantidad a pedir)

| Tool | Emite | Canal de en-transito | Veredicto |
|---|---|---|---|
| `hat_settlement` | cantidad a pedir | - | sin canal |
| `hat_tension` | cantidad a pedir | - | sin canal |
| `multi_echelon` | nivel objetivo | - | sin canal |
| `newsvendor` | cantidad a pedir | - | sin canal |
| `simulation` | nivel objetivo | - | sin canal |
| `sop` | cantidad a pedir | - | sin canal |
| `channel_replenishment` | cantidad a pedir | open_purchase_qty_by_sku del ERP | SI descuenta |
| `ddmrp` | cantidad a pedir | columna on_order | SI descuenta |
| `drp` | cantidad a pedir | columna de entradas programadas (scheduled receipts) | SI descuenta |
| `excel_replenishment` | cantidad a pedir | columna En Transito de la planilla | SI descuenta |
| `inventory_optimization` | cantidad a pedir | columna on_order del CSV | SI descuenta |
| `launch_readiness` | veredicto de cobertura | columna on_order + ETA | SI descuenta |
| `odoo_replenishment` | cantidad a pedir | on_order_by_sku (OC abiertas del ERP) | SI descuenta |
| `shopify_replenishment` | cantidad a pedir | quantities(names: incoming) de Shopify | SI descuenta |

## Evidencia

- **`hat_settlement`** (src.hats.Candidate) -- el acuerdo se arma sobre los mismos ``HatInputs`` que la tension: la Q* que sale al HANDOFF no descuenta lo que ya viene en camino
- **`hat_tension`** (src.hats.Candidate) -- ``HatInputs`` lleva demanda, costo, lead time, terminos del proveedor y flete, y ninguna posicion: cada sombrero propone una Q sobre un estante que no ve
- **`multi_echelon`** (jobs.multi_echelon_job.EchelonLine) -- emite un NIVEL objetivo (order_up_to) por etapa, no una cantidad: el neteo contra la posicion lo hace quien ejecuta, y el entregable no trae con que hacerlo
- **`newsvendor`** (jobs.newsvendor_job.NewsvendorLine) -- la compra de una temporada se dimensiona desde la razon critica y nada mas: no hay estante ni OC abierta en el contrato, asi que lo ya comprado para la temporada se vuelve a comprar entero
- **`simulation`** (jobs.simulation_job.SkuSimResult) -- busca el nivel objetivo que minimiza el costo simulado; como todo nivel, se aplica contra la posicion al ejecutar, y esa posicion no entra aca
- **`sop`** (src.sop.PeriodPlan) -- la posicion entra por ``opening_inventory``, que es una foto del estante: el plan de suministro del periodo no descuenta lo que ya esta pedido y llega dentro del horizonte
- **`channel_replenishment`** (jobs.channel_replenishment_job.ChannelLine) -- con 2 unidades ya emitidas en las OC abiertas del ERP la cantidad a pedir baja: [4.0, 0.0, 20.0] -> [2.0, 0.0, 18.0]
- **`ddmrp`** (jobs.ddmrp_job.DdmrpPart) -- con 80 unidades ya emitidas la cantidad a pedir baja: [142.5, 142.5] -> [62.5, 62.5]
- **`drp`** (jobs.drp_job.BranchPlan) -- con 10 unidades ya emitidas en la columna de entradas programadas la cantidad a pedir baja: [30.0, 40.0] -> [20.0, 30.0]
- **`excel_replenishment`** (jobs.excel_replenishment_job.ReplenishmentLine) -- con 5 unidades ya emitidas en la columna de la planilla la cantidad a pedir baja: [58.0, 50.0] -> [53.0, 45.0]
- **`inventory_optimization`** (jobs.inventory_optimization.SkuRecommendation) -- con 80 unidades ya emitidas en la columna del CSV la cantidad a pedir baja: [349.99] -> [0.0]
- **`launch_readiness`** (jobs.launch_readiness_job.LaunchLine) -- con 400 unidades ya emitidas que llegan ANTES del lanzamiento el veredicto deja de mandar a comprar: red -> green; con las mismas unidades llegando DESPUES no acredita: red -> red
- **`odoo_replenishment`** (src.connectors.replenish.ReplenishmentLine) -- con 80 unidades ya emitidas por SKU la cantidad a pedir baja: [60.0] -> [0.0]
- **`shopify_replenishment`** (src.connectors.replenish.ReplenishmentLine) -- con 80 unidades ya emitidas en la cantidad incoming de la tienda la cantidad a pedir baja: [28.0] -> [0.0]

## Lo que el barrido no resuelve

El neteo es **una** clase de brecha. La misma maquinaria sirve para otras —«¿usa el lead
time medido o el declarado?», «¿lleva la fecha de llegada?»— pero cada sonda nueva es una
decisión sobre qué se considera insumo, y eso se diseña por clase, no en abstracto.

Y los `sin canal` no son bugs: son decisiones pendientes. Un
`newsvendor` de temporada probablemente **sí** debería netear lo ya comprado para esa
temporada; un `multi_echelon` que emite niveles probablemente **no** —pero entonces el
entregable tiene que decir contra qué posición se aplica ese nivel, y hoy no lo dice.
Están acá para que la decisión se tome, no para que se olvide.
