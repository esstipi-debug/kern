# Brief de pre-llamada — retailers AU/NZ (diseño)

Fecha: 2026-08-01
Estado: diseño aprobado, sin implementar
Alcance: los 35 leads de segmento `Retailer - direct client` de la lista AU/NZ

## 0. Por qué existe

El aparato de venta de Kern está construido, desplegado e instrumentado, y los
mails enviados son cero. `documentation/GTM_OUTREACH_TEMPLATES.md` ya tiene el
mail que hace falta: la **Variante A**, "the real-finding icebreaker (use when
you have a genuine number)", y la **Variante B** como fallback explícito "when
you haven't run a real diagnostic on this prospect yet".

Hoy los 66 leads de la lista están en Variante B.

Este trabajo produce una sola cosa: **el hallazgo público verificable que sube un
lead de Variante B a Variante A**. No es un reporte sobre la empresa. La
investigación cualitativa ya está hecha a mano — la lista trae `Why-fit`,
`Personalization hook` y `Contact path` por lead.

El movimiento es el que produjo el 52% de quiebre publicado de TodoFrio en el
caso chileno, sobre las tools zero-data que ya corren en producción.

### No objetivos

- Los 31 leads de segmento operador/canal (consultores SCM, contadores Xero,
  partners Shopify, COOs fraccionales). No tienen catálogo público que escanear;
  su motion es otra y merece su propio spec.
- Detector de curva de talles rota. Sería la señal más filosa para los ~8 leads
  de moda y calzado, pero es motor nuevo al servicio de 8 de 35.
- Escribir a `webapp/_leads/leads.jsonl`. Toca producción y agrega un contrato de
  datos que hay que verificar aparte.
- Cualquier registro en el registry de tools. Esto es GTM, no capacidad de
  producto; meterlo al registry contamina la superficie que se le vende al
  cliente.

## 1. Restricción que gobierna el diseño

Kern no permite escanear un dominio sin un registro de cumplimiento fechado en
`config/sites/*.yaml`. La regla dura del repo es que los fetchers **degradan,
nunca se disfrazan**.

La API real, verificada en el código:

- `src/pricing_intel/models.py:302` — `SiteConfig.is_approved` es
  `tos_decision != "prohibited" and robots_txt_respected`.
- `src/pricing_intel/acquire/base.py:186` — `require_approved_site(domain)`
  levanta `SiteNotApprovedError` **antes** de cualquier request, y sólo mira
  `config/sites/`.

Los 5 dominios chilenos tienen su record escrito a mano. Para 35 dominios AU/NZ
el trabajo real no es el brief: es el gate.

De ahí sale la columna vertebral del diseño (§2.1): los drafts se escriben a un
directorio que `require_approved_site()` no mira, así que **la aprobación humana
queda forzada por código que ya existe**, no por un flag nuevo ni por un
`input()` en medio de una corrida.

## 2. Arquitectura: tres etapas, artefactos en disco

Tres scripts chicos en `scripts/`, cada uno lee lo que dejó el anterior. El corte
entre etapa 1 y 2 es el punto de aprobación humana, y por eso es un límite de
proceso y no una rama dentro de una corrida.

```
lista de leads
     |
     v
[1] precall_gate.py   -- red: robots.txt   -> config/sites/_pending/*.yaml
     |
     |  (revisión humana: mover los aprobados a config/sites/)
     v
[2] precall_scan.py   -- red: catálogo     -> data/precall/<domain>.csv
     |
     v
[3] precall_brief.py  -- sin red           -> deliverables/precall/<slug>.md
                                            + deliverables/precall/index.csv
```

Los tres son idempotentes y reanudables: si el artefacto de un dominio ya existe,
se saltea salvo `--force`. 35 fetches con delay de cortesía son lentos y
frágiles; hay que poder retomar sin re-scrapear.

### 2.1 `scripts/precall_gate.py`

Entrada: la lista de leads (CSV). Hoy vive fuera del repo
(`kern-au-nz-target-list.csv`); la implementación la commitea a
`documentation/leads/au-nz-target-list.csv` y el script la toma por `--leads`,
con ese path como default. Es información pública de empresas, sin PII.

Filtra `Segment` que empiece con `Retailer`.

Por dominio: fetchea el `robots.txt` real y escribe un draft de `SiteConfig` a
`config/sites/_pending/<domain>.yaml`.

Reglas, sin excepciones:

- **Nunca escribe a `config/sites/`.** Ese movimiento lo hace un humano.
- `tos_summary` dice la verdad literal: generado desde `robots.txt` únicamente,
  página de ToS no leída por un humano a la fecha `<ISO>`. El campo es
  obligatorio y no vacío por `__post_init__`; llenarlo con una ficción sería
  el peor resultado posible de este script.
- `robots_txt_respected` refleja lo que devolvió el fetch, no lo que conviene.
- Si el `robots.txt` bloquea la ruta del catálogo, `tos_decision = "prohibited"`
  y ese lead queda en Variante B. **No hay override.**
- Si no bloquea, `tos_decision = "limited"` — nunca `"allowed"`, porque nadie
  leyó la página de ToS todavía.
