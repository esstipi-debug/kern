# ESPEC — Normalizador precio/kilo, catálogos congelados CL

**Invariante (no negociable).** `peso_neto_kg(titulo, contexto) -> Decimal | None`. Si el peso no se deriva con certeza de **evidencia literal**, devuelve `None` y el SKU va a **CUARENTENA** con un `motivo` tipado. Nunca 1 kg por defecto. Nunca promediar rangos. Nunca convertir volumen. Gemela de la invariante de vedas: *ningún atributo se afirma sin evidencia literal en el título*.

**Salidas posibles** (tres, no dos):
| Salida | Significado |
|---|---|
| `Decimal` | kg netos del SKU vendible, exactos |
| `PRICE_BASIS_PER_KG` | el catálogo publica precio **por kilo**; `precio_por_kg = precio`, `peso_pack = None` (evidencia literal requerida: "kilo"/"kg"/"Kgr" desnudo, "(precio por kg)", o nota "Valor publicado por kilo") |
| `None` + motivo | CUARENTENA |

---

## 0. Pre-proceso obligatorio (antes de cualquier regex)

| # | Paso | Por qué (caso literal) |
|---|---|---|
| P0.1 | Decodificar entidades HTML **exactamente una vez**. Si tras decodificar aún queda `&#`, → CUARENTENA `ENTIDAD_RESIDUAL` | `"Demi &#8211; Baguette"` inyecta el número 8211 |
| P0.2 | NFKC + mapear todo Unicode `Zs` (U+00A0, **U+202F**, U+2009) a espacio ASCII; colapsar runs | `"Azúcar Blanca 10 Kg"` usa U+202F: `/\d+ ?kg/` devuelve `[]` |
| P0.3 | Normalizar `–` (U+2013) y `—` (U+2014) a `-` **después** de P0.1, marcando que eran dash largo | `"Reineta Porciones – 1 kg"` vs `"Chorito Granel 200 – 300 Seco"` |
| P0.4 | Enmascarar `$\d[\d.]*` (moneda) y `\d+\s*%` | `"Gift Card $100.000"`, `"CARNE MOLIDA 4% MATERIA GRASA"` |
| P0.5 | **Regla del run alfabético**: un número solo forma token de medida si el run alfabético máximo que lo sigue (≤2 espacios, punto final opcional) es **igual completo** a una entrada del léxico. Nunca `substring`. | `"QUESO T10 LLIFEN COLUN 10 KG APP"`: run = `LLIFEN` ≠ `L` → no es 10 litros. Mata también `oz`⊂`Gyozas`, `cc`⊂`CARPACCIO`, `lb`⊂`Albacora` |

**Léxicos** (comparación case-insensitive, punto final opcional):
```
KGU = kg, kgs, kgr, kilo, kilos, k g   (incluye KG, Kg, Kilos, KILO, Kgr, "kg.", "kilos.")
GU  = g, gr, grs, gramos                (incluye G, GR, Gr, Grs, GRS, "Gr.", "grs.")
LBU = lb, lbs
VOL = l, lt, lts, litro, litros, ml, cc, oz     -> RECHAZO
CNT = u, un, unid, unidad, unidades, uds, hojas, mangas, bolsas, barras,
      tarros, bidones, cajas, planchas, rolls, pack, piezas, empanaditas   -> RECHAZO
LEN = mm, cm, m, mts, metros                                               -> RECHAZO
CONT_SKU  = bolsa, saco, balde, bidón, frasco, pieza, barra, bandeja, plancha, sachet
CONT_OUTER= caja, cajas, manga, mangas, pallet, master
VENENO    = up, app, aprox, aprox., app., variable, calibre, grado, meses, trim
```

---

## 1. TABLA DE FORMATOS RECONOCIDOS

Todas las filas son instancias de un solo token: `(?<![\wÁÉÍÓÚÑ.,/-])(?P<n>\d{1,4}(?:[.,]\d{1,3})?)[ ]{0,2}(?P<u>[A-Za-z]{1,8})\.?(?![A-Za-z])` + clasificación de `u` por léxico. Las regex por fila son la forma concreta que debe testearse.

