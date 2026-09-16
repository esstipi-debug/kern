# Twin sembrado con el catálogo real de un negocio — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el catálogo público real de un negocio (JSON de Shopify, 170 productos / 216 variantes) entre tal cual al twin de red existente, con lo no observable declarado como hipótesis, y salga un reporte por grupo valorizado a precio de lista.

**Architecture:** Un módulo nuevo `jobs/twin_catalogo.py` que (1) lee y clasifica el catálogo en tres estados, (2) arma una lista de payloads —una red por `product_type`, dos payloads por grupo (disponibles / quebradas)— para el `run()` que ya existe en `jobs/digital_twin_job.py`, y (3) consolida los `TwinReport` en un `CatalogTwinReport` con venta en riesgo en CLP, cobertura e hipótesis. Dos cambios aditivos y compatibles al job del twin (reenviar `initial_on_hand`; exportar `served_rows`). Un example lo corre sobre el grupo Pescados bajo cuatro escenarios.

**Tech Stack:** Python 3.11+, numpy, pandas (ya en el repo), pytest, ruff. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-09-16-twin-sembrado-catalogo-real-design.md`

## Global Constraints

- No se agrega ninguna tool al registry ni se toca ninguno de los 8 lugares fijos (`scm_agent/tools.py`, `tool_options.py`, `citation_gate.py`, `src/i18n.py`, `tests/test_scm_agent.py`, `tests/test_capability_smoke.py`, `webapp/how_it_works_data.py`, conteos en README/CLAUDE.md).
- `src/digital_twin.py` no se modifica.
- `jobs/digital_twin_job.py` solo recibe los dos cambios aditivos de la Task 1; nada más.
- Periodo = día; 364 periodos por defecto. Techo de celdas: `_MAX_CELLS` (2.000.000) de `jobs/digital_twin_job.py`. Techo de productos por payload: 250.
- `auto_size_factor` por defecto 1,0.
- Tres estados por campo: `declarado` / `no_declarado` / `contradicho`. Nunca dos.
- El deck abre con la frase "Esto es una hipótesis sobre su catálogo, no una medición de su bodega." y no nombra proveedores reales.
- Toda variante sin precio finito y positivo se excluye y se cuenta.
- `ruff check` limpio en cada commit. Cada test se ve en rojo antes de implementar.
- Rama `feat/twin-catalogo-real`, worktree `C:\Users\Gamer\Music\scm\.wt-twin`. Python: el global (`python`, 3.13, tiene pandas/numpy). Correr pytest con `PYTHONPATH=.` desde la raíz del worktree.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `jobs/digital_twin_job.py` (modificar, 2 puntos) | `run()` reenvía `initial_on_hand` del nodo del payload; `TwinReport` gana `served_rows` y `run()` lo llena |
| `jobs/twin_catalogo.py` (crear) | Todo lo nuevo: `Variante`, `Cobertura`, `Hipotesis`, `cargar_catalogo`, `armar_payloads`, `correr`, `consolidar`, `deck`, `escribir` |
| `examples/run_twin_catalogo.py` (crear) | CLI: catálogo → grupo → escenarios → carpeta de salida |
| `tests/fixtures/twin_catalogo/catalogo_mdlc_6.json` (crear) | 6 productos reales extraídos del JSON del 28-ago (MAFI con 5 variantes, uno sin SKU, uno quebrado, Cerdo, Otros, un Pescados sano) |
| `tests/test_digital_twin_tool.py` (modificar) | 2 tests para los cambios aditivos de la Task 1 |
| `tests/test_twin_catalogo.py` (crear) | Las pruebas del spec (1-7) más guardas |

---

### Task 1: el twin reenvía `initial_on_hand` y exporta lo servido

**Files:**
- Modify: `jobs/digital_twin_job.py:41-57` (dataclass `TwinReport`), `jobs/digital_twin_job.py:152-158` (construcción de `NodeSpec` en `run()`), y el bloque de acumulación de trazas dentro del `for idx, product in enumerate(payload["products"])` (~línea 190-205)
- Test: `tests/test_digital_twin_tool.py`

**Interfaces:**
- Consumes: `src.digital_twin.NodeSpec(initial_on_hand: float | None)` (ya existe, línea 66) y `TwinResult.served: dict[str, np.ndarray]` (ya existe, línea 106).
- Produces: `run(payload)` acepta `"initial_on_hand"` opcional en cada dict de nodo; `TwinReport.served_rows: tuple[tuple[int, str, str, float], ...]` con la misma forma que `demand_rows` (periodo, product_id, tienda, unidades servidas).

- [ ] **Step 1: Escribir los dos tests que fallan**

Agregar al final de `tests/test_digital_twin_tool.py`:

```python
def test_run_forwards_initial_on_hand_from_the_node_payload():
    """Un nodo del payload puede arrancar vacío. Sin el reenvío, run() ignora la
    clave y la tienda arranca en su nivel S auto-dimensionado (> 0)."""
    payload = dt.prepare({"n_stores": 1, "n_dcs": 1, "n_products": 1, "periods": 30, "seed": 3})
    for node in payload["nodes"]:
        if node["kind"] in ("dc", "store"):
            node["initial_on_hand"] = 0.0
    report = dt.run(payload)
    first_period_store = [
        on_hand for t, _pid, loc, on_hand in report.inventory_rows if t == 0 and loc == "STORE-1"
    ]
    assert first_period_store == [0.0]


def test_run_exports_served_rows_aligned_with_demand_rows():
    """served_rows tiene una fila por (periodo, producto, tienda), igual que
    demand_rows, y nunca sirve más de lo demandado."""
    payload = dt.prepare({"n_stores": 2, "n_dcs": 1, "n_products": 2, "periods": 40, "seed": 5})
    report = dt.run(payload)
    assert len(report.served_rows) == len(report.demand_rows)
    demanded = {(t, p, s): u for t, p, s, u in report.demand_rows}
    for t, p, s, served in report.served_rows:
        assert (t, p, s) in demanded
        assert 0.0 <= served <= demanded[(t, p, s)] + 1e-9
```

- [ ] **Step 2: Verlos fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_digital_twin_tool.py -q -k "initial_on_hand or served_rows"`
Expected: 2 failed — el primero con `assert [123.4] == [0.0]` (un número positivo), el segundo con `AttributeError: 'TwinReport' object has no attribute 'served_rows'`.

- [ ] **Step 3: Implementar (dos cambios aditivos)**

En `TwinReport`, agregar el campo con default al final de la dataclass (después de `summary: str`):

```python
    served_rows: tuple[tuple[int, str, str, float], ...] = ()   # periodo, producto, tienda, servido
```

En `run()`, la construcción de specs pasa a:

```python
    specs = [NodeSpec(
        name=n["name"], kind=n["kind"], supplier=n["supplier"],
        lead_time=int(n["lead_time"]), review_period=int(n["review_period"]),
        capacity=(float(n["capacity"]) if n.get("capacity") is not None else None),
        initial_on_hand=(
            float(n["initial_on_hand"]) if n.get("initial_on_hand") is not None else None
        ),
    ) for n in payload["nodes"]]
```

Declarar `served_rows: list[tuple[int, str, str, float]] = []` junto a `demand_rows`, y dentro del bucle `for store, trace in result.demand.items():` (donde ya se calcula `served = result.served[store]`) agregar:

```python
            served_rows.extend(
                (t, pid, store, round(float(served[t]), 2)) for t in range(periods)
            )
```

