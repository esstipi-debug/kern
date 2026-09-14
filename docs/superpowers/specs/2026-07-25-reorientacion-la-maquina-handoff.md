# Handoff — Reorientación: dejamos de pulir la instancia y nombramos la máquina

**Fecha:** 2026-07-25 · **Para:** la ventana siguiente, arrancando en frío.

---

## El encuadre

Dos días construyendo un clasificador de vedas de pesca chilenas y un léxico
de congelados en español. Funciona, pero solo sirve si es Chile, congelados y
WooCommerce. De las 17 tareas del plan, ~2 son universales y 15 son Chile.
**Eso es la instancia, no el producto.**

A partir de acá se construye la máquina y Chile pasa a ser su primer dataset.

---

## ⚠️ Dos correcciones al encuadre original — verificadas antes de escribir esto

El handoff dictado traía dos hechos desactualizados. Se corrigen acá para que
esta ventana no reconstruya lo que ya existe.

### 1. El "bug conocido" del canario de merluza NO existe

El encuadre reportaba en `detect_variante` (líneas ~158-159):

```python
if especie == "merluza":
    return Attribution("austral", "austral")
```

**Esa línea no está en el código.** Verificado el 2026-07-25 contra el árbol
de trabajo y contra `HEAD`; `git status` limpio; los 88 tests del módulo
pasan.

Qué pasó realmente: esa línea fue una **mutación deliberada** aplicada para
comprobar que el test muerde. Mordió — 3 rojos, incluido el test de propiedad
— y se revirtió en el acto. Lo que quedó en el transcript fue el experimento,
no el estado del repo.

La conclusión importante no cambia y de hecho se refuerza: **el test funciona
y está verificado por mutación**, en dos direcciones y sobre cuatro
implementaciones erróneas distintas. No hay nada que arreglar acá.

### 2. El normalizador de unidad SÍ existe

El encuadre decía *"es el punto único de falla y NO existe todavía"*. Se
construyó en el turno inmediatamente anterior:

| Qué | Dónde | Commit |
|---|---|---|
| Normalizador de peso neto | `src/pricing_intel/weight_format.py` | `93af233` |
| Sus tests (125) | `tests/test_weight_format.py` | `93af233` |
| Su especificación | `docs/superpowers/specs/2026-07-25-normalizador-clp-kg-spec.md` | `93af233` |
| Arreglo de `extract_pack_size` | `src/pricing_intel/normalize.py` | `c2c250a` |

Salió de una cosecha real: 10 agentes en paralelo sobre **1.279 SKUs en 10
catálogos**, que produjo 15 trampas ordenadas por daño y 25 casos de prueba
que discriminan entre implementaciones plausibles. Medido sobre
puntomarinochile.cl: **60,5% resuelto, 39,5% en cuarentena**.

Verificado por mutación: asumir 1 kg → 19 rojos · convertir volumen a masa →
2 · ventana plana sin segmentar paréntesis → 1 · promediar el rango → 2.

**Esto cambia el primer paso.** Ya no es "construirlo genérico o extraer lo
que existe": es *cuánto* generalizar lo que existe. Ver "Primer paso" abajo.

---

## La máquina — los dos componentes

Ambos independientes del dominio.

### 1. Lector de catálogo público

Lee el catálogo de cualquier pyme y lo normaliza. **Ya verificado en campo:**

- **WooCommerce Store API.** Probar las TRES rutas antes de concluir que no
  hay feed — la ruta no es uniforme y esto causó falsos negativos reales:
  - `/wp-json/wc/store/v1/products` (la más común)
  - `/wp-json/wc/store/products` (Distribuidoris, Pro Verdy, Interagro)
  - `/index.php/wp-json/wc/store/products` (permalinks planos, Pesquera Mancilla)

  `per_page=100`, paginar por `X-WP-TotalPages`. **Un 403 es un WAF, no
  ausencia de catálogo.**
- **Shopify:** `/products.json?limit=250`, paginar por `page`.
- **Jumpseller y Bsale NO exponen JSON** — hay que leer HTML.

**Estado del normalizador de unidad:** existe (ver corrección 2). Lo que falta
es decidir su grado de generalidad. Hoy su *pipeline* es universal — decodificar
entidades, plegar Unicode, enmascarar antes de extraer, clasificar por nivel de
empaque, cota de plausibilidad — y sus *léxicos* son es/en (`kilo`, `gramos`,
`kg`, `lb`). El eje de generalización es el léxico, no la lógica.