| # | Regex | Captura | Unidad | → kg | Ejemplos literales de la cosecha |
|---|---|---|---|---|---|
| A1 | `(?<![\w.,])(\d{1,3})[ ]{0,2}(KGU)\.?(?![A-Za-z])` | entero + kilo | kg | `×1` | `Ostión Sin Coral 1KG` · `Papas Prefritas Bolsa 1 Kg` · `Pure Lutosa 1Kg` · `Camarón grande 36/40 1 Kilo` · `Langostino entero u15 2kg.` |
| A2 | `(?<![\w.,])(\d{1,3}),(\d{1,3})[ ]{0,2}(KGU)\.?` | **coma decimal** (1–3 dec) | kg | `×1` | `Salmón Ahumado Laminado 1,130 Kg` (=1,13) · `Queso Crema Nacional 1,36kg` · `Papas Prefritas Bolsa 2,5kg` · `SALMÓN ... PIEZA 1,2KG UNIDAD` |
| A3 | `(?<![\w.,])(\d{1,3})\.(\d{1,2})[ ]{0,2}(KGU)\.?` | **punto decimal** (1–2 dec) | kg | `×1` | `Bolsa De 1.05 Kg` · `Papa Crinkle Farm Frites Bolsa De 2.5 Kg` · `QUESO MANTECOSO BARRA QUILLAYES 3.2 KG APP`\* · `Queso crema barra de 1.36 kilos` · `Merluza Varita Apanada*0.5 kilos` |
| A4 | `(?<![\w.,])(\d{1,4})[ ]{0,2}(GU)\.?(?![A-Za-z])` | entero + gramo | g | `÷1000` | `Camarón 100/200 Cocido Pelado 500 G` · `Choclo Grano 500 GR` · `Ketchup Clasico By Maria 1000 Gr.` · `Salmón Ahumado ... Pimienta 113g` · `Tártaro de Salmón 250 Grs.` · `Mantequilla Portion Colun 10 gr` |
| A5 | `(?<![\w.,/-])(\d{1,2})[ ]{0,2}(LBU)(?![A-Za-z-])` | libra | lb | `×0.45359237` | `CAMARÓN ECUATORIANO APANADO PRECOCIDO 1LB REAL` (único uso legítimo; ver R-A5) |
| A6 | `(\d{1,2})[ ]{0,2}[xX×*][ ]{0,2}(TOKEN_MASA)` | **multiplicador** (operando derecho **con** unidad de masa) | pack | `N × M` | `LASAGNA BOLOGNESA SADIA CAJA 6X600G` = 3,6 · `Papas Prefritas 13x13mm 4X2,5KG` = 10,0 · `Nugget de Pollo Crocante Sadia Caja 2x3Kg` = 6,0 · `Langostino Austral ... CAJA 6x2KG` = 12,0 · `Volcán Chocolate 2 unid*90gr.` = 0,18 |
| A7 | `(TOKEN_MASA)[ ]{0,2}[xX×][ ]{0,2}(\d{1,2})(?![A-Za-z0-9])` | multiplicador **postfijo** | pack | `M × N` | `Queso Provoleta Santa Rosa Bandeja 130 gr X 2` = 0,26 |
| A8 | `(\d{1,2})[ ]?(unidades?\|un)\s+de\s+(TOKEN_MASA)\s*c/u` | pack con marcador `C/U` | pack | `N × M` | `Queso Mini Brie ... Pack 5 Unidades De 25 gr C/U` = 0,125 |
| B1 | `(?<![A-Za-z0-9])(KGU)(?![A-Za-z])` **sin** número en todo el título | unidad desnuda | — | `PRICE_BASIS_PER_KG` | `Esparrago kilo` · `Reineta Filete  KILO` · `Trucha Trozos al Natural kg` · `Espárrago Kgr` · `Camarón 36/40 Crudo Pelado desvenado  Imp.kilo` (¡frontera izquierda = `.`, no `\b`!) |
| B2 | `\(\s*precio por kg\s*\)` | basis explícito | — | `PRICE_BASIS_PER_KG` | `Lomo Superior de Atún Aleta Azul Fresco (precio por kg)` |
| B3 | descripción contiene `Valor publicado por kilo` | basis declarado por el vendedor | — | `PRICE_BASIS_PER_KG` **y** `peso_pack = None` | 81 de 96 SKUs de rapa-nui.cl |

\* A3 marca además `APP` → veneno (fila R-V1); el token parsea pero el SKU va a cuarentena por peso variable.

**Separador decimal — regla cerrada** (los 10 catálogos mezclan las dos convenciones, a veces dentro del mismo feed):

| Patrón | Lectura | Justificación literal |
|---|---|---|
| `,` + 1–3 dígitos | **decimal**, siempre | cero casos de coma-miles en masa en 1279 SKUs; `1,130 Kg` = 1,13 |
| `.` + 1–2 dígitos | **decimal** | `2.5 kilos`, `1.05 Kg`, `9.5 kg`, `3.05 KG` |
| `.` + exactamente 3 dígitos | **CUARENTENA** `SEPARADOR_AMBIGUO` | `1.000cc`, `900-1.000g` usan punto de miles; no existe ningún `1.130 kg` legítimo → el rechazo no cuesta ningún SKU y bloquea el error de 1000× |
| dos separadores en un número, o espacio dentro del número | CUARENTENA | `"Volumen: 1. 000 cc"` (delmaratuhogar) |

