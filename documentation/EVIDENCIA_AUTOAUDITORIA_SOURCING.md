# Evidencia: Kern se audita a sí mismo (sourcing)

> **Cuándo se usa esto.** En la *segunda* conversación con un prospecto, cuando
> pregunta "¿y cómo sé que esto no me miente?". No es material de apertura en frío:
> abrir mostrando un bug propio no vende, lo que vende es la respuesta a esa pregunta
> cuando ya la hizo.

**Dataset:** `Procurement KPI Analysis Dataset.csv` — 777 órdenes de compra reales,
5 proveedores en competencia (CC0, Kaggle). Mismo archivo en las dos corridas.

---

## Antes

Comando: el brief de sourcing sobre el CSV crudo, sin mapeo de columnas.

```
supplier,deliveries,otif,on_time_rate,in_full_rate,avg_lead_time,ppm,avg_price,topsis_score,rank
Alpha_Inc,141,0.0,0.0,0.0,0.0,0.0,0.0,0.0,1
Beta_Supplies,156,0.0,0.0,0.0,0.0,0.0,0.0,0.0,2
Delta_Logistics,171,0.0,0.0,0.0,0.0,0.0,0.0,0.0,3
Epsilon_Group,166,0.0,0.0,0.0,0.0,0.0,0.0,0.0,4
Gamma_Co,143,0.0,0.0,0.0,0.0,0.0,0.0,0.0,5
[ok] conf=1.00  Compared 5 suppliers on OTIF / lead time / quality; recommended award: Alpha_Inc.
```

Tres cosas mal, no una:

1. **El scorecard entero en cero.** Ninguna columna de decisión se encontró, y "columna
   ausente" se convirtió en `0.0` en vez de en un error.
2. **Igual adjudicó un ganador.** Con las cinco filas idénticas, TOPSIS empata a todos;
   el `rank 1` salió por orden alfabético. `Alpha_Inc` ganó por la A.
3. **`conf=1.00` y QA limpio.** La confianza mide si el ruteo eligió bien la herramienta,
   no si el dato servía. Un brief bien ruteado sobre un dataset vacío informa 100%.

Variante peor, con la columna mapeada a mano a `Order_Status`: `bool('Cancelled')` es
`True` en Python, así que **las órdenes canceladas puntuaban como entregas perfectas**
(`on_time_rate = 1.00`).

---

## Después

Mismo archivo, mismo comando, más una declaración explícita del SLA contractual:

```
  supplier            n    otif  ontime  infull   lead       ppm   price  topsis
  Alpha_Inc         116   0.534   0.724   0.759   10.7    21,946   58.05   0.966
  Epsilon_Group     149   0.497   0.664   0.718   10.9    29,636   59.32   0.880
  Gamma_Co          130   0.531   0.731   0.715   10.2    49,609   59.13   0.780
  Beta_Supplies     143   0.448   0.650   0.699   11.3    93,253   58.20   0.397
  Delta_Logistics   151   0.477   0.662   0.682   10.9   143,973   57.08   0.079
  recommended: Alpha_Inc   qa_issues=[]
```

Y el entregable ahora **declara sus propios supuestos**, en vez de esconderlos:

```
  [note] Lead time measured as 'Delivery_Date' minus 'Order_Date'.
  [note] 1 row(s) have a negative lead time (delivery dated before the order) and are
         excluded - such a row scores as the fastest, always-on-time supplier.
  [note] [ASSUMPTION] On-time measured against a declared 14-day SLA - the data carries
         no promised-delivery date. Confirm the contractual SLA before awarding.
  [note] In-full derived from 'Order_Status' being one of ['Delivered'].
  [note] 136 of 777 rows had no defect count in 'Defective_Units'; defect PPM is measured
         over the remaining rows only, not diluted across all of them.
  [note] 88 of 777 purchase orders are excluded from the scorecard for the reasons above;
         the panel is scored on the remaining 689.
```

Vale la pena mirar el PPM: subió (Alpha 17.677 → 21.946) respecto de una versión intermedia
de este mismo arreglo que rellenaba con cero los 136 conteos de defectos faltantes. Rellenar
con cero no significa "sin defectos": mete 136 filas nunca inspeccionadas en el denominador y
**baja** la tasa. El número honesto se mide sobre lo que sí se inspeccionó, y la cobertura se
informa aparte.

`Alpha_Inc` sigue ganando — pero ahora gana por 0.538 de OTIF contra 0.448 del último,
no por la primera letra del nombre.

---

## Y cuando el dato no alcanza, dice que no alcanza

Sin declarar el SLA, no inventa uno:

```
[needs_data] cannot compute on_time: the data carries no on-time column and no
promised-delivery date. Pass promised_date_col, or declare the contractual lead-time SLA
with sla_days=<n>. On-time scored against an undeclared default is a fabricated number.
```

Apuntando la columna de on-time a un campo de texto libre:

```
[needs_data] column 'Order_Status' cannot be read as on_time: values like ['cancelled',
'delivered', 'partially delivered'] are free text, not a yes/no flag.
```

---

## Qué probar con esto

El argumento **no** es "Kern no tiene bugs". Es más chico y más verificable:

- La herramienta ahora **se niega por nombre** cuando le falta el campo que decide, en vez
  de rellenarlo con el valor más halagador.
- Cada supuesto que sí toma queda **escrito en el entregable**, no en la cabeza de nadie.
- El bug se encontró **corriendo la herramienta contra datos reales**, no leyendo el código
  — y la suite estaba 100% verde mientras el bug existía, porque el test entraba por un
  fixture ya mapeado. El arreglo estructural es el test que entra por un CSV crudo
  (`tests/test_sourcing_raw_csv.py`).

Ese último punto es el que responde la pregunta del prospecto: la diferencia entre una
herramienta que dice números y una que sabe cuándo callarse.
