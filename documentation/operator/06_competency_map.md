# 06 · Mapa de Competencias — Qué Debes Saber y Cómo Aprenderlo

> No necesitas derivar los modelos: Kern los implementa y los valida contra
> simulación. Pero sí necesitas **entenderlos lo suficiente para defenderlos** y
> **juzgar cuándo aplican**. Este es tu mapa de competencias y tu ruta de estudio.

---

## 🧩 Matriz de competencias

Tres niveles: **Fundamento** (sin esto no operas), **Competente** (entregas con
credibilidad), **Avanzado** (consultoría de cadena completa).

| Competencia | Nivel | Por qué la necesitas | Dónde se ve en Kern |
|---|---|---|---|
| Leer un *report.md* + xlsx y sus *Fuentes* | Fundamento | Defender cada número ante el cliente | Salida de cualquier job |
| Los 4 desenlaces y cerrar cada uno | Fundamento | Es el núcleo de tu rol | `src/guided.py`, [doc 02](02_division_of_labor.md) |
| Aprobar un *writeback* por *risk tier* | Fundamento | Controlas lo irreversible | `src/writeback.py`, [RB-5](04_runbooks.md) |
| σ_e vs. σ_demanda (error de pronóstico) | Fundamento | El error #1 del gremio; lo vas a explicar seguido | `src/safety_stock.py`, `src/forecasting.py` |
| EOQ, `(s,Q)`/`(R,S)`, niveles de servicio | Competente | Política de inventario | `src/eoq.py`, `src/policies.py` |
| ABC-XYZ y política por segmento | Competente | Segmentación accionable | `src/classification.py`, `abc_xyz` tool |
| Demanda intermitente (Croston) y sesgada (gamma) | Competente | Saber cuándo el modelo normal sub-stockea | `src/forecasting.py`, `src/distributions.py` |
| Métricas de exactitud (MAPE/WAPE/RMSSE/MASE) + sesgo | Competente | Juzgar la calidad del pronóstico | `src/forecast_metrics.py` |
| DDMRP (buffers rojo/amarillo/verde, net-flow) | Competente | Reabasto basado en demanda | `src/ddmrp.py`, `ddmrp` tool |
| Cadencia S&OP / IBP y facilitación | Avanzado | Facilitas la junta y fuerzas la decisión | `src/sop.py`, [RB-7](04_runbooks.md) |
| Cost-to-serve y capital de trabajo (cash-to-cash) | Avanzado | Decisiones de cliente/canal y tesorería | `src/cost_to_serve.py`, `src/working_capital.py` |
| Sourcing / MCDM (TOPSIS), supplier scorecards (OTIF/PPM) | Avanzado | Selección y QBR de proveedores | `src/mcdm.py`, `src/supplier_scorecard.py` |
| *Landed cost* (Incoterms) | Avanzado | Costo real de importación | `src/landed_cost.py` |
| Multi-echelon / diseño de red | Avanzado | Dónde colocar stock en la red | `src/multi_echelon.py` |
| Riesgo y resiliencia (TTR/TTS, single-source) | Avanzado | Mapas de riesgo | `src/risk.py` |
| Escalación: disputa / legal / financiera / operativa | Competente | Enrutar al rol correcto con SLA | `src/escalation.py`, [RB-6](04_runbooks.md) |

---

## 📚 La biblioteca L3 como tu currículo

Kern está fundamentado en un **grafo de conocimiento de 23 libros SCM** (1824
nodos / 3640 aristas), consultable y citado por capítulo. **Es tu material de
estudio dirigido**: cuando un entregable cite un concepto que no dominas, ve
directo a su fuente.

**Referencia primaria del motor de inventario:**
- Vandeput, N. (2020). *Inventory Optimization: Models and Simulations*. De Gruyter.
- Vandeput, N. (2021). *Data Science for Supply Chain Forecasting* — el error de
  pronóstico σ_e (§4.2.5).

**Áreas cubiertas por el grafo:** forecasting, pricing / revenue management, SCM
general, inventario, manufacturing planning, operations management, logística,
sostenibilidad y liderazgo de cadena de suministro.

### Consultar el conocimiento (autoestudio dirigido)

```bash
# Explica un concepto y dónde se implementa
python examples/query_knowledge.py --explain newsvendor

# Busca un tema en el grafo
python examples/query_knowledge.py --search "fill rate"

# El puente teoría↔código: qué función implementa el concepto citado
python examples/query_knowledge.py --bridge "safety stock"
```

Cada cita en un entregable resuelve **el capítulo del libro Y la función `src/`**
detrás del número. Estudias justo lo que necesitas defender, sin leer 23 libros
completos.

### Skills de agente (en Claude Code / Cursor)

`.cursor/skills/` (sincronizados a `~/.claude/skills/`):
- `/vandeput-inventory-optimization` — overview + árbol de decisión
- `…-eoq-policies` (Cap. 2–5) · `…-service-cost` (Cap. 6–8) · `…-advanced` (Cap. 9–13)

---

## 🛤️ Ruta de incorporación sugerida

| Semana | Foco | Resultado |
|---|---|---|
| **1** | Docs 01–02 + RB-2/RB-5. σ_e, EOQ, `(s,Q)`/`(R,S)` | Corres y revisas un entregable de inventario; apruebas un writeback |
| **2** | ABC-XYZ, forecasting (Croston/gamma), métricas | Produces política por segmento y paquete de pronóstico |
| **3** | Escalaciones (RB-6), handoffs (RB-3), DDMRP, landed cost | Cierras los 4 desenlaces con soltura |
| **4** | Modo SCM: S&OP (RB-7), cost-to-serve, sourcing/MCDM | Facilitas un ciclo S&OP y un estudio de sourcing |
| **Continuo** | Cierra brechas de la matriz con la biblioteca L3 | Subes de Competente a Avanzado |

---

## 🎓 La prueba de que estás listo

Puedes, sin ayuda:

1. Tomar un brief de cliente, elegir el **modo** y el **entregable** correctos.
2. Producir el entregable y pasarlo por **tu** compuerta de QA.
3. **Defender cada número** con su *Fuente* (L3) cuando el cliente cuestione.
4. **Cerrar los cuatro desenlaces**: elegir opciones, ejecutar handoffs, aprobar
   writebacks irreversibles, enrutar escalaciones con su SLA.
5. **Cubrir o asumir documentadamente** cada residuo.
6. Dejar una **bitácora auditable** de qué se decidió, quién aprobó y por qué.

Cuando eso es rutina, eres un operador de Kern pleno: el humano confiable al
final del lazo que el agente, por diseño, nunca cierra solo.

---

*Fin del Portafolio del Operador. Vuelve al [README](README.md) para el índice
completo.*