**Cota de plausibilidad**: resultado válido solo si `0.005 ≤ kg ≤ 25`. Fuera → CUARENTENA `FUERA_DE_RANGO` (mínimo real: `Mantequilla Portion Colun 10 gr`; máximo real: `Queso Cheddar Block Santa Rosa 19,7 kg`).

---

## 2. TABLA DE RECHAZOS EXPLÍCITOS

| Clase | Patrón que NUNCA parsea a kg | Por qué | Ejemplos literales |
|---|---|---|---|
| **Volumen** | `\d+\s*(l\|lt\|lts\|litro\|litros\|ml\|cc)\b` | No hay conversión honesta a masa: densidad desconocida y, en conserva, el peso drenado no se publica | `Locos al natural 1lt` · `LOCOS COCIDOS CONSERVA FRASCO 1.000CC UNIDAD` · `Frasco Loco Conserva 1000cc.` · `Locos Cocidos – Frasco 700 cc` · `Salsa soya Kikkoman 18,9 L` · `Aceite Vegetal Genérico 900 Cc` · `Helados Bravissimo 900 ml.` |
| **Onza** | `\d+([.,]\d+)?\s*oz` | Todas las ocurrencias son **fluidas**, probado por `Bowl craft 500 cc 50 un (12oz)` | `Degustador 1.0 oz 100 un` · `Degustador 1,5 Oz` · `Degustador 2 Oz` |
| **Conteo** | número + `CNT` | Piezas ≠ masa; el catálogo usa 6+ grafías (`U/u/UN/un/unid/Unidades`) | `Empanada Champiñon Queso 10 U` · `48 empanaditas de queso` · `Machas media concha (20 unid)` · `Alga Nori Blue Bolsa 100 Hojas` · `Camarones Torpedo Apanados (10u)` · `Bandeja 247 (3 Rolls)` |
| **Longitud** | número + `LEN` | Corte, diámetro o metraje de insumo | `Kani Kama 18 cm 500 G` (la masa es `500 G`) · `Toalla Jumbo 250 mts 2 UN` · `Film Plasrico 300 M` · `Masas Empanadas 19 cm` · `Pinza de cocina de bambú 17,8cm` |
| **Dimensión de corte** | `\d+\s*[xX×*]\s*\d+` con operando derecho **sin** unidad de masa (o con `mm`) | `N×M` de corte, no multiplicador de pack. La misma `x` que en A6 → discrimina por la unidad del operando derecho | `Papas prefritas 11X11 1kg` · `Papa 12X12 Mm Farm Frites Bolsa De 2 Kg` · `Papas Prefritas 6X6 Bolsa De 2.5 Kg` · `Papas Pre-fritas finas 7*7  2.5  kilos` · `Papas Fritas 10mm Star Frites Bolsa 2 Kg` · `Salmón Atlántico Porción Sin Piel 1&#215;5 ... Caja 5 kg` |
| **Calibre camarón / marisco** | `\d{1,3}\s*[/\-–]\s*\d{1,3}` **no** seguido de unidad de masa | Piezas por libra (o por kilo en chorito). Nunca peso, nunca fracción | `Camarón 36/40 Asiatico Crudo Pelado 1 Kg` · `Camarón Ecuatoriano 100-200 1kg` · `Chorito Granel 200 – 300 Seco` · `Camarón 31/35` · `OSTIÓN CARNE SIN CORAL 30/50 IQF` · `Gambas Colas Cocidas Peladas 40/100 IQF 500g` |
| **Calibre alfanumérico / grado** | `\b[uU]-?\d{1,2}\b`, `\bL[12]\b`, `\d+\s*-?UP\b`, `\d+LB-UP`, `Calibre [A-Z0-9]`, `Grado \d`, `\d+ Meses` | Grado de talla, no formato | `Camarón U-15 sin Cáscara Crudo IQF 1Kg.` · `Langostino entero u15 2kg.` · `Langostino Austral Entero L1 Crudo IQF` · `Pulpo del Norte 1UP` · `Pulpo ... 2-UP Caja 12Kg.` · `Salmón ... TRIM-D 2LB-UP CAJA 10Kg` · `Ostión con Coral Calibre B de 1kg` · `Arroz Grado 2 Camsa 1 Kg` · `Queso Parmesano Lituano 36 Meses Pieza 4 kg` |
| **Packaging / no-alimento** | SKU cuya categoría o nombre es envase, utensilio, servicio o gift card | Contamina cualquier métrica de kg vendidos | `Vaso Domo 235 ml` · `Bowl Craft 1300 cc` · `Bandeja Aluminio (c-10LX)` · `Freidora de Aire Airflow 3.5 Lts FDV` · `Gift Card $50.000` · `Bolsa térmica South Wind` · `Esterilla Redonda Makisu` · `Arriendo frio congelado` · `Producto pruebas` |
| **Número desnudo** | `\d+` sin run de unidad válido | Sin unidad no hay medida | `Churrasco Especial 40 (caja)` · `Churrasco Posta procesado 100 (caja)` · `BANDEJA 242` · `SELECCIONE CANTIDAD: 48` |
| **Barra no numérica** | `/` entre palabras | Alias, no calibre. Prohibido `split('/')` | `Pangasius Filete s/piel s/espinas IQF 1kg` · `Papa Carita/Smile Bembrazil` · `Supremita/Filetito Pollo rebozado 3 Kg.` · `Pulpa Maracuya s/pepa 1 kilo` |