- `rate_limit_seconds` con un default conservador; `pii_policy` siempre `"none"`
  (el modelo lo exige y no admite excepción).

Salida además: un resumen del lote para revisar de un vistazo, con el veredicto
por dominio y la línea de `robots.txt` que lo justifica.

### 2.2 `scripts/precall_scan.py`

Corre **sólo** sobre dominios presentes en `config/sites/`, vía
`require_approved_site()`. Un dominio que quedó en `_pending/` hace que la
llamada tire `SiteNotApprovedError` y ese lead se saltee. Eso es la propiedad
que hace confiable a todo el resto.

Por dominio aprobado:

1. Intenta el catálogo público de Shopify (`/products.json`, paginado).
2. Si no existe o no es Shopify, cae a JSON-LD reutilizando
   `src/pricing_intel/acquire/structured.py`, que ya sabe leer nodos
   `Product`/`Offer`. No se reimplementa extracción de structured data.
3. Respeta `rate_limit_seconds` del propio record del dominio.

Salida: `data/precall/<domain>.csv` con las columnas que el consumidor de la
etapa 3 sniffea — `sku`, `name`, `gtin`, `price` — más `available` y
`published_at`.

Un dominio que falla (timeout, 404, catálogo vacío, HTML sin structured data) se
registra con su motivo en un log del lote y **el lote sigue**. Falla de un
dominio nunca es falla de la corrida.

### 2.3 `scripts/precall_brief.py`

**Cero red.** Lee los CSV de `data/precall/`.

Por dominio:

- Corre `jobs/data_quality_job.py`, que sniffea `sku`/`name`/`gtin`/`price` de un
  maestro de producto y devuelve SKU duplicados, GTIN con dígito verificador
  GS1 inválido, nombres faltantes y costos no positivos. Motor existente, en
  producción, sin una línea nueva.
- Calcula el % de quiebre publicado contando `available`. Es aritmética sobre el
  catálogo, no un motor.

Renderiza `deliverables/precall/<slug>.md` con cuatro bloques cortos:

1. El hallazgo, con su número.
2. Cómo lo verifica él mismo en su propio sitio en dos minutos.
3. El gancho, tomado de `Personalization hook` de la lista.
4. La ruta de contacto, tomada de `Contact path`.

Nada más. Es un brief para entrar a una llamada, no un reporte.

Y `deliverables/precall/index.csv`: una fila por lead con el dominio, qué
detector disparó, el número, y la variante que corresponde.

## 3. Encuadre obligatorio del hallazgo

Va literal en la plantilla, heredado de la doctrina del caso chileno:

> El quiebre publicado es **estado de publicación, no inventario.**

Se presenta como "tu señal pública no coincide con tu operación", en forma de
pregunta. Afirmarlo como dato de stock es la objeción que mató el pitch de $390k
en Chile, y es la primera de las seis objeciones fatales que ya son doctrina.

El mismo cuidado aplica a calidad de datos: un GTIN con dígito verificador
inválido en el catálogo público es un hecho verificable sobre el catálogo
público. No es una afirmación sobre su ERP.

## 4. Umbral A/B

`index.csv` marca **Variante A** sólo si el hallazgo supera un piso fijado una
vez, igual para todos los leads, y no negociable por lead.

Abajo del piso, el lead cae a **Variante B** — que es un mail que ya existe y
funciona sin número.

Un hallazgo débil vendido como fuerte quema el dominio y el remitente. El piso
concreto se fija en el plan de implementación, sobre la distribución real que
salga del primer scan; fijarlo antes de ver datos sería inventarlo.

## 5. Manejo de errores

| Situación | Comportamiento |
|---|---|
| `robots.txt` bloquea | `tos_decision = "prohibited"`, lead a Variante B, sin override |
| Dominio sólo en `_pending/` | `SiteNotApprovedError` antes de la red; se saltea |
| Catálogo no accesible / vacío | Se registra el motivo, el lote sigue, lead a Variante B |
| Sin structured data ni `products.json` | Igual que arriba |
| Hallazgo bajo el piso | Variante B, explícito en `index.csv` |
| Artefacto ya existe | Se saltea salvo `--force` |

Regla transversal: **ningún lead se descarta en silencio.** Todo lead que no
llega a Variante A aparece en `index.csv` con el motivo.

## 6. Testing

Fixtures locales, **cero red en la suite**:

- Un `products.json` sintético de Shopify, paginado.
- Una página con JSON-LD `Product`/`Offer` y otra sin structured data.
- Un catálogo vacío.
- Un `robots.txt` que bloquea y otro que no.

El test que importa: **`scan` se niega a correr contra un dominio que sólo existe
en `config/sites/_pending/`.** Esa es la garantía de cumplimiento del diseño
entero, y es la que hay que romper primero para saber que el test sirve.

Además: idempotencia (segunda corrida no re-fetchea), y que un dominio que falla
no aborta el lote.

## 7. Criterio de éxito

No es cobertura ni % de capacidad. Es: **cuántos de los 35 leads quedan con un
número real y verificable listo para pegar en la Variante A**, y cuántos mails
salen.

Si el resultado son 35 briefs y cero mails, el trabajo falló.