### 2. Motor de restricciones fechadas

Cruza un catálogo normalizado contra un dataset de restricciones externas con
fecha, y emite severidad por fila.

**La forma es universal** — cuotas de importación, cambios de arancel,
temporadas, vencimientos de certificación, renovaciones de licencia — y solo
el dataset es local.

Implementación existente con la parte reusable ya separada:
`src/regulatory/veda_calendar.py`. Cada atributo se devuelve como
`Attribution(value, evidence, conflicts)`, y sin evidencia literal queda sin
declarar.

Piezas que ya son genéricas y sobreviven al cambio de dominio:

- `Attribution` y sus tres estados.
- La matriz de severidad `(tipo de restricción × estado del atributo)`.
- La distinción **alcance sobre derivados**: una restricción que prohíbe
  *tener* alcanza los productos elaborados; una que prohíbe *producir* solo
  afecta el abastecimiento aguas arriba. Es exactamente la distinción
  extractiva/biológica, sin la palabra "veda".
- La cobertura territorial declarada: "cerrada en N de M territorios; para el
  resto no hay norma verificada — desconocido, **no abierto**".

---

## La disciplina — la parte más valiosa, y no es de vedas

**Tres estados por atributo, nunca dos: declarado / no declarado /
contradicho.** "No sé" jamás se rellena con el valor más probable. Cada
afirmación carga el fragmento literal del texto que la sostiene. Y la
cobertura se declara: *"82 de 96 comparables"*, nunca esconder los 14.

Salió de cuatro bugs seguidos que parecían distintos y eran el mismo: rellenar
un campo ausente con una suposición.

| # | Bug | Lo que rellenaba |
|---|---|---|
| 1 | Origen | Producto importado marcado bajo norma chilena |
| 2 | Elaborados | Una conserva tratada igual que una empanada, pese a alcances opuestos |
| 3 | Variante | "Merluza Apanada" salía como austral cuando podía ser común |
| 4 | Territorio | Se afirmaba exposición sin decir sobre qué territorio |

Y el tercer estado (`contradicho`) es el que más rinde: un dato que **falta**
se le pregunta al cliente; uno **contradicho** se le muestra — es un defecto
en su propia ficha, accionable sin preguntar nada.

**Los tests que la defienden, ambos verificados por mutación:**

- `tests/test_veda_calendar_declared_only.py` — 88 tests. El central es una
  *propiedad* sobre todo el corpus: para cada atributo declarado, su evidencia
  tiene que ser un fragmento literal del título.
- `tests/test_weight_format.py` — 125 tests. La invariante gemela: si el peso
  no se deriva con certeza de evidencia literal, cuarentena con motivo tipado.

> **Hallazgo que conviene no perder:** el test de propiedad **no** atrapa el
> arbitraje silencioso. Elegir la primera señal produce evidencia literal
> válida, así que la propiedad pasa. Evidencia inventada y arbitraje
> silencioso son dos fallas distintas y cada una necesita su propia guarda.

---

## Qué se archiva — no extender, no terminar

`config/lexicon/es_cl_congelados.yaml` y `config/regulatory/cl_vedas.yaml`
pasan a ser **el primer dataset de muchos, un ejemplo**.

- No agregar especies.
- No agregar decretos.
- No terminar el informe de las 5 distribuidoras chilenas.
- No escribir nada de outreach.

También quedan archivados, sin borrar: el one-pager comercial
`documentation/paquetes/radiografia-congelados-cl.md` y el plan de 3 PRs
`docs/superpowers/plans/2026-07-24-radiografia-congelados-chile.md`.

---

## Dónde está todo

| Qué | Dónde |
|---|---|
| Worktree | `C:\Users\Gamer\Music\scm\.wt-radiografia-cl` |
| Rama | `feat/radiografia-congelados-cl` — **el nombre ya no calza; evaluar rama nueva.** Basada en `origin/main`, 15 commits, sin PR |
| Repo principal | `C:\Users\Gamer\Music\scm\supply-chain-optimization` — en otra rama con trabajo concurrente ajeno. **No tocar.** |
| venv | Vive en el repo principal, no en el worktree: `C:\Users\Gamer\Music\scm\supply-chain-optimization\.venv\Scripts\python.exe` |

Correr tests desde el worktree:

```bash
cd "C:/Users/Gamer/Music/scm/.wt-radiografia-cl" && PYTHONPATH=. "C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe" -m pytest tests/test_veda_calendar_declared_only.py tests/test_weight_format.py -q
```