---

## 3. ORDEN DE PRECEDENCIA (algoritmo, no consejo)

```
P0  Pre-proceso (sección 0). Si ENTIDAD_RESIDUAL -> CUARENTENA.

P1  BASIS. Si el SKU tiene evidencia B1/B2/B3 -> devolver PRICE_BASIS_PER_KG.
    El peso del pack NO se usa ni se infiere. (rapa-nui: el "1kg" del titulo
    es la UNIDAD DE PRECIO, no el envase — 81/96 SKUs.)

P2  MASCARADO, en este orden estricto. Mascarar = reemplazar el span por un
    placeholder no numerico, de modo que el resto del pipeline sea
    independiente del orden de aparicion en el titulo:
      P2.1 calibre con barra      \d{1,3}/\d{1,3}
      P2.2 calibre alfanumerico   U-?\d+ | L[12] | Calibre X | Grado N | N Meses
      P2.3 dimension de corte     \d+[xX×*]\d+  con operando derecho SIN masa
      P2.4 rango con guion        \d{1,3}\s*-\s*\d{1,3}  NO seguido de masa
    (P2.1 antes que P2.4 resuelve "Camarón ... 36/40 – 1 Kg": el 40 se consume
     primero, el "- 1 Kg" ya no tiene digito a la izquierda.)

P3  VENENOS. Si tras el mascarado el titulo conserva alguno, -> CUARENTENA:
      V1 marcador de peso variable: aprox | aprox. | app | App. | APP | "peso variable"
      V2 piso abierto: \d+\s*(kg|lb)?\s*-?UP
      V3 rango de masa: N-M / "N a M" / "N o M" seguido de unidad de masa,
         Y sin ningun token determinado en P4 (si hay uno, el rango solo se ignora)
      V4 dos o mas variantes en un titulo: "(500g / 1kg)", "(30g – 50g)"
      V5 >=2 niveles de empaque distintos de CONT_OUTER, o la palabra "pallet"
      V6 case-pack no declarado por politica de fuente (ver P7)

P4  EXTRACCION. Tokens de masa determinados (filas A1-A8), con la cota
    0.005..25 kg. Cada token se etiqueta por nivel:
      T1  no adyacente a CONT_OUTER  (ventana +-2 tokens, ambos lados)
      T2  adyacente a CONT_OUTER     ("caja x 12 kilos", "10 Kg Por Manga")
      T3  rango / por-pieza          (nunca produce respuesta)

P5  GANADOR = el unico elemento del nivel mas alto no vacio (T1 > T2 > T3).
      - T1 con >=2 elementos -> CUARENTENA MASAS_EN_CONFLICTO
      - T1 vacio, T2 con 1 elemento -> ese (la caja es el SKU vendible)
      - solo T3 -> CUARENTENA RANGO_INDETERMINADO

P6  CONTRASTE ESTRUCTURADO. Si el feed trae weight/grams no vacio y !=0 y
    difiere >2% del resultado -> CUARENTENA CONTRADICCION_FUENTE.
    weight vacio o grams==0 = ausencia de evidencia, NUNCA cero kg
    (mariscosdelaconcha: grams=0 en 209/212 variantes).

P7  POLITICA DE FUENTE (declarada, con evidencia citada; default = UNKNOWN):
      case_pack_informativo: "(N Unidades Por Caja)" no altera el formato del SKU
      Sin declaracion -> V6 -> CUARENTENA. Aplica a ~110 titulos de alimentika.cl.

P8  Devolver Decimal, o None + motivo.
```