Y en el `return TwinReport(...)` agregar `served_rows=tuple(served_rows),`.

- [ ] **Step 4: Verlos pasar, y que nada más se rompa**

Run: `PYTHONPATH=. python -m pytest tests/test_digital_twin_tool.py tests/test_digital_twin.py -q && python -m ruff check jobs/digital_twin_job.py tests/test_digital_twin_tool.py`
Expected: todo `passed`, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add jobs/digital_twin_job.py tests/test_digital_twin_tool.py
git commit -m "feat(twin): run() reenvía initial_on_hand y exporta served_rows (aditivo, compatible)"
```

---

### Task 2: la fixture de 6 productos reales y la carga con tres estados

**Files:**
- Create: `tests/fixtures/twin_catalogo/catalogo_mdlc_6.json`
- Create: `jobs/twin_catalogo.py` (primera mitad: modelo + `cargar_catalogo`)
- Create: `tests/test_twin_catalogo.py`

**Interfaces:**
- Consumes: nada del repo.
- Produces:
  - `Variante(clave: str, sku: str | None, variant_id: int, producto: str, grupo: str, precio: float, disponible: bool, estado_sku: str)` — frozen dataclass.
  - `Cobertura(sku_declarado: int, sku_no_declarado: int, sku_contradicho: int, precio_declarado: int, precio_excluido: int, quiebre_hoy: int, grupos: tuple[tuple[str, int], ...])` — frozen.
  - `cargar_catalogo(path: str | Path, *, min_variantes_por_grupo: int = 5) -> tuple[tuple[Variante, ...], Cobertura]`.
  - Constantes: `ESTADO_DECLARADO = "declarado"`, `ESTADO_NO_DECLARADO = "no_declarado"`, `ESTADO_CONTRADICHO = "contradicho"`, `GRUPO_OTROS = "Otros"`.

- [ ] **Step 1: Generar la fixture (script de una vez, no se versiona)**

Correr desde la raíz del worktree:

```bash
python - <<'PY'
import json
src = r"C:\Users\Gamer\Music\scm\radiografia-mdlc-catalogo-2026-08-28.json"
ids = {8586271621408, 10549043298592, 10425744621856, 10355566477600, 10168153604384, 10469628805408}
d = json.load(open(src, encoding="utf-8"))
sel = [p for p in d if p["id"] in ids]
assert len(sel) == 6, len(sel)
keep_p = ("id", "title", "handle", "product_type", "vendor", "created_at", "updated_at")
keep_v = ("id", "product_id", "sku", "title", "price", "available", "grams", "created_at", "updated_at")
out = [{**{k: p[k] for k in keep_p}, "variants": [{k: v[k] for k in keep_v} for v in p["variants"]]} for p in sel]
import os; os.makedirs("tests/fixtures/twin_catalogo", exist_ok=True)
json.dump(out, open("tests/fixtures/twin_catalogo/catalogo_mdlc_6.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("variantes:", sum(len(p["variants"]) for p in out))
PY
```

Expected: `variantes: 13` (MAFI 5, Camarón 1, Salmón filete 4, SAS454 1, Cerdo 1, Otros 1). Los 6 son: `Merluza Austral Filete` (Pescados; SKU `MAFI` en 0,5 kg y 1 kg → contradicho; 3 variantes sin SKU; 3 quebradas), `Camarón 40-50 con Cáscara Crudo y Cabeza 1kg` (Mariscos, sin SKU), `Salmón filete sin piel fresco` (Pescados, 4 variantes sin SKU, todas quebradas), `Salmón Slice Ahumado al Frio 454g` (Pescados, `SAS454`, disponible), `Cerdo Chuleta Centro 1kg` (Cerdo, `ZCCC`), `Pinza de cocina de bambú 17,8cm` (Otros, `PINB`).

- [ ] **Step 2: Escribir los tests que fallan**

Crear `tests/test_twin_catalogo.py`:

```python
"""Twin sembrado con el catalogo real: carga con tres estados, payloads por grupo,
consolidacion valorizada. Fixture: 6 productos reales de Mariscos De La Concha
(2026-08-28), 13 variantes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs import twin_catalogo as tc

FIXTURE = Path(__file__).parent / "fixtures" / "twin_catalogo" / "catalogo_mdlc_6.json"


def test_cargar_catalogo_cuenta_los_tres_estados_del_sku():
    """MAFI vive en dos variantes de distinto peso -> las dos son 'contradicho'
    y llevan clave MAFI#<variant_id>. Sin SKU -> 'no_declarado' con clave
    id:<variant_id>. Con SKU unico -> 'declarado' con clave = sku."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert len(variantes) == 13
    assert cob.sku_contradicho == 2
    assert cob.sku_no_declarado == 8
    assert cob.sku_declarado == 3
    claves = {v.clave for v in variantes}
    assert "MAFI#47628018876704" in claves and "MAFI#47628018909472" in claves
    assert "SAS454" in claves and "ZCCC" in claves and "PINB" in claves
    assert "id:52204278513952" in claves
    assert all(v.estado_sku in (tc.ESTADO_DECLARADO, tc.ESTADO_NO_DECLARADO, tc.ESTADO_CONTRADICHO)
               for v in variantes)


def test_cargar_catalogo_funde_los_grupos_chicos_en_otros():
    """Pescados tiene 10 variantes y queda; Mariscos (1), Cerdo (1) y Otros (1)
    no llegan al minimo de 5 y se funden en 'Otros'."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert dict(cob.grupos) == {"Pescados": 10, tc.GRUPO_OTROS: 3}
    assert {v.grupo for v in variantes if v.sku == "ZCCC"} == {tc.GRUPO_OTROS}


def test_cargar_catalogo_declara_precio_y_quiebre_de_hoy():
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert cob.precio_declarado == 13 and cob.precio_excluido == 0
    assert cob.quiebre_hoy == 7   # MAFI 0.4/0.5/0.6 kg + Salmon filete x4
    mafi_1kg = next(v for v in variantes if v.clave == "MAFI#47628018909472")
    assert mafi_1kg.precio == 14450.0 and mafi_1kg.disponible is True


def test_cargar_catalogo_excluye_y_cuenta_la_variante_sin_precio(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data[0]["variants"][0]["price"] = "0"
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    variantes, cob = tc.cargar_catalogo(p)
    assert len(variantes) == 12 and cob.precio_excluido == 1


def test_cargar_catalogo_levanta_valueerror_nombrando_el_campo_que_falta(tmp_path):
    p = tmp_path / "malo.json"
    p.write_text(json.dumps([{"id": 1, "title": "x", "product_type": "Pescados"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="variants"):
        tc.cargar_catalogo(p)
```

- [ ] **Step 3: Verlos fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q`
Expected: 5 errors/fails con `ModuleNotFoundError: No module named 'jobs.twin_catalogo'`.

- [ ] **Step 4: Implementar la carga**

Crear `jobs/twin_catalogo.py`:

```python
"""Twin sembrado con el catalogo real de un negocio (fidelidad 3).

Lo observable del catalogo publico (SKU, precio, estado de stock, categoria) entra
tal cual al twin de red de ``jobs/digital_twin_job.py``; lo no observable (demanda,
plazos) entra como hipotesis declarada. Una red por grupo (``product_type`` como
proxy del proveedor), dos payloads por grupo (disponibles / quebradas hoy).

Regla de tres estados por campo: declarado / no_declarado / contradicho. Nunca dos.
Spec: docs/superpowers/specs/2026-09-16-twin-sembrado-catalogo-real-design.md
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ESTADO_DECLARADO = "declarado"
ESTADO_NO_DECLARADO = "no_declarado"
ESTADO_CONTRADICHO = "contradicho"
GRUPO_OTROS = "Otros"
MIN_VARIANTES_POR_GRUPO = 5


@dataclass(frozen=True)
class Variante:
    """Una variante del catalogo, con su clave para el twin y el estado de su SKU."""

    clave: str
    sku: str | None
    variant_id: int
    producto: str
    grupo: str
    precio: float
    disponible: bool
    estado_sku: str


@dataclass(frozen=True)
class Cobertura:
    """La tabla de tres estados que el reporte imprime siempre."""

    sku_declarado: int
    sku_no_declarado: int
    sku_contradicho: int
    precio_declarado: int
    precio_excluido: int
    quiebre_hoy: int
    grupos: tuple[tuple[str, int], ...]


def _precio(valor: object) -> float | None:
    try:
        p = float(valor)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def cargar_catalogo(
    path: str | Path, *, min_variantes_por_grupo: int = MIN_VARIANTES_POR_GRUPO,
) -> tuple[tuple[Variante, ...], Cobertura]:
    """Lee un products.json de Shopify y clasifica cada variante en tres estados."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("el catalogo debe ser una lista de productos (products.json)")
    for p in data:
        if "variants" not in p:
            raise ValueError(f"producto {p.get('id')!r} sin campo 'variants'")

    crudas: list[dict] = []
    for p in data:
        for v in p["variants"]:
            crudas.append({
                "sku": (v.get("sku") or "").strip() or None,
                "variant_id": int(v["id"]),
                "producto": str(p.get("title", "")),
                "grupo": str(p.get("product_type") or GRUPO_OTROS).strip() or GRUPO_OTROS,
                "precio": _precio(v.get("price")),
                "disponible": bool(v.get("available")),
            })

    repetidos = {s for s, n in Counter(c["sku"] for c in crudas if c["sku"]).items() if n > 1}
    tam = Counter(c["grupo"] for c in crudas)
    chicos = {g for g, n in tam.items() if n < min_variantes_por_grupo}

    variantes: list[Variante] = []
    excluidas = 0
    for c in crudas:
        if c["precio"] is None:
            excluidas += 1
            continue
        if c["sku"] is None:
            estado, clave = ESTADO_NO_DECLARADO, f"id:{c['variant_id']}"
        elif c["sku"] in repetidos:
            estado, clave = ESTADO_CONTRADICHO, f"{c['sku']}#{c['variant_id']}"
        else:
            estado, clave = ESTADO_DECLARADO, c["sku"]
        grupo = GRUPO_OTROS if c["grupo"] in chicos else c["grupo"]
        variantes.append(Variante(
            clave=clave, sku=c["sku"], variant_id=c["variant_id"], producto=c["producto"],
            grupo=grupo, precio=c["precio"], disponible=c["disponible"], estado_sku=estado,
        ))

    estados = Counter(v.estado_sku for v in variantes)
    cobertura = Cobertura(
        sku_declarado=estados[ESTADO_DECLARADO],
        sku_no_declarado=estados[ESTADO_NO_DECLARADO],
        sku_contradicho=estados[ESTADO_CONTRADICHO],
        precio_declarado=len(variantes),
        precio_excluido=excluidas,
        quiebre_hoy=sum(1 for v in variantes if not v.disponible),
        grupos=tuple(sorted(Counter(v.grupo for v in variantes).items())),
    )
    return tuple(variantes), cobertura
```

- [ ] **Step 5: Verlos pasar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q && python -m ruff check jobs/twin_catalogo.py tests/test_twin_catalogo.py`
Expected: `5 passed`, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/twin_catalogo/catalogo_mdlc_6.json jobs/twin_catalogo.py tests/test_twin_catalogo.py
git commit -m "feat(twin-catalogo): carga del catalogo real con tres estados por campo"
```

---

### Task 3: hipótesis declaradas y payloads por grupo

**Files:**
- Modify: `jobs/twin_catalogo.py` (agregar `Hipotesis`, `Escenario`, `PayloadGrupo`, `armar_payloads`)
- Modify: `tests/test_twin_catalogo.py`

**Interfaces:**
- Consumes: `Variante` (Task 2); `jobs.digital_twin_job._MAX_CELLS` y `_DISRUPTION_KINDS`.
- Produces:
  - `Escenario(nombre: str, kind: str | None, start: int = 90, duration: int = 42, magnitude: float = 2.0)` — frozen; `kind=None` es el escenario base.
  - `Hipotesis(base_demanda_por_grupo: float = 8.0, noise_cv: float = 0.25, season_amplitude: float = 0.25, season_period: int = 364, lead_proveedor: int = 7, lead_tienda: int = 1, periods: int = 364, auto_size_factor: float = 1.0)` — frozen.
  - `PayloadGrupo(grupo: str, disponibles: bool, escenario: Escenario, payload: dict)` — frozen.
  - `ESCENARIOS_ESTRENO: tuple[Escenario, ...]` con los cuatro del spec.
  - `armar_payloads(variantes, *, hipotesis: Hipotesis, escenario: Escenario, seed: int = 42) -> tuple[PayloadGrupo, ...]`.
  - `MAX_PRODUCTOS_POR_PAYLOAD = 250`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_twin_catalogo.py`:

```python
def test_armar_payloads_una_red_por_grupo_y_dos_payloads_por_grupo():
    """Pescados y Otros -> 2 grupos. Pescados tiene disponibles y quebradas -> 2
    payloads; Otros solo disponibles -> 1. Cada red: PROVEEDOR-<grupo> ->
    CD-MDLC -> TIENDA-WEB."""
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])
    assert [(p.grupo, p.disponibles) for p in pls] == [
        (tc.GRUPO_OTROS, True), ("Pescados", False), ("Pescados", True),
    ]
    pesc = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    assert [n["name"] for n in pesc.payload["nodes"]] == ["PROVEEDOR-Pescados", "CD-MDLC", "TIENDA-WEB"]
    assert [n["kind"] for n in pesc.payload["nodes"]] == ["supplier", "dc", "store"]
    assert pesc.payload["nodes"][1]["lead_time"] == 7 and pesc.payload["nodes"][2]["lead_time"] == 1
    assert pesc.payload["periods"] == 364 and pesc.payload["auto_size_factor"] == 1.0


def test_armar_payloads_las_quebradas_de_hoy_arrancan_vacias():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])
    quebradas = next(p for p in pls if p.grupo == "Pescados" and not p.disponibles)
    sanas = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    assert [n.get("initial_on_hand") for n in quebradas.payload["nodes"]] == [None, 0.0, 0.0]
    assert [n.get("initial_on_hand") for n in sanas.payload["nodes"]] == [None, None, None]
    assert {p["product_id"] for p in quebradas.payload["products"]} == {
        "id:50967838097696", "MAFI#47628018876704", "id:50967838130464",
        "id:51910435635488", "id:51910435799328", "id:51910435832096", "id:51910435864864",
    }


def test_armar_payloads_la_demanda_es_un_prior_declarado_y_determinista():
    """Misma semilla -> mismos payloads; distinta semilla -> distintas bases.
    La base media por grupo es la de la hipotesis (escala ABC lognormal)."""
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    hip = tc.Hipotesis(base_demanda_por_grupo=8.0)
    a = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=1)
    b = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=1)
    c = tc.armar_payloads(variantes, hipotesis=hip, escenario=tc.ESCENARIOS_ESTRENO[0], seed=2)
    assert [p.payload for p in a] == [p.payload for p in b]
    assert [p.payload["products"] for p in a] != [p.payload["products"] for p in c]
    bases = [p["base"] for p in a[2].payload["products"]]
    assert all(x > 0 for x in bases)
    assert a[2].payload["demand"]["noise_cv"] == 0.25 and a[2].payload["demand"]["season_period"] == 364


def test_armar_payloads_el_corte_de_proveedor_apunta_al_proveedor_del_grupo():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    corte = next(e for e in tc.ESCENARIOS_ESTRENO if e.kind == "supplier_outage")
    pls = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=corte)
    pesc = next(p for p in pls if p.grupo == "Pescados" and p.disponibles)
    d = pesc.payload["disruptions"]
    assert d == [{"kind": "supplier_outage", "target": "PROVEEDOR-Pescados", "start": 90,
                  "duration": 42, "magnitude": 2.0}]
    pico = next(e for e in tc.ESCENARIOS_ESTRENO if e.kind == "demand_surge")
    pls2 = tc.armar_payloads(variantes, hipotesis=tc.Hipotesis(), escenario=pico)
    assert pls2[2].payload["disruptions"][0]["target"] == "TIENDA-WEB"


def test_armar_payloads_respeta_el_techo_de_productos():
    variantes, _ = tc.cargar_catalogo(FIXTURE)
    muchas = tuple(
        tc.Variante(clave=f"X{i}", sku=f"X{i}", variant_id=i, producto="x", grupo="Pescados",
                    precio=1.0, disponible=True, estado_sku=tc.ESTADO_DECLARADO)
        for i in range(251)
    )
    with pytest.raises(ValueError, match="250"):
        tc.armar_payloads(muchas, hipotesis=tc.Hipotesis(), escenario=tc.ESCENARIOS_ESTRENO[0])
```

- [ ] **Step 2: Verlos fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q -k armar_payloads`
Expected: 5 failed con `AttributeError: module 'jobs.twin_catalogo' has no attribute 'Hipotesis'` (o `armar_payloads`).

- [ ] **Step 3: Implementar**

Agregar a `jobs/twin_catalogo.py` (después de `Cobertura`; sumar `import numpy as np` y `from jobs.digital_twin_job import _DISRUPTION_KINDS, _MAX_CELLS` a los imports):

```python
MAX_PRODUCTOS_POR_PAYLOAD = 250
NODO_CD = "CD-MDLC"
NODO_TIENDA = "TIENDA-WEB"


@dataclass(frozen=True)
class Escenario:
    """Un escenario del nucleo; kind=None es la base sin disrupcion."""

    nombre: str
    kind: str | None
    start: int = 90
    duration: int = 42
    magnitude: float = 2.0


ESCENARIOS_ESTRENO: tuple[Escenario, ...] = (
    Escenario("base", None),
    Escenario("corte_proveedor", "supplier_outage", start=90, duration=42),
    Escenario("plazo_estirado", "lead_time_spike", start=90, duration=30, magnitude=5.0),
    Escenario("pico_demanda", "demand_surge", start=90, duration=14, magnitude=2.0),
)


@dataclass(frozen=True)
class Hipotesis:
    """Lo no observable, declarado. Todo esto se imprime en el reporte."""

    base_demanda_por_grupo: float = 8.0   # unidades/dia por variante, antes de la escala ABC
    noise_cv: float = 0.25
    season_amplitude: float = 0.25
    season_period: int = 364
    lead_proveedor: int = 7               # dias proveedor -> CD
    lead_tienda: int = 1                  # dias CD -> tienda
    periods: int = 364                    # periodo = dia
    auto_size_factor: float = 1.0


@dataclass(frozen=True)
class PayloadGrupo:
    grupo: str
    disponibles: bool
    escenario: Escenario
    payload: dict


def _nodos(grupo: str, hip: Hipotesis, *, vacios: bool) -> list[dict]:
    inicial = 0.0 if vacios else None
    return [
        {"name": f"PROVEEDOR-{grupo}", "kind": "supplier", "supplier": None,
         "lead_time": 0, "review_period": 1, "capacity": None, "initial_on_hand": None},
        {"name": NODO_CD, "kind": "dc", "supplier": f"PROVEEDOR-{grupo}",
         "lead_time": hip.lead_proveedor, "review_period": 1, "capacity": None,
         "initial_on_hand": inicial},
        {"name": NODO_TIENDA, "kind": "store", "supplier": NODO_CD,
         "lead_time": hip.lead_tienda, "review_period": 1, "capacity": None,
         "initial_on_hand": inicial},
    ]


def _disrupciones(grupo: str, esc: Escenario) -> list[dict]:
    if esc.kind is None:
        return []
    if esc.kind not in _DISRUPTION_KINDS:
        raise ValueError(f"escenario desconocido: {esc.kind!r}")
    target = {
        "supplier_outage": f"PROVEEDOR-{grupo}",
        "lead_time_spike": NODO_CD,
        "demand_surge": NODO_TIENDA,
    }[esc.kind]
    return [{"kind": esc.kind, "target": target, "start": esc.start,
             "duration": esc.duration, "magnitude": esc.magnitude}]


def _productos(variantes: list[Variante], hip: Hipotesis, seed: int) -> list[dict]:
    # Mismo mecanismo que digital_twin_job.prepare: escala ABC lognormal ordenada,
    # determinista por semilla. Es un prior, no una observacion.
    rng = np.random.default_rng(seed)
    factores = np.sort(rng.lognormal(mean=0.0, sigma=0.7, size=len(variantes)))[::-1]
    return [
        {"product_id": v.clave, "base": round(hip.base_demanda_por_grupo * float(f), 2)}
        for v, f in zip(variantes, factores)
    ]


def armar_payloads(
    variantes: tuple[Variante, ...] | list[Variante], *,
    hipotesis: Hipotesis, escenario: Escenario, seed: int = 42,
) -> tuple[PayloadGrupo, ...]:
    """Una red por grupo; dos payloads por grupo (disponibles / quebradas), sin vacios."""
    por_grupo: dict[str, list[Variante]] = {}
    for v in variantes:
        por_grupo.setdefault(v.grupo, []).append(v)
    demanda = {
        "trend": 0.0, "season_amplitude": hipotesis.season_amplitude,
        "season_period": hipotesis.season_period, "promo_every": 0, "promo_length": 1,
        "promo_uplift": 0.0, "noise_cv": hipotesis.noise_cv, "zero_prob": 0.0,
    }
    salida: list[PayloadGrupo] = []
    for i, grupo in enumerate(sorted(por_grupo)):
        for disponibles in (False, True):
            lote = [v for v in por_grupo[grupo] if v.disponible is disponibles]
            if not lote:
                continue
            if len(lote) > MAX_PRODUCTOS_POR_PAYLOAD:
                raise ValueError(
                    f"grupo {grupo!r}: {len(lote)} productos supera el techo de "
                    f"{MAX_PRODUCTOS_POR_PAYLOAD} por payload"
                )
            if len(lote) * 1 * hipotesis.periods > _MAX_CELLS:
                raise ValueError(f"grupo {grupo!r}: supera _MAX_CELLS ({_MAX_CELLS:,})")
            salida.append(PayloadGrupo(
                grupo=grupo, disponibles=disponibles, escenario=escenario,
                payload={
                    "nodes": _nodos(grupo, hipotesis, vacios=not disponibles),
                    "products": _productos(lote, hipotesis, seed + 1000 * i + int(disponibles)),
                    "periods": hipotesis.periods, "demand": demanda,
                    "disruptions": _disrupciones(grupo, escenario), "seed": seed,
                    "auto_size_factor": hipotesis.auto_size_factor,
                },
            ))
    return tuple(salida)
```

- [ ] **Step 4: Verlos pasar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q && python -m ruff check jobs/twin_catalogo.py tests/test_twin_catalogo.py`
Expected: `10 passed`, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add jobs/twin_catalogo.py tests/test_twin_catalogo.py
git commit -m "feat(twin-catalogo): hipotesis declaradas y una red por grupo, con las quebradas arrancando vacias"
```

---

### Task 4: correr y consolidar, valorizando a precio de lista

**Files:**
- Modify: `jobs/twin_catalogo.py` (agregar `ResultadoGrupo`, `VarianteGolpeada`, `CatalogTwinReport`, `correr`, `consolidar`)
- Modify: `tests/test_twin_catalogo.py`

**Interfaces:**
- Consumes: `jobs.digital_twin_job.run(payload) -> TwinReport` con `served_rows` (Task 1); `PayloadGrupo`, `Variante`, `Cobertura`, `Hipotesis` (Tasks 2-3).
- Produces:
  - `ResultadoGrupo(grupo: str, disponibles: bool, escenario: Escenario, report: TwinReport)` — frozen.
  - `VarianteGolpeada(clave: str, producto: str, estado_sku: str, unidades_perdidas: float, venta_en_riesgo_clp: float)` — frozen.
  - `CatalogTwinReport(escenario: Escenario, hipotesis: Hipotesis, cobertura: Cobertura, fill_por_grupo: tuple[tuple[str, float], ...], dias_quiebre_por_grupo: tuple[tuple[str, int], ...], venta_en_riesgo_clp: float, venta_en_riesgo_por_grupo: tuple[tuple[str, float], ...], golpeadas: tuple[VarianteGolpeada, ...], demand_rows, served_rows, inventory_rows, order_rows)` — frozen; las cuatro `*_rows` con la forma del twin.
  - `correr(payloads) -> tuple[ResultadoGrupo, ...]`.
  - `consolidar(resultados, variantes, *, cobertura, hipotesis, top: int = 10) -> CatalogTwinReport`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_twin_catalogo.py`:

```python
def _reporte(escenario=None, seed=7):
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    hip = tc.Hipotesis(periods=120)
    esc = escenario or tc.ESCENARIOS_ESTRENO[0]
    pls = tc.armar_payloads(variantes, hipotesis=hip, escenario=esc, seed=seed)
    res = tc.correr(pls)
    return tc.consolidar(res, variantes, cobertura=cob, hipotesis=hip), variantes


def test_consolidar_valoriza_a_precio_de_lista_y_solo_eso():
    """venta_en_riesgo = sum(unidades no servidas x precio de la variante).
    Se verifica a mano contra las filas del twin: si se cambiara el precio por
    otro campo, o se sumara algo mas, este numero no cuadra."""
    rep, variantes = _reporte()
    precio = {v.clave: v.precio for v in variantes}
    demand = {(t, p, s): u for t, p, s, u in rep.demand_rows}
    a_mano = sum(
        (demand[(t, p, s)] - u) * precio[p] for t, p, s, u in rep.served_rows
    )
    assert rep.venta_en_riesgo_clp == pytest.approx(a_mano, rel=1e-6)
    assert rep.venta_en_riesgo_clp >= 0.0
    assert dict(rep.venta_en_riesgo_por_grupo).keys() == {"Pescados", tc.GRUPO_OTROS}


def test_consolidar_reporta_fill_y_quiebre_por_grupo_y_las_mas_golpeadas():
    rep, _ = _reporte(tc.ESCENARIOS_ESTRENO[1])   # corte de proveedor
    fills = dict(rep.fill_por_grupo)
    assert set(fills) == {"Pescados", tc.GRUPO_OTROS}
    assert all(0.0 <= f <= 1.0 for f in fills.values())
    assert all(d >= 0 for _, d in rep.dias_quiebre_por_grupo)
    assert 1 <= len(rep.golpeadas) <= 10
    assert rep.golpeadas == tuple(sorted(rep.golpeadas, key=lambda g: -g.venta_en_riesgo_clp))
    assert all(g.estado_sku in (tc.ESTADO_DECLARADO, tc.ESTADO_NO_DECLARADO, tc.ESTADO_CONTRADICHO)
               for g in rep.golpeadas)


def test_consolidar_es_determinista_por_semilla():
    a, _ = _reporte(seed=3)
    b, _ = _reporte(seed=3)
    c, _ = _reporte(seed=4)
    assert a.venta_en_riesgo_clp == b.venta_en_riesgo_clp and a.fill_por_grupo == b.fill_por_grupo
    assert (a.venta_en_riesgo_clp, a.fill_por_grupo) != (c.venta_en_riesgo_clp, c.fill_por_grupo)


def test_consolidar_las_filas_llevan_la_clave_real_de_la_variante():
    rep, variantes = _reporte()
    claves = {v.clave for v in variantes}
    assert {p for _, p, _, _ in rep.demand_rows} == claves
    assert {p for _, p, _, _ in rep.inventory_rows} <= claves
```

- [ ] **Step 2: Verlos fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q -k consolidar`
Expected: 4 failed con `AttributeError: module 'jobs.twin_catalogo' has no attribute 'correr'`.

- [ ] **Step 3: Implementar**

Agregar a `jobs/twin_catalogo.py` (sumar `from jobs import digital_twin_job` y `from jobs.digital_twin_job import TwinReport` a los imports):

```python
@dataclass(frozen=True)
class ResultadoGrupo:
    grupo: str
    disponibles: bool
    escenario: Escenario
    report: TwinReport


@dataclass(frozen=True)
class VarianteGolpeada:
    clave: str
    producto: str
    estado_sku: str
    unidades_perdidas: float
    venta_en_riesgo_clp: float


@dataclass(frozen=True)
class CatalogTwinReport:
    """Un escenario, todos los grupos, valorizado a precio de lista."""

    escenario: Escenario
    hipotesis: Hipotesis
    cobertura: Cobertura
    fill_por_grupo: tuple[tuple[str, float], ...]
    dias_quiebre_por_grupo: tuple[tuple[str, int], ...]
    venta_en_riesgo_clp: float
    venta_en_riesgo_por_grupo: tuple[tuple[str, float], ...]
    golpeadas: tuple[VarianteGolpeada, ...]
    demand_rows: tuple[tuple[int, str, str, float], ...]
    served_rows: tuple[tuple[int, str, str, float], ...]
    inventory_rows: tuple[tuple[int, str, str, float], ...]
    order_rows: tuple[tuple[int, str, str, float], ...]


def correr(payloads: tuple[PayloadGrupo, ...]) -> tuple[ResultadoGrupo, ...]:
    """Cada payload por el run() del twin, sin cambios."""
    return tuple(
        ResultadoGrupo(grupo=p.grupo, disponibles=p.disponibles, escenario=p.escenario,
                       report=digital_twin_job.run(p.payload))
        for p in payloads
    )


def consolidar(
    resultados: tuple[ResultadoGrupo, ...], variantes: tuple[Variante, ...] | list[Variante], *,
    cobertura: Cobertura, hipotesis: Hipotesis, top: int = 10,
) -> CatalogTwinReport:
    if not resultados:
        raise ValueError("sin resultados que consolidar")
    por_clave = {v.clave: v for v in variantes}
    escenario = resultados[0].escenario

    demand: list = []
    served: list = []
    inventory: list = []
    orders: list = []
    demandado: dict[str, float] = {}
    servido: dict[str, float] = {}
    quiebre_grupo: dict[str, int] = {}
    perdidas: dict[str, float] = {}
    for r in resultados:
        demand.extend(r.report.demand_rows)
        served.extend(r.report.served_rows)
        inventory.extend(r.report.inventory_rows)
        orders.extend(r.report.order_rows)
        quiebre_grupo[r.grupo] = quiebre_grupo.get(r.grupo, 0) + sum(
            k.stockout_periods for k in r.report.node_kpis if k.kind == "store"
        )
    dem_idx = {(t, p, s): u for t, p, s, u in demand}
    for t, p, s, u in served:
        falta = max(dem_idx[(t, p, s)] - u, 0.0)
        perdidas[p] = perdidas.get(p, 0.0) + falta
        g = por_clave[p].grupo
        demandado[g] = demandado.get(g, 0.0) + dem_idx[(t, p, s)]
        servido[g] = servido.get(g, 0.0) + u

    riesgo_var = {p: perdidas[p] * por_clave[p].precio for p in perdidas}
    riesgo_grupo: dict[str, float] = {}
    for p, clp in riesgo_var.items():
        g = por_clave[p].grupo
        riesgo_grupo[g] = riesgo_grupo.get(g, 0.0) + clp
    fill = {
        g: (servido[g] / demandado[g] if demandado[g] > 0 else 1.0) for g in demandado
    }
    golpeadas = tuple(
        VarianteGolpeada(clave=p, producto=por_clave[p].producto,
                         estado_sku=por_clave[p].estado_sku,
                         unidades_perdidas=round(perdidas[p], 2),
                         venta_en_riesgo_clp=round(riesgo_var[p], 0))
        for p in sorted(riesgo_var, key=lambda k: -riesgo_var[k])[:top]
        if riesgo_var[p] > 0
    )
    return CatalogTwinReport(
        escenario=escenario, hipotesis=hipotesis, cobertura=cobertura,
        fill_por_grupo=tuple(sorted((g, round(f, 4)) for g, f in fill.items())),
        dias_quiebre_por_grupo=tuple(sorted(quiebre_grupo.items())),
        venta_en_riesgo_clp=round(sum(riesgo_var.values()), 0),
        venta_en_riesgo_por_grupo=tuple(sorted((g, round(c, 0)) for g, c in riesgo_grupo.items())),
        golpeadas=golpeadas,
        demand_rows=tuple(demand), served_rows=tuple(served),
        inventory_rows=tuple(inventory), order_rows=tuple(orders),
    )
```

Nota para el implementador: el test de valorización compara contra `round` de suma de todo; si `venta_en_riesgo_clp` redondeado a 0 decimales no cuadra con `rel=1e-6` en un caso chico, quitar el `round(..., 0)` del total (dejarlo como float exacto) — el test manda, no el redondeo cosmético.

- [ ] **Step 4: Verlos pasar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q && python -m ruff check jobs/twin_catalogo.py tests/test_twin_catalogo.py`
Expected: `14 passed`, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add jobs/twin_catalogo.py tests/test_twin_catalogo.py
git commit -m "feat(twin-catalogo): correr por grupo y consolidar con venta en riesgo a precio de lista"
```

---

### Task 5: deck y archivos de salida (con la cobertura y las hipótesis siempre impresas)

**Files:**
- Modify: `jobs/twin_catalogo.py` (agregar `deck`, `escribir`)
- Modify: `tests/test_twin_catalogo.py`

**Interfaces:**
- Consumes: `CatalogTwinReport` (Task 4); `src.deliverable.Deliverable/Finding/Kpi/DataSource` y `Deliverable.to_markdown()`; `src.export.write_summary_csv(rows: list[dict], path) -> Path`.
- Produces:
  - `deck(report: CatalogTwinReport, *, client: str = "Mariscos De La Concha") -> Deliverable`.
  - `escribir(report: CatalogTwinReport, out_dir: str | Path, *, client: str = "Mariscos De La Concha") -> dict[str, Path]` con claves `demand_history`, `served`, `inventory`, `orders`, `cobertura`, `golpeadas`, `deck`.
  - `FRASE_APERTURA = "Esto es una hipótesis sobre su catálogo, no una medición de su bodega."`

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_deck_abre_con_la_frase_de_hipotesis_y_declara_cobertura_y_priors():
    rep, _ = _reporte()
    d = tc.deck(rep)
    md = d.to_markdown()
    assert md.startswith("# ") and tc.FRASE_APERTURA in d.summary
    assert "Hipótesis de demanda (no observadas)" in md
    assert "declarado" in md and "no_declarado" in md and "contradicho" in md
    assert "venta en riesgo a precio de lista" in md.lower()
    assert "proveedor: no declarado" in md.lower()


def test_escribir_deja_los_csv_del_twin_con_fecha_diaria_y_clave_real(tmp_path):
    rep, variantes = _reporte()
    paths = tc.escribir(rep, tmp_path)
    assert set(paths) == {"demand_history", "served", "inventory", "orders", "cobertura", "golpeadas", "deck"}
    import pandas as pd
    dem = pd.read_csv(paths["demand_history"])
    assert list(dem.columns) == ["date", "product_id", "location", "units"]
    assert dem["date"].iloc[0] == "2025-01-06" and dem["date"].iloc[1] == "2025-01-07"
    assert set(dem["product_id"]) == {v.clave for v in variantes}
    assert paths["deck"].read_text(encoding="utf-8").startswith("# ")
```

- [ ] **Step 2: Verlos fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q -k "deck or escribir"`
Expected: 2 failed con `AttributeError: ... has no attribute 'deck'`.

- [ ] **Step 3: Implementar**

Agregar a `jobs/twin_catalogo.py` (sumar `import pandas as pd`, `from src.deliverable import DataSource, Deliverable, Finding, Kpi`, `from src.export import write_summary_csv`):

```python
FRASE_APERTURA = "Esto es una hipótesis sobre su catálogo, no una medición de su bodega."
_FECHA_INICIO = "2025-01-06"


def _tabla_cobertura(c: Cobertura) -> str:
    return (
        "| Campo | declarado | no_declarado | contradicho |\n|---|---:|---:|---:|\n"
        f"| SKU | {c.sku_declarado} | {c.sku_no_declarado} | {c.sku_contradicho} |\n"
        f"| precio | {c.precio_declarado} | {c.precio_excluido} (excluidas) | 0 |\n"
        f"| en quiebre hoy | {c.quiebre_hoy} | 0 | 0 |\n"
        "| proveedor | 0 | todos (proveedor: no declarado; agrupado por categoría) | 0 |\n"
        "| demanda | 0 | todos (prior) | 0 |\n"
        "| lead time | 0 | todos (prior) | 0 |\n"
        "| peso / unidad | 0 | todos (la unidad es la variante) | 0 |"
    )


def _seccion_hipotesis(h: Hipotesis) -> str:
    return (
        "## Hipótesis de demanda (no observadas)\n\n"
        f"- demanda base por grupo: {h.base_demanda_por_grupo} unidades/día, escala ABC lognormal (σ 0,7)\n"
        f"- ruido (CV): {h.noise_cv} · estacionalidad: ±{h.season_amplitude:.0%} cada {h.season_period} días\n"
        f"- lead time proveedor → CD: {h.lead_proveedor} días (no declarado — prior) · CD → tienda: {h.lead_tienda} día(s)\n"
        f"- horizonte: {h.periods} días · dimensionado automático: {h.auto_size_factor}×"
    )


def deck(report: CatalogTwinReport, *, client: str = "Mariscos De La Concha") -> Deliverable:
    esc = report.escenario
    fills = dict(report.fill_por_grupo)
    peor = min(fills, key=fills.get)
    summary = (
        f"{FRASE_APERTURA} Escenario '{esc.nombre}' sobre {report.cobertura.precio_declarado} "
        f"variantes en {len(fills)} grupo(s), {report.hipotesis.periods} días: venta en riesgo a "
        f"precio de lista CLP {report.venta_en_riesgo_clp:,.0f}; el grupo más golpeado es "
        f"{peor} con fill {fills[peor]:.1%}."
    )
    findings = [
        Finding("Cobertura de lo observable", _tabla_cobertura(report.cobertura),
                impact="lo que entró del catálogo tal cual, y lo que es hipótesis"),
        Finding("Hipótesis", _seccion_hipotesis(report.hipotesis),
                impact="cambiar cualquiera cambia el número; por eso están impresas"),
    ]
    for g in report.golpeadas[:5]:
        findings.append(Finding(
            f"{g.producto} ({g.clave}, SKU {g.estado_sku})",
            f"{g.unidades_perdidas:,.0f} unidades no servidas · CLP {g.venta_en_riesgo_clp:,.0f}",
            impact="venta en riesgo a precio de lista; no es margen ni pérdida contable",
        ))
    kpis = [
        Kpi("Venta en riesgo a precio de lista (CLP)", f"{report.venta_en_riesgo_clp:,.0f}",
            rationale="unidades no servidas × precio de lista de la variante"),
        *[Kpi(f"Fill {g}", f"{f:.1%}", target="≥ 95%") for g, f in report.fill_por_grupo],
        *[Kpi(f"Días-tienda en quiebre {g}", str(d)) for g, d in report.dias_quiebre_por_grupo],
    ]
    fuentes = [
        DataSource("SKU, precio, estado de stock, categoría", "products.json público del catálogo", "lectura 2026-08-28"),
        DataSource("demanda, lead times", "prior declarado (no observado)", "por corrida"),
    ]
    return Deliverable(
        title=f"Twin del catálogo — {client} — escenario {esc.nombre}",
        client=client, summary=summary, findings=tuple(findings), kpis=tuple(kpis),
        data_sources=tuple(fuentes),
        recommendations=(
            "Validar la demanda base con un mes de ventas reales antes de usar cualquier número de este deck.",
            "Si el corte de proveedor domina, la decisión es cobertura de stock del grupo, no de una variante.",
        ),
        confidence=0.3,
        residual="Proveedor real, demanda y plazos no observados; los quiebres publicados son estado de publicación, no inventario.",
    )


def _con_fecha(rows, value_col: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["period", "product_id", "location", value_col])
    inicio = pd.Timestamp(_FECHA_INICIO)
    df.insert(0, "date", df.pop("period").map(lambda t: (inicio + pd.Timedelta(days=int(t))).date().isoformat()))
    return df


def escribir(report: CatalogTwinReport, out_dir: str | Path, *, client: str = "Mariscos De La Concha") -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, col, rows in (
        ("demand_history", "units", report.demand_rows),
        ("served", "units_served", report.served_rows),
        ("inventory", "on_hand", report.inventory_rows),
        ("orders", "order_qty", report.order_rows),
    ):
        p = d / f"twin_{key}.csv"
        _con_fecha(rows, col).to_csv(p, index=False)
        paths[key] = p
    c = report.cobertura
    paths["cobertura"] = write_summary_csv([
        {"campo": "sku", "declarado": c.sku_declarado, "no_declarado": c.sku_no_declarado, "contradicho": c.sku_contradicho},
        {"campo": "precio", "declarado": c.precio_declarado, "no_declarado": c.precio_excluido, "contradicho": 0},
        {"campo": "quiebre_hoy", "declarado": c.quiebre_hoy, "no_declarado": 0, "contradicho": 0},
    ], d / "twin_cobertura.csv")
    paths["golpeadas"] = write_summary_csv([
        {"clave": g.clave, "producto": g.producto, "estado_sku": g.estado_sku,
         "unidades_perdidas": g.unidades_perdidas, "venta_en_riesgo_clp": g.venta_en_riesgo_clp}
        for g in report.golpeadas
    ], d / "twin_golpeadas.csv")
    deck_path = d / "twin_deck.md"
    deck_path.write_text(deck(report, client=client).to_markdown(), encoding="utf-8")
    paths["deck"] = deck_path
    return paths
```

Nota: si `Deliverable.to_markdown()` no imprime `findings[].detail` con saltos de línea (tabla), el test de "declarado/no_declarado/contradicho" puede fallar por formato; en ese caso incluir la tabla de cobertura también en `summary` no es aceptable (el summary es una frase) — mover la tabla a un `Finding` por fila (una `Finding` por campo con detail "declarado=N · no_declarado=M · contradicho=K"). El test manda.

- [ ] **Step 4: Verlos pasar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q && python -m ruff check jobs/twin_catalogo.py tests/test_twin_catalogo.py`
Expected: `16 passed`, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add jobs/twin_catalogo.py tests/test_twin_catalogo.py
git commit -m "feat(twin-catalogo): deck con cobertura e hipotesis impresas, y los CSV del twin con fecha diaria"
```

---

### Task 6: el example, y la corrida sobre el catálogo real

**Files:**
- Create: `examples/run_twin_catalogo.py`
- Modify: `tests/test_twin_catalogo.py`

**Interfaces:**
- Consumes: todo `jobs.twin_catalogo`.
- Produces: CLI `python examples/run_twin_catalogo.py --catalogo <json> [--grupo Pescados] [--escenario base|corte_proveedor|plazo_estirado|pico_demanda|todos] [--out deliverables/twin_catalogo] [--seed 42] [--base-demanda 8] [--periodos 364]`; función `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_example_corre_los_cuatro_escenarios_sobre_la_fixture(tmp_path, capsys):
    import examples.run_twin_catalogo as cli
    rc = cli.main(["--catalogo", str(FIXTURE), "--grupo", "Pescados", "--escenario", "todos",
                   "--out", str(tmp_path), "--periodos", "60", "--seed", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    for nombre in ("base", "corte_proveedor", "plazo_estirado", "pico_demanda"):
        assert nombre in out
        assert (tmp_path / nombre / "twin_deck.md").exists()
    assert "venta en riesgo" in out.lower()
```

- [ ] **Step 2: Verlo fallar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q -k example`
Expected: `ModuleNotFoundError: No module named 'examples.run_twin_catalogo'`.

- [ ] **Step 3: Implementar**

Crear `examples/run_twin_catalogo.py`:

```python
"""Twin sembrado con el catalogo real de un negocio: un grupo, cuatro escenarios.

    python examples/run_twin_catalogo.py --catalogo radiografia-mdlc-catalogo-2026-08-28.json
    python examples/run_twin_catalogo.py --catalogo cat.json --grupo Pescados --escenario corte_proveedor

Lo observable (SKU, precio, quiebre, categoria) entra tal cual; demanda y plazos son
priors declarados y se imprimen. Nada de esto mide la bodega de nadie.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobs import twin_catalogo as tc  # noqa: E402


def _escenarios(nombre: str) -> tuple[tc.Escenario, ...]:
    if nombre == "todos":
        return tc.ESCENARIOS_ESTRENO
    por_nombre = {e.nombre: e for e in tc.ESCENARIOS_ESTRENO}
    if nombre not in por_nombre:
        raise SystemExit(f"escenario desconocido: {nombre!r}; opciones: {', '.join(por_nombre)}, todos")
    return (por_nombre[nombre],)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogo", required=True, help="products.json de Shopify")
    ap.add_argument("--grupo", default="Pescados", help="product_type a simular (o 'todos')")
    ap.add_argument("--escenario", default="todos")
    ap.add_argument("--out", default="deliverables/twin_catalogo")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--base-demanda", type=float, default=8.0, dest="base_demanda")
    ap.add_argument("--periodos", type=int, default=364)
    ap.add_argument("--cliente", default="Mariscos De La Concha")
    args = ap.parse_args(argv)

    variantes, cobertura = tc.cargar_catalogo(args.catalogo)
    if args.grupo != "todos":
        variantes = tuple(v for v in variantes if v.grupo == args.grupo)
        if not variantes:
            raise SystemExit(f"el grupo {args.grupo!r} no existe; hay: {', '.join(g for g, _ in cobertura.grupos)}")
    hip = tc.Hipotesis(base_demanda_por_grupo=args.base_demanda, periods=args.periodos)

    print(f"{'escenario':18} {'fill':>7} {'dias quiebre':>13} {'venta en riesgo CLP':>20}")
    for esc in _escenarios(args.escenario):
        pls = tc.armar_payloads(variantes, hipotesis=hip, escenario=esc, seed=args.seed)
        rep = tc.consolidar(tc.correr(pls), variantes, cobertura=cobertura, hipotesis=hip)
        tc.escribir(rep, Path(args.out) / esc.nombre, client=args.cliente)
        fill = dict(rep.fill_por_grupo)
        dias = dict(rep.dias_quiebre_por_grupo)
        for g in sorted(fill):
            print(f"{esc.nombre:18} {fill[g]:7.1%} {dias[g]:13d} {dict(rep.venta_en_riesgo_por_grupo)[g]:20,.0f}  [{g}]")
    print(f"\ncobertura: sku declarado={cobertura.sku_declarado} no_declarado={cobertura.sku_no_declarado} "
          f"contradicho={cobertura.sku_contradicho} · en quiebre hoy={cobertura.quiebre_hoy}")
    print(f"salida en {args.out}/<escenario>/ (twin_deck.md, twin_*.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verlo pasar**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py -q && python -m ruff check examples/run_twin_catalogo.py`
Expected: `17 passed`, `All checks passed!`

- [ ] **Step 5: Corrida real sobre el catálogo completo (evidencia para el PR)**

Run (desde la raíz del worktree):

```bash
PYTHONPATH=. python examples/run_twin_catalogo.py --catalogo "C:/Users/Gamer/Music/scm/radiografia-mdlc-catalogo-2026-08-28.json" --grupo Pescados --escenario todos --out deliverables/twin_catalogo_mdlc
```

Expected: una tabla de 4 filas (base, corte_proveedor, plazo_estirado, pico_demanda) con fill entre 0 y 1, el corte de proveedor con el fill más bajo, y la línea de cobertura `sku declarado=... no_declarado=57 contradicho=2 · en quiebre hoy=63` sobre las 216 variantes (con `--grupo todos`). Guardar la salida de consola en el cuerpo del PR. `deliverables/` está en `.gitignore`.

- [ ] **Step 6: Commit**

```bash
git add examples/run_twin_catalogo.py tests/test_twin_catalogo.py
git commit -m "feat(twin-catalogo): example de un grupo bajo cuatro escenarios"
```

---

### Task 7: cierre — suite, spec en estado final, PR

**Files:**
- Modify: `docs/superpowers/specs/2026-09-16-twin-sembrado-catalogo-real-design.md` (línea de Estado)

- [ ] **Step 1: Suite relacionada y lint**

Run: `PYTHONPATH=. python -m pytest tests/test_twin_catalogo.py tests/test_digital_twin_tool.py tests/test_digital_twin.py tests/test_scm_agent.py tests/test_capability_smoke.py -q && python -m ruff check .`
Expected: todo `passed` (los dos últimos confirman que el registry no cambió: siguen los mismos conteos), `All checks passed!`

- [ ] **Step 2: Mutación mínima del spec (a mano, sin commitear)**

Quitar temporalmente `initial_on_hand=inicial` en `_nodos` (poner `None`) y correr `pytest tests/test_twin_catalogo.py -k quebradas` → debe fallar. Restaurar. Cambiar `por_clave[p].precio` por `1.0` en `consolidar` y correr `-k valoriza` → debe fallar. Restaurar. Confirmar `git status` limpio salvo el spec.

- [ ] **Step 3: Estado del spec**

Cambiar la línea `- Estado: borrador. ...` por `- Estado: implementado en la rama feat/twin-catalogo-real (plan docs/superpowers/plans/2026-09-16-twin-sembrado-catalogo-real.md).`

- [ ] **Step 4: Commit y PR**

```bash
git add docs/superpowers/specs/2026-09-16-twin-sembrado-catalogo-real-design.md
git commit -m "docs(twin-catalogo): spec en estado implementado"
git push -u origin feat/twin-catalogo-real
gh pr create --base main --title "feat(twin-catalogo): el twin sembrado con el catálogo real de un negocio" --body-file <cuerpo con la tabla de la Task 6 Step 5, la cobertura y la frase de apertura>
```

La CI no corre hasta el 1-oct: la evidencia (suite local, tabla de la corrida real) va en el cuerpo del PR.

---

## Self-review (hecho al escribir)

- **Cobertura del spec:** decisión 1 (grupo = red, proxy declarado) → Task 3 `_nodos`/`armar_payloads` + Task 5 tabla "proveedor: no declarado"; decisión 2 (tres estados) → Task 2 `cargar_catalogo` + Task 5 `_tabla_cobertura`; decisión 3 (demanda prior) → Task 3 `_productos` + Task 5 `_seccion_hipotesis`; decisión 4 (lead times) → `Hipotesis`; decisión 5 (quebradas vacías) → Task 1 reenvío + Task 3 `vacios`; decisión 6 (escenarios) → `ESCENARIOS_ESTRENO`, `auto_size_factor` 1,0; decisión 7 (venta en riesgo) → Task 4 `consolidar` + Task 5 rótulo; decisión 8 (salida) → Task 4/5; decisión 9 (guardas) → Task 2 `ValueError` de `variants`, `_precio`, `GRUPO_OTROS`; Task 3 techos. "Lo que NO se promete" → `FRASE_APERTURA`, `residual`, sin vedas, sin kg. Pruebas 1-7 del spec → Tasks 2-6.
- **Desvío respecto del spec, ya asumido:** `jobs/digital_twin_job.py` SÍ se toca, en dos puntos aditivos y compatibles (Task 1); el spec queda corregido en la misma rama. Y los CSV con fecha diaria se escriben en el módulo nuevo en vez de reutilizar `write_operational` (que fecha por semanas).
- **Consistencia de tipos:** `Variante.clave` es la `product_id` en todos los payloads, filas y `golpeadas`; `served_rows` tiene la misma forma que `demand_rows` en Task 1, 4 y 5; `Escenario.kind` usa los literales de `_DISRUPTION_KINDS`.
- **Placeholders:** ninguno.