---

## Lo que no hay que reinvestigar

**Kern tiene 45 herramientas; 3 son de precio.** Ninguna ve precios "a nivel
nacional": ese índice no existe, y la API pública de MercadoLibre está
bloqueada por su propio `robots.txt`, que nombra a `ClaudeBot`.

| Herramienta | Qué necesita |
|---|---|
| `pricing` | Tu histórico de ventas (precio y cantidad por SKU) |
| `price_intelligence` | Un refs CSV + un config de sitio aprobado por dominio |
| `price_watch` | Una URL semilla de UN competidor + tu catálogo |

`price_intelligence` exige un config humano por dominio en `config/sites/`.
Hay 6 autorados y marcados `PENDIENTE DE REVISION DEL OPERADOR` — registran
solo lo verificado (robots.txt chequeado; ToS sin revisión legal, por eso
`limited` y techo L1). **Esa revisión sigue pendiente y es del operador.**

Corrida real de `price_intelligence` sobre 11 referencias: **0% de cobertura
sin configs, 29% con ellos**, y la compuerta de QA bloqueó el entregable por
estar bajo el mínimo de 60%. De 6 sitios, 4 rechazan la lectura de ficha
(Shopify dispara captcha; TodoFrío no emite JSON-LD) pero los 6 publican
catálogo por feed abierto. **Leer fichas es el camino frágil; leer feeds es el
robusto** — ese hallazgo es el que justifica el componente 1.

Otras exclusiones ya resueltas, no re-litigar: los 16 datasets de SUBPESCA en
datos.gob.cl son CC BY-NC (sin uso comercial) · `sernapesca.cl/robots.txt`
bloquea `Claude-Web` y `anthropic-ai` · Unimarc y Líder tienen bloqueo Akamai
duro · INE IPC es CC BY-SA 4.0 y el share-alike contamina derivados.

---

## Cómo quiero las respuestas

- **Capacidades:** ir al código, dar la ficha (key real, archivo, qué pide,
  qué da, dónde está el límite) **y correrla con datos reales**. Una capacidad
  descrita es una promesa; una corrida es un hecho.
- **Liderar con visual**, pocas líneas de texto alrededor.
- **Puntos de decisión con 2 a 4 opciones concretas**, nunca una pregunta
  abierta.
- **Investigación multiagente:** lanes por *clase de fuente*
  (transaccional / regulatorio / competitivo / geográfico / societario / señal
  blanda), ejes de clasificación declarados antes de la barrera, serie y foto
  declaradas por separado.

---

## Primer paso

El test ya se corrió al escribir este handoff: **88 verdes, canario ausente,
árbol limpio**. No hay bug que arreglar.

El paso real es la decisión de generalización, y hay tres formas de encararla.
Pedir las opciones antes de escribir código.

| | Enfoque | Qué implica |
|---|---|---|
| A | **Extraer el núcleo genérico de lo que existe** | Separar pipeline de léxico en `weight_format.py`, y `Attribution` + matriz de severidad de las vedas en `veda_calendar.py`. Chile queda como dataset cargable. Trabajo conocido, riesgo bajo, valida la tesis de que la máquina existe adentro. |
| B | **Escribir el segundo dataset primero** | Tomar un dominio distinto (vencimientos de certificación, cuotas de importación) y forzar la generalización desde dos instancias en vez de una. Más lento, pero una abstracción sacada de un solo caso casi siempre está mal. |
| C | **Empezar por el lector de catálogo** | Es el componente con cero código propio hoy (solo las rutas verificadas), es el que da la entrada a todo lo demás, y es genérico por construcción. |

---

## Commits de la rama, para orientarse

```
93af233  feat(peso): normalizador de peso neto para catalogos congelados CL
f6acb7d  feat(vedas): tercer estado de evidencia -- contradicho
c2c250a  fix(normalize): extract_pack_size confundia medidas y dimensiones con conteo
da3b975  test(vedas): la invariante de "nada no declarado" como test que se pone rojo
35db5ef  feat(vedas): ambito regional explicito, 15 codigos por decreto
f5bb13c  feat(lexico): variantes por especie, se evaluan todas las vedas en conjunto
d4176c1  feat(vedas): dataset curado + distincion extractiva/biologica y elaborados
f19e77f  feat(lexico): regla de origen es-CL, tres estados en vez de dos
09f7ed8  docs(paquetes): Radiografia del Congelado - peldano 0
```