**Por qué T1 gana a T2 y no al revés.** `"Langostino entero u15 2kg. (caja x 12 kilos)"`: `u15` muere en P2.2; `2kg.` es T1; `12 kilos` es T2 por adyacencia a `caja` → gana **2 kg**. La misma regla, sin excepción, resuelve `"ATÚN STEAK 170-220G IVP CAJA 5KG"`: T1 vacío, T3 = el rango por pieza, T2 = `5KG` → gana **5 kg**; y su gemelo `"ATÚN STEAK 170-220G IVP UNIDAD"` queda solo con T3 → **CUARENTENA**. El mismo fragmento literal, dos SKUs que difieren ~25×, resueltos sin heurística posicional.

---

## 4. CASOS QUE VAN A CUARENTENA AUNQUE PAREZCAN PARSEABLES

| Caso | Título literal | Motivo |
|---|---|---|
| Peso variable nominal | `Salmón Premium – 1,7 kg aprox.` | El body dice "Variable entre 1,5 y 2,0 kg"; **ningún** SKU pesa 1,7. Y su hermano `Salmón Exportación – 1,5 kg aprox.` declara el mismo rango con otro nominal → los pesos del título no son comparables entre SKUs |
| Marcador `App/APP` | `Queso Mantecoso Quilacoya Bolita 0.9 Kg App` | 34 títulos en alimentika con 7 grafías (`APP`, `App`, `app`, `App.`, `aprox.`) |
| Piso abierto | `Congrio Dorado HGT 3 Kg UP` · `Pulpo del Norte Entero Crudo IQF 2-UP Caja 12Kg.` | `UP` = "y hacia arriba", sin techo. En el pulpo, además, 2 no es el SKU (la caja de 12 kg lo es) |
| Rango de masa por pieza sin total | `Merluza Austral Lomo Selecto Porción 100-200g Unidad` · `Congrio Dorado Medallón con Piel IVP 250-350g unidad` | Indeterminado por construcción |
| Dos masas del mismo nivel | `Salmón Slice Carpaccio Ahumado en Frío Laminado 250g 10kg.` | No hay señal lingüística de cuál es el SKU: no aparece "caja" |
| Masa + conteo sin operador | `Hamburguesa Vacuno 225G Pampa Verde Caja 20 Unidades` · `Albóndigas de vacuno 6 unidades 180g Super Beef` | 225 g es por pieza y 4,5 kg (el vendible) no aparece en el título. En las albóndigas ningún campo del feed dice si 180 g es total o unitario |
| Tres niveles de empaque | `Arroz Klick 1 Kg – Manga de 10 Kg (10 Unidades por Caja)` · `Azúcar Blanca 10 Kg (Pallet 100 Mangas, 10 Kg Por Manga)` | Unidad/manga/caja/pallet: el título declara los tres como si fueran "el formato" |
| Dos variantes en un título | `Ostión Carne del Norte con Coral Congelado (500g / 1kg)` · `Pinzas de Jaiba Cóctel Cocidas (500g – 1kg)` · `Caja de Regalo Caviar Oscietra Kenoz (30g – 50g)` | Son dos SKUs reales; "último match gana" asigna 1 kg a algo que también se vende en 500 g (2×) |
| Contradicción con el feed | `Brócoli 1kg` (desc: "bolsa de 2 kilos") · `Salmón salar ahumado laminado 1kg` (`weight=0.5`) · `Aros de cebolla apanados 1kg` (`weight=0.6`) · `Porciones de salmón salar 1kg` (`weight=1.4`) | El título parsea perfecto y está mal |
| Título truncado / corrupto | `Queso Mozzarella Polenghi Laminado 2,73 kg (4 Unidades Caja` | Paréntesis sin cerrar: no se puede decidir si falta un dato |
| Peso bruto con concha/caldo cuando se compara | `Chorito Cocido con Concha en su Caldo 1kg` vs `Chorito Rubio Premium Cocidos sin Concha 1kg` | Parsean ambos a 1,000 pero **no son comparables**: emitir `kg` + flag `PESO_BRUTO` y excluir del ranking precio/kg cruzado |
| Formato solo en variaciones | `Pulpa de Piña Krio-Puch` (attr `Cantidad`: `1kg`, `caja 10kg`) · `Arvejas Congeladas` (`200 Grs`/`500 Grs`/`1 Kgr`) | Un SKU de título = N SKUs vendibles. `caja 10kg` es 10×1kg: leerlo como envase unitario multiplica ×10 el costo unitario |
| Volumen aunque el producto sea sólido | `Locos Cocidos – Frasco 700 cc` · `Pulpo en conserva 1lt` | El peso drenado no se publica en ningún campo |

---

## 5. CASOS DE PRUEBA (25, todos literales de la cosecha)

| # | Título literal | Esperado | Qué rompe si se hace mal |
|---|---|---|---|
| 1 | `Langostino entero u15 2kg. (caja x 12 kilos)` | `2.000` | "último match" o "match mayor" → 12 kg (6× el peso real). Además `2kg.` con punto pegado rompe `(\d+)\s*kg\b` anclado a fin de token |
| 2 | `Camarón 100/200 Cocido Pelado 500 G` | `0.500` | "primer número" → 100. Discrimina calibre-antes-de-peso |
| 3 | `Camarón 21/25 crudo con cáscara` | **CUARENTENA** | Aceptar el calibre como peso (21) o asumir 1 kg. El único peso está en `short_description` y es el de la **caja** ("1 Caja 10 Kilos") |
| 4 | `Salmón Ahumado Laminado 1,130 Kg` | `1.130` | Coma-miles → 1130 kg (1000×). Regex `\d+,\d(kg)` → 1,1 (−2,7%) |
| 5 | `Queso Crema Nacional 1,36kg` | `1.360` | Regex de un decimal calibrada sobre `1,1kg`/`2,5kg` captura `1,3` y descarta el 6: error del 4,4% silencioso |
| 6 | `Bocaditos Papa Y Queso Bembrazil Bolsa De 1.05 Kg` | `1.050` | `locale.atof` es-CL → 105 kg. En pulpochile **no existe** ni una coma decimal: el punto es decimal |
| 7 | `LOCOS COCIDOS CONSERVA FRASCO 1.000CC UNIDAD` | **CUARENTENA** (volumen) | `float('1.000')` = 1 → 1 cc (1000×); o cc→g → 1 kg de peso drenado inventado |
| 8 | `QUESO T10 LLIFEN COLUN 10 KG APP` | **CUARENTENA** (`APP`) | Regex de volumen `\d+\s*l` captura "10 L" de `T10 LLIFEN` → aceite de 10 litros en un queso. Y si se salva de eso, `APP` = peso variable |
| 9 | `Azúcar Blanca 10 Kg (Pallet 100 Mangas, 10 Kg Por Manga)` | **CUARENTENA** (pallet) | Separador U+202F: `/\d+ ?kg/` devuelve `[]` en JS y en `split(' ')`. Y aunque se parsee, el vendible puede ser pallet, manga o unidad |
| 10 | `Arroz Klick 1 Kg – Manga de 10 Kg (10 Unidades por Caja)` | **CUARENTENA** (3 niveles) | "último match" → 10 kg. En-dash U+2013 hace que `split('-')` no parta el título |
| 11 | `Papas Prefritas 13x13mm 4X2,5KG Global Frozen` | `10.000` | Las dos `x` significan cosas opuestas en el **mismo** título: corte y multiplicador. Tratarlas igual da 13 kg, 169 unidades o 2,5 kg |
| 12 | `Papas Prefritas 6X6 Bolsa De 2.5 Kg` | `2.500` | `\d+\s*[A-Za-z]+` captura literalmente `6 Bolsa` (verificado en la cosecha) |
| 13 | `Demi &#8211; Baguette` | **CUARENTENA** | Regex sobre JSON crudo → 8211 g (8,2 kg) de un pan que no declara peso. Falso positivo puro |
| 14 | `Salmón Atlántico Porción Sin Piel 1&#215;5 IVP Congelado Caja 5 kg` | `5.000` | Sin decodificar: `\d+` ve 1, 215 y 5 → "215 g". Con `1×5` mal clasificado como multiplicador → 5 unidades |
| 15 | `CARNE MOLIDA 4% MATERIA GRASA CONGELADA CAJA 5KG` | `5.000` | "primer número" → 4 kg. Discrimina heurística posicional |
| 16 | `Salmón Slice Carpaccio Ahumado en Frío Laminado 250g 10kg.` | **CUARENTENA** | Cualquier desempate (primero/último/mayor) inventa una decisión: 40× de diferencia |
| 17a | `ATÚN STEAK 170-220G IVP UNIDAD` | **CUARENTENA** | Un mapa fragmento→formato colapsa 17a y 17b, que difieren ~25× |
| 17b | `ATÚN STEAK 170-220G IVP CAJA 5KG` | `5.000` | Idem, en sentido inverso: si el rango "gana", se pierde la caja |
| 18 | `Salmón salar ahumado laminado 1kg` (feed: `weight=0.5`) | **CUARENTENA** | Título perfecto, 100% de error. Solo el contraste con el campo estructurado lo detecta |
| 19 | `Aros de cebolla apanados 1kg` (feed: `weight=0.6`) | **CUARENTENA** | Peor que el anterior: la descripción **no** lo aclara. Un pipeline title-only no tiene forma de saberlo |
| 20 | `Ostión Sin Coral 1KG` | `1.000` | Regex case-sensitive de `1kg` lo pierde entero (83 aciertos vs 84 reales) y `weight` está vacío: no hay respaldo |
| 21 | `Espárrago Kgr` | `PRICE_BASIS_PER_KG` | Unidad **sin** número: toda regex numérica da falso negativo; y `Kgr` es la 4.ª grafía de kilo del catálogo |
| 22 | `Camarón 36/40 Crudo Pelado desvenado  Imp.kilo` | `PRICE_BASIS_PER_KG` | `kilo` pegado al punto de `Imp.`: `\b` o "espacio antes de la unidad" no lo ven → el SKU queda sin formato o se lleva el calibre |
| 23a | `Volcán Chocolate 2 unid*90gr.` | `0.180` | `*` **es** multiplicador: leer 90 g entrega la mitad del contenido |
| 23b | `Champiñon  Laminado *1 Kilo` | `1.000` | El **mismo** `*` es ruido decorativo. 23a+23b juntos matan cualquier tratamiento uniforme de `*` |
| 24 | `Congrio Dorado HGT 3 Kg UP` | **CUARENTENA** | `3 Kg` parsea limpio pero es el calibre mínimo por pieza, sin techo |
| 25 | `CAMARÓN ECUATORIANO APANADO PRECOCIDO 1LB REAL` | `0.454` | Regex de volumen `\d+\s*L` → 1 litro (fallo reproducido en la cosecha original). Y `2/3Lb` / `2LB-UP` deben seguir yendo a cuarentena |

*Casos de control adicionales recomendados (mismo espíritu):* `Papas Pre-fritas 2.5  kilos 12*12` → `2.500` (orden invertido + doble espacio); `Ostión Carne del Norte con Coral Congelado (500g / 1kg)` → CUARENTENA; `Hamburguesa Vacuno 225G Pampa Verde Caja 20 Unidades` → CUARENTENA.

---

## 6. TRAMPAS ORDENADAS POR DAÑO

Daño = (magnitud del error) × (SKUs afectados) × (probabilidad de pasar desapercibido). Universo: **1279 SKUs / 10 catálogos**.

| # | Trampa | SKUs | Magnitud | Por qué duele tanto |
|---|---|---|---|---|
| 1 | **"1kg" es la unidad de precio, no el envase** (rapa-nui: "Nota: Valor publicado por kilo") | **81** de 96 | hasta 2× en ambos sentidos | Es la mayoría del catálogo, no un caso borde. Y hay contradicciones duras dentro: `Brócoli 1kg`=2 kg, `Salmón salar ahumado laminado 1kg`=0,5 kg, `Aros de cebolla apanados 1kg`=0,6 kg, `Porciones de salmón salar 1kg`=1,4 kg. Silencioso al 100% |
| 2 | **Calibre leído como peso** (`36/40`, `36-40`, `100-200`, `U15`, `L1`, `2-UP`) | **≈94** | 15×–40× o inversión total | Es el fragmento numérico más frecuente del universo después del peso. Tres sintaxis (barra, guion, alfanumérica) y en `Chorito 100/200` la misma cadena que en camarón significa otra base |
| 3 | **Volumen convertido a masa** (lt/ml/cc/oz) | **81** | 0%–100% de invención | Un `1lt` de locos "pesa" 1 kg solo si te lo inventás; el peso drenado no está publicado en ningún campo de ningún catálogo. Además `1.000cc` suma el error de 1000× del punto de miles |
| 4 | **Separador decimal cruzado** (punto Y coma conviven, a veces en el mismo feed) | **≈55** con decimal, **≥10** con conflicto directo | 10×, 100× o 1000× | alimentika tiene `3.2 KG` y `3,2 kg` para el mismo valor; rapa-nui trae `weight='0.6'` y `formatted_weight='0,6 kg'` en el **mismo registro**; southwind mezcla `1.4Kg` y `1,7Kg` en la **misma lista de variantes**. Cualquier política única falla en un lado |
| 5 | **Formato solo en variaciones** (título sin formato) | **≈21** (distribuidoris) + **24** (todofrio) + **8** (pulpochile) + variantes southwind/mariscos ≈ **70+** | falso negativo, o ×10 | `Pulpa de Piña Krio-Puch` con attr `caja 10kg` = 10×1kg: leerlo como envase unitario multiplica ×10 el costo. Y las etiquetas de atributo son 6 (`Seleccione Cantidad:`, `SELECCIONE FORMATO:`, `FORMATO:`, `Rango de Peso`, `Selección Peso:`, `Cantidad`) |
| 6 | **Conteo tomado como masa** | **≈125** | inventa kg donde no existen | `Ostión Media Concha – 10 Unidades` tiene `Peso: N/A` explícito; pero `Machas Media Concha – 20 Unidades` **sí** pesa 340 g (solo en el body). Los dos hermanos se comportan al revés → ninguna regla simple sirve |
| 7 | **Doble nivel de empaque** (SKU vs caja/manga/pallet) | **≈35** con masa en ambos niveles + **≈110** con case-pack de conteo (alimentika) | 4×–10× | `caja x 12 kilos` vs `2kg.`; `250g 10kg.` sin la palabra "caja" para desempatar. Es el error que invierte rankings de proveedor completos |
| 8 | **Peso variable presentado como exacto** (`aprox`, `App`, `-UP`, rangos) | **34** (alimentika) + **6** (delmar) + **~14** (rapa-nui "puede variar al pesar") ≈ **54** | ±10%–±40% | `Salmón Premium – 1,7 kg aprox.` no corresponde a ningún salmón real (rango 1,5–2,0). El parser acierta sintácticamente y miente en los hechos |
| 9 | **Entidades HTML sin decodificar** | **≈11 títulos** | inventa números que no existen | `&#8211;`→8211, `&#215;`→215, `&#038;`→038. `Demi &#8211; Baguette` genera un peso de la nada en un producto sin peso |
| 10 | **Dimensión de corte como formato/pack** | **≈23** | 121 unidades, 12 mm, o 5× | `11X11`, `12X12 Mm`, `13x13mm`, `7*7`, `9Mm`, `10mm`. Y `Papas Prefritas 13x13mm 4X2,5KG` tiene corte y multiplicador con la misma letra |
| 11 | **Espacio Unicode y unidad pegada a palabra** | **2** (U+202F) + **1** (`PIEZA12`) + **1** (`Imp.kilo`) | falso negativo silencioso | La regex devuelve `[]` sin error. No hay excepción, no hay log: el SKU simplemente desaparece del comparativo |
| 12 | **Falsos amigos de unidad por substring** | 14 (`oz`⊂Gyozas) + 7 (`lb`⊂Albacora/Albóndigas) + 6 (`cc`⊂Carpaccio) + 1 (`L`⊂LLIFEN) ≈ **28** | inventa unidades | Solo aparece si alguien busca la unidad sin `\b`. La regla del run alfabético completo lo elimina de raíz |
| 13 | **Packaging / no-alimento en el mismo feed** | **≈31** | contamina el denominador | `Vaso Domo 235 ml`, `Freidora de Aire Airflow 3.5 Lts FDV`, `Gift Card $100.000`, `Arriendo frio congelado`, `Producto pruebas`. Inflan la cobertura y aparecen en demos |
| 14 | **Grafías múltiples del mismo formato** (GROUP BY case-sensitive) | `1 Kg`/`1 kg`/`1KG`/`1Kg.`/`1 Kilo`/`1kilo`/`Kgr`… ≈ **6 grafías por formato** | fragmenta el análisis, no el peso | No produce un peso falso, pero parte "18 productos de 1 kilo" en dos grupos de 8 y 10 y esconde la cobertura real |
| 15 | **Títulos duplicados / casi homónimos** | `Locos Cocidos` ×2 (700cc vs 1.000cc), `Salsa soya Kikkoman baja en sodio 148ml` ×2, `Lasagna Bolognesa Sadia 600g` vs `CAJA 6X600G` | colapsa SKUs distintos | Cualquier dedup por nombre funde formatos y precios que difieren 6× |

**Cobertura esperada bajo esta espec.** No es un defecto que la cuarentena sea grande: es la medición. Un normalizador que reporte 95% de cobertura sobre estos 1279 SKUs está mintiendo en al menos las trampas 1, 2, 5, 7 y 8 — es decir, en más de 300 SKUs. El KPI correcto es **precisión sobre lo publicado**, con la tasa de cuarentena reportada por catálogo y por motivo.