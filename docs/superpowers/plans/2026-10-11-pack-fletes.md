# Pack `fletes` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un pack `packs/fletes/` sobre `nosilo_core` que lee un CSV de envíos del cliente y devuelve, por ruta, la matriz transportista × ruta con puntaje costo/servicio desglosado y una recomendación de reasignación con su ahorro.

**Architecture:** Tres módulos con una responsabilidad cada uno: `loader.py` (CSV → `Envio` tipados, validación estricta), `engine.py` (puro, sin reloj, `Decimal`: agrupar → métricas → elegibilidad → puntaje → recomendación) y `tool.py` (el `Tool` de `nosilo_core.registry` con `prepare → run → qa → deliver` + `options`). Mismo patrón que `packs/source_to_pay/`.

**Tech Stack:** Python 3.11+, solo stdlib (`csv`, `decimal`, `datetime`, `dataclasses`) + `nosilo_core`. pytest. ruff.

**Spec:** `docs/superpowers/specs/2026-10-11-pack-fletes-design.md`

## Global Constraints

- El pack importa **solo stdlib + `nosilo_core` + `packs`**. Nada de `src`, `scm_agent`, `jobs`, `webapp`, `warehouse`, ni terceros (lo hace cumplir `tests/test_pack_boundary.py`).
- Montos, kg, índices y puntajes en `Decimal`. `float` solo en `ExecutionOption.score`.
- Motor puro: sin `date.today()`, sin reloj, sin I/O. Mismos datos ⇒ mismo resultado.
- OTD vacío es `None`, **nunca 0**. Ninguna fila se descarta en silencio.
- Se llama **OTD**, nunca OTIF, en código, informe y mensajes.
- Defaults: `peso_costo = 0.6`, `peso_servicio = 0.4`, `min_envios = 10`, anualizar solo si `dias_periodo >= 28`.
- Prints/strings de consola en ASCII (el informe `.md` y el CSV pueden llevar UTF-8). En el código, la flecha de ruta es `" -> "`.
- No se toca nada fuera de `packs/fletes/`, `tests/test_fletes_*.py`, `tests/_fletes_fixtures.py` y `docs/`. No se registra en `scm_agent/tools.py`.
- Comandos desde la raíz del worktree `C:\Users\Gamer\Music\scm\wtfl`, rama `feat/pack-fletes`. `PYTHONPATH=.` ya lo pone `pyproject.toml` (`pythonpath = "."`).
- Commits: `<tipo>(fletes): <descripción>` y la línea `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **CSV exportado desde Excel en castellano** (separador `;`, BOM UTF-8): un usuario espera que se lea igual que uno con comas. → Task 1 detecta `;` por la línea de encabezado y abre con `utf-8-sig`; tests `test_acepta_separador_punto_y_coma` y `test_acepta_bom_de_excel`.
2. **CSV guardado en Windows-1252** (lo que hace Excel con "CSV" a secas, con tildes en "Concepción"): se espera un mensaje que diga cómo arreglarlo, no un traceback. → Task 1, `test_archivo_no_utf8_pide_guardar_como_csv_utf8`.
3. **Coma decimal o separador de miles** (`1.234,50`): se espera que se rechace nombrando fila, columna y la regla, no que se lea mal. → Task 1, `test_coma_decimal_se_rechaza_con_la_regla`.
4. **Mismas ciudades escritas distinto** (`"Santiago"` vs `" santiago "`): se espera una sola ruta, como con los transportistas. → Task 2 normaliza origen y destino con la misma regla que transportista; test `test_origen_y_destino_se_normalizan_como_el_transportista`.
5. **Fecha de entrega anterior al despacho, o `nan`/`inf` en un monto**: se espera rechazo explícito, no un OTD o un costo/kg absurdos. → Task 1, `test_entrega_antes_del_despacho_se_rechaza` y `test_nan_e_inf_se_rechazan`.

---

## File Structure

| Archivo | Responsabilidad | Task |
|---|---|---|
| `packs/fletes/loader.py` | contrato del CSV → `tuple[Envio, ...]`, `LoadError` | 1 |
| `packs/fletes/engine.py` | `Celda`, `RutaAgrupada`, `agrupar` (Task 2); `Pesos`, `Recomendacion`, `ResultadoRuta`, `Comparacion`, `puntuar`, `comparar` (Task 3) | 2, 3 |
| `packs/fletes/tool.py` | `Tool` + `prepare/run/qa/deliver/options`, `register`, `build_fletes_tools` | 4 |
| `packs/fletes/__init__.py` | docstring del pack + re-exports | 1 (mínimo), 4 (completo) |
| `tests/_fletes_fixtures.py` | constructores de `Envio` para tests | 2 |
| `tests/test_fletes_loader.py` | Task 1 | 1 |
| `tests/test_fletes_engine.py` | Tasks 2 y 3 | 2, 3 |
| `tests/test_fletes_pack.py` | Task 4 + caso integral | 4 |

---

### Task 1: Loader del CSV

**Files:**
- Create: `packs/fletes/__init__.py`
- Create: `packs/fletes/loader.py`
- Test: `tests/test_fletes_loader.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `class LoadError(ValueError)`
  - `@dataclass(frozen=True) class Envio`: `envio_id: str, transportista: str, origen: str, destino: str, fecha_despacho: date, costo: Decimal, moneda: str, kg: Decimal, fecha_prometida: date | None = None, fecha_entrega: date | None = None`
  - `REQUIRED_COLUMNS: tuple[str, ...]`, `OPTIONAL_COLUMNS: tuple[str, ...]`
  - `normalize_name(value: str) -> str` (colapsa espacios, conserva mayúsculas)
  - `name_key(value: str) -> str` (`normalize_name(value).casefold()`)
  - `parse_envios(text: str) -> tuple[Envio, ...]`
  - `load_envios(path: str | Path) -> tuple[Envio, ...]`

- [ ] **Step 1: Crear el paquete vacío**

`packs/fletes/__init__.py`:

```python
"""Fletes: compara los transportistas del propio cliente, ruta por ruta.

Docstring completo y re-exports en la Task 4.
"""
```

- [ ] **Step 2: Escribir los tests que fallan**

`tests/test_fletes_loader.py`:

```python
"""Contrato del CSV de envios: lo que se lee, lo que se rechaza y con que mensaje."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from packs.fletes.loader import LoadError, load_envios, name_key, normalize_name, parse_envios

HEADER = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg,fecha_prometida,fecha_entrega"
ROW = "E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-07"


def _text(*rows: str, header: str = HEADER) -> str:
    return "\n".join([header, *rows]) + "\n"


def test_lee_una_fila_valida_con_tipos() -> None:
    (envio,) = parse_envios(_text(ROW))
    assert envio.envio_id == "E1"
    assert envio.transportista == "Rapido"
    assert envio.origen == "Santiago"
    assert envio.destino == "Concepcion"
    assert envio.fecha_despacho == date(2026, 1, 5)
    assert envio.costo == Decimal("150")
    assert envio.moneda == "CLP"
    assert envio.kg == Decimal("100")
    assert envio.fecha_prometida == date(2026, 1, 7)
    assert envio.fecha_entrega == date(2026, 1, 7)


def test_fechas_opcionales_en_blanco_son_none() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))
    assert envio.fecha_prometida is None
    assert envio.fecha_entrega is None


def test_columnas_opcionales_ausentes_son_none() -> None:
    header = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg"
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100", header=header))
    assert envio.fecha_prometida is None and envio.fecha_entrega is None


def test_falta_columna_obligatoria_la_nombra() -> None:
    header = "envio_id,transportista,origen,destino,fecha_despacho,moneda,kg"
    with pytest.raises(LoadError, match="costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,CLP,100", header=header))


def test_encabezado_ignora_mayusculas_espacios_y_columnas_extra() -> None:
    header = " Envio_ID ,Transportista,origen,destino,fecha_despacho, Costo ,moneda,kg,comentario"
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,nada", header=header))
    assert envio.costo == Decimal("150")


def test_coma_decimal_se_rechaza_con_la_regla() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna costo:.*punto decimal"):
        parse_envios(_text('E1,Rapido,Santiago,Concepcion,2026-01-05,"1.234,50",CLP,100,,'))


def test_nan_e_inf_se_rechazan() -> None:
    with pytest.raises(LoadError, match="fila 2, columna costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,nan,CLP,100,,"))
    with pytest.raises(LoadError, match="fila 2, columna kg"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,10,CLP,inf,,"))


def test_kg_cero_y_costo_negativo_se_rechazan() -> None:
    with pytest.raises(LoadError, match="fila 2, columna kg"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,0,,"))
    with pytest.raises(LoadError, match="fila 2, columna costo"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,-1,CLP,100,,"))


def test_fecha_mal_formada_se_rechaza() -> None:
    with pytest.raises(LoadError, match=r"fila 2, columna fecha_despacho:.*AAAA-MM-DD"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,05/01/2026,150,CLP,100,,"))
    with pytest.raises(LoadError, match="fila 2, columna fecha_entrega"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-07T10:00"))


def test_celda_obligatoria_vacia_se_rechaza() -> None:
    with pytest.raises(LoadError, match="fila 2, columna transportista"):
        parse_envios(_text("E1,,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))


def test_envio_id_repetido_nombra_las_dos_filas() -> None:
    with pytest.raises(LoadError, match=r"fila 3.*E1.*fila 2"):
        parse_envios(_text(ROW, ROW))


def test_dos_monedas_se_rechazan_nombrandolas() -> None:
    otra = "E2,Rapido,Santiago,Concepcion,2026-01-05,150,USD,100,,"
    with pytest.raises(LoadError, match=r"CLP, USD"):
        parse_envios(_text(ROW, otra))


def test_entrega_antes_del_despacho_se_rechaza() -> None:
    with pytest.raises(LoadError, match="fila 2, columna fecha_entrega"):
        parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,CLP,100,2026-01-07,2026-01-04"))


def test_archivo_sin_envios_se_rechaza() -> None:
    with pytest.raises(LoadError, match="no tiene envios"):
        parse_envios(_text())
    with pytest.raises(LoadError, match="vacio"):
        parse_envios("")


def test_lineas_en_blanco_se_saltean_sin_contar_como_envio() -> None:
    assert len(parse_envios(_text(ROW, "", "E2,Lento,Santiago,Concepcion,2026-01-05,120,CLP,100,,"))) == 2


def test_acepta_separador_punto_y_coma() -> None:
    text = _text(ROW.replace(",", ";"), header=HEADER.replace(",", ";"))
    (envio,) = parse_envios(text)
    assert envio.costo == Decimal("150")


def test_acepta_bom_de_excel(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_text(_text(ROW), encoding="utf-8-sig")
    (envio,) = load_envios(path)
    assert envio.envio_id == "E1"


def test_archivo_no_utf8_pide_guardar_como_csv_utf8(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_bytes(_text(ROW.replace("Concepcion", "Concepción")).encode("cp1252"))
    with pytest.raises(LoadError, match="UTF-8"):
        load_envios(path)


def test_archivo_inexistente_es_load_error(tmp_path) -> None:
    with pytest.raises(LoadError, match="no se pudo leer"):
        load_envios(tmp_path / "no-existe.csv")


def test_moneda_se_guarda_en_mayusculas() -> None:
    (envio,) = parse_envios(_text("E1,Rapido,Santiago,Concepcion,2026-01-05,150,clp,100,,"))
    assert envio.moneda == "CLP"


def test_nombres_normalizados() -> None:
    assert normalize_name("  Chile   Express ") == "Chile Express"
    assert name_key("  CHILE   express ") == name_key("Chile Express")
    (envio,) = parse_envios(_text("E1,  Rapido   Sur ,Santiago,Concepcion,2026-01-05,150,CLP,100,,"))
    assert envio.transportista == "Rapido Sur"
```

- [ ] **Step 3: Correr y verificar que fallan**

Run: `pytest tests/test_fletes_loader.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'packs.fletes.loader'`

- [ ] **Step 4: Implementar `loader.py`**

`packs/fletes/loader.py`:

```python
"""El CSV de envios, leido con validacion estricta.

Una fila invalida rechaza el archivo entero con fila, columna y motivo. Nunca se
descarta una fila en silencio: un envio que desaparece es un transportista que
sale mejor de lo que es.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED_COLUMNS: tuple[str, ...] = (
    "envio_id", "transportista", "origen", "destino",
    "fecha_despacho", "costo", "moneda", "kg",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("fecha_prometida", "fecha_entrega")


class LoadError(ValueError):
    """El archivo no cumple el contrato. El mensaje dice donde y por que."""


@dataclass(frozen=True)
class Envio:
    envio_id: str
    transportista: str
    origen: str
    destino: str
    fecha_despacho: date
    costo: Decimal
    moneda: str
    kg: Decimal
    fecha_prometida: date | None = None
    fecha_entrega: date | None = None


def normalize_name(value: str) -> str:
    """Colapsa espacios; conserva mayusculas para mostrar."""
    return " ".join(value.split())


def name_key(value: str) -> str:
    """Clave de agrupacion: 'CHILEXPRESS ' y 'Chilexpress' son el mismo."""
    return normalize_name(value).casefold()


def load_envios(path: str | Path) -> tuple[Envio, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise LoadError(
            "el archivo no esta en UTF-8: en Excel, guardalo como 'CSV UTF-8 (delimitado por comas)'"
        ) from None
    except OSError as exc:
        raise LoadError(f"no se pudo leer el archivo: {exc}") from None
    return parse_envios(text)


def parse_envios(text: str) -> tuple[Envio, ...]:
    text = text.lstrip("\ufeff")
    first_line = text.split("\n", 1)[0]
    if not first_line.strip():
        raise LoadError("el archivo esta vacio")
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [cell.strip().lower() for cell in next(reader)]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise LoadError("faltan columnas obligatorias: " + ", ".join(missing))
    index = {col: header.index(col) for col in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS) if col in header}

    envios: list[Envio] = []
    seen_ids: dict[str, int] = {}
    currencies: dict[str, int] = {}
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        row_no = reader.line_num
        envio = _parse_row(cells, index, row_no)
        if envio.envio_id in seen_ids:
            raise LoadError(
                f"fila {row_no}: envio_id {envio.envio_id!r} repetido (ya estaba en la fila {seen_ids[envio.envio_id]})"
            )
        seen_ids[envio.envio_id] = row_no
        currencies.setdefault(envio.moneda, row_no)
        envios.append(envio)

    if not envios:
        raise LoadError("el archivo no tiene envios")
    if len(currencies) > 1:
        raise LoadError(
            "hay mas de una moneda (" + ", ".join(sorted(currencies))
            + "): esta version no convierte monedas; separa el archivo por moneda"
        )
    return tuple(envios)


def _cell(cells: list[str], index: dict[str, int], col: str) -> str:
    i = index.get(col)
    if i is None or i >= len(cells):
        return ""
    return cells[i].strip()


def _error(row_no: int, col: str, motivo: str, raw: str) -> LoadError:
    return LoadError(f"fila {row_no}, columna {col}: {motivo} ({raw!r})")


def _decimal(raw: str, col: str, row_no: int) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _error(row_no, col, "no es un numero (usa punto decimal, sin separador de miles)", raw) from None
    if not value.is_finite():
        raise _error(row_no, col, "no es un numero finito", raw)
    return value


def _date(raw: str, col: str, row_no: int) -> date:
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw) from None


def _parse_row(cells: list[str], index: dict[str, int], row_no: int) -> Envio:
    def required(col: str) -> str:
        value = _cell(cells, index, col)
        if not value:
            raise _error(row_no, col, "vacia", value)
        return value

    def optional_date(col: str) -> date | None:
        value = _cell(cells, index, col)
        return _date(value, col, row_no) if value else None

    costo = _decimal(required("costo"), "costo", row_no)
    if costo < 0:
        raise _error(row_no, "costo", "no puede ser negativo", str(costo))
    kg = _decimal(required("kg"), "kg", row_no)
    if kg <= 0:
        raise _error(row_no, "kg", "debe ser mayor que cero", str(kg))
    despacho = _date(required("fecha_despacho"), "fecha_despacho", row_no)
    prometida = optional_date("fecha_prometida")
    entrega = optional_date("fecha_entrega")
    if entrega is not None and entrega < despacho:
        raise _error(row_no, "fecha_entrega", "es anterior a fecha_despacho", entrega.isoformat())

    return Envio(
        envio_id=required("envio_id"),
        transportista=normalize_name(required("transportista")),
        origen=normalize_name(required("origen")),
        destino=normalize_name(required("destino")),
        fecha_despacho=despacho,
        costo=costo,
        moneda=required("moneda").upper(),
        kg=kg,
        fecha_prometida=prometida,
        fecha_entrega=entrega,
    )
```

- [ ] **Step 5: Correr y verificar que pasan**

Run: `pytest tests/test_fletes_loader.py tests/test_pack_boundary.py -q`
Expected: todos PASS (la frontera ya recorre `packs/fletes/loader.py`).

- [ ] **Step 6: Lint**

Run: `ruff check packs/fletes tests/test_fletes_loader.py`
Expected: `All checks passed!` (si aparece I001, `ruff check --fix` sobre esos archivos).

- [ ] **Step 7: Commit**

```bash
git add packs/fletes/__init__.py packs/fletes/loader.py tests/test_fletes_loader.py
git commit -m "feat(fletes): loader estricto del CSV de envios

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Motor — agrupar, métricas y elegibilidad

**Files:**
- Create: `packs/fletes/engine.py`
- Create: `tests/_fletes_fixtures.py`
- Test: `tests/test_fletes_engine.py`

**Interfaces:**
- Consumes: `Envio`, `name_key` de `packs.fletes.loader`.
- Produces (en `packs/fletes/engine.py`):
  - constantes `MIN_ENVIOS_DEFAULT = 10`, `MOTIVO_POCOS_ENVIOS = "pocos_envios"`, `MOTIVO_SIN_FECHAS = "sin_fechas"`
  - `@dataclass(frozen=True) class Celda`: `ruta: str, transportista: str, envios: int, kg_total: Decimal, costo_total: Decimal, costo_kg: Decimal, envios_fechados: int, otd: Decimal | None, elegible: bool, motivo: str | None, indice_costo: Decimal | None = None, indice_servicio: Decimal | None = None, puntaje: Decimal | None = None`
  - `@dataclass(frozen=True) class RutaAgrupada`: `ruta: str, celdas: tuple[Celda, ...], dias_periodo: int`
  - `agrupar(envios: Sequence[Envio], min_envios: int = MIN_ENVIOS_DEFAULT) -> tuple[RutaAgrupada, ...]` — rutas ordenadas por `ruta.casefold()`, celdas por `transportista.casefold()`. `ruta` = `"{origen} -> {destino}"` con la grafía de la primera aparición.
  - `tests/_fletes_fixtures.py`: `envio(...) -> Envio` y `lote(...) -> list[Envio]` (firmas abajo).

- [ ] **Step 1: Crear los constructores de test**

`tests/_fletes_fixtures.py`:

```python
"""Constructores de envios para los tests del pack fletes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from packs.fletes.loader import Envio

INICIO = date(2026, 1, 1)


def envio(
    n: int,
    transportista: str,
    *,
    origen: str = "Santiago",
    destino: str = "Concepcion",
    dia: int = 0,
    costo: str = "100",
    kg: str = "100",
    prometida: int | None = 2,
    entrega: int | None = 2,
    moneda: str = "CLP",
) -> Envio:
    """Un envio; `prometida`/`entrega` son dias despues del despacho (None = sin fecha)."""
    despacho = INICIO + timedelta(days=dia)
    return Envio(
        envio_id=f"{transportista}|{origen}|{destino}|{n}",
        transportista=transportista,
        origen=origen,
        destino=destino,
        fecha_despacho=despacho,
        costo=Decimal(costo),
        moneda=moneda,
        kg=Decimal(kg),
        fecha_prometida=None if prometida is None else despacho + timedelta(days=prometida),
        fecha_entrega=None if entrega is None else despacho + timedelta(days=entrega),
    )


def lote(
    transportista: str,
    n: int,
    *,
    a_tiempo: int | None = None,
    fechados: int | None = None,
    dia0: int = 0,
    paso: int = 1,
    **kw: str,
) -> list[Envio]:
    """n envios cada `paso` dias desde `dia0`. Los primeros `fechados` llevan fechas;
    de esos, los primeros `a_tiempo` llegan a tiempo y el resto un dia tarde."""
    fechados = n if fechados is None else fechados
    a_tiempo = fechados if a_tiempo is None else a_tiempo
    out: list[Envio] = []
    for i in range(n):
        if i >= fechados:
            out.append(envio(i, transportista, dia=dia0 + i * paso, prometida=None, entrega=None, **kw))
        else:
            out.append(envio(i, transportista, dia=dia0 + i * paso, entrega=2 if i < a_tiempo else 3, **kw))
    return out
```

- [ ] **Step 2: Escribir los tests que fallan**

`tests/test_fletes_engine.py`:

```python
"""Motor del pack fletes: metricas, elegibilidad, puntaje y recomendacion."""

from __future__ import annotations

from decimal import Decimal

import pytest

from packs.fletes.engine import (
    MIN_ENVIOS_DEFAULT,
    MOTIVO_POCOS_ENVIOS,
    MOTIVO_SIN_FECHAS,
    agrupar,
)
from tests._fletes_fixtures import envio, lote


def _celda(rutas, ruta, transportista):
    (r,) = [x for x in rutas if x.ruta == ruta]
    (c,) = [x for x in r.celdas if x.transportista == transportista]
    return c


def test_costo_kg_es_cociente_de_sumas_no_promedio_de_cocientes() -> None:
    envios = [envio(1, "A", costo="100", kg="100"), envio(2, "A", costo="300", kg="100", dia=1),
              envio(3, "A", costo="10", kg="800", dia=2)]
    c = _celda(agrupar(envios), "Santiago -> Concepcion", "A")
    assert c.kg_total == Decimal("1000")
    assert c.costo_total == Decimal("410")
    assert c.costo_kg == Decimal("410") / Decimal("1000")  # promedio de cocientes daria 1.3375


def test_otd_cuenta_solo_envios_con_las_dos_fechas() -> None:
    envios = lote("A", 12, fechados=10, a_tiempo=8)
    c = _celda(agrupar(envios), "Santiago -> Concepcion", "A")
    assert c.envios == 12
    assert c.envios_fechados == 10
    assert c.otd == Decimal(8) / Decimal(10)


def test_llegar_el_dia_prometido_es_a_tiempo() -> None:
    c = _celda(agrupar([envio(1, "A", prometida=2, entrega=2)]), "Santiago -> Concepcion", "A")
    assert c.otd == Decimal(1)


def test_sin_fechas_el_otd_es_none_nunca_cero() -> None:
    c = _celda(agrupar(lote("A", 12, fechados=0)), "Santiago -> Concepcion", "A")
    assert c.otd is None
    assert not c.elegible
    assert c.motivo == MOTIVO_SIN_FECHAS


def test_elegibilidad_cuenta_envios_fechados_no_envios() -> None:
    rutas = agrupar(lote("A", 15, fechados=9) + lote("B", 10, dia0=100))
    a = _celda(rutas, "Santiago -> Concepcion", "A")
    b = _celda(rutas, "Santiago -> Concepcion", "B")
    assert (a.elegible, a.motivo) == (False, MOTIVO_POCOS_ENVIOS)
    assert (b.elegible, b.motivo) == (True, None)


def test_min_envios_es_parametro() -> None:
    c = _celda(agrupar(lote("A", 3), min_envios=3), "Santiago -> Concepcion", "A")
    assert c.elegible
    assert MIN_ENVIOS_DEFAULT == 10


def test_min_envios_menor_a_uno_se_rechaza() -> None:
    with pytest.raises(ValueError):
        agrupar(lote("A", 3), min_envios=0)


def test_la_ruta_es_dirigida() -> None:
    envios = [envio(1, "A"), envio(2, "A", origen="Concepcion", destino="Santiago")]
    assert [r.ruta for r in agrupar(envios)] == ["Concepcion -> Santiago", "Santiago -> Concepcion"]


def test_transportista_se_agrupa_sin_mayusculas() -> None:
    envios = [envio(1, "Chilexpress"), envio(2, "CHILEXPRESS", dia=1)]
    (ruta,) = agrupar(envios)
    (c,) = ruta.celdas
    assert c.transportista == "Chilexpress"  # grafia de la primera aparicion
    assert c.envios == 2


def test_origen_y_destino_se_normalizan_como_el_transportista() -> None:
    envios = [envio(1, "A", origen="Santiago"), envio(2, "A", origen="SANTIAGO", destino="concepcion", dia=1)]
    (ruta,) = agrupar(envios)
    assert ruta.ruta == "Santiago -> Concepcion"
    assert ruta.celdas[0].envios == 2


def test_dias_periodo_va_de_la_primera_a_la_ultima_fecha_de_la_ruta_inclusive() -> None:
    envios = lote("A", 3, dia0=0, paso=10) + lote("B", 2, dia0=5, paso=40)  # dias 0,10,20 y 5,45
    (ruta,) = agrupar(envios)
    assert ruta.dias_periodo == 46


def test_orden_estable_de_rutas_y_celdas() -> None:
    envios = [envio(1, "zeta", destino="b"), envio(2, "Alfa", destino="b"), envio(3, "A", destino="a")]
    rutas = agrupar(envios)
    assert [r.ruta for r in rutas] == ["Santiago -> a", "Santiago -> b"]
    assert [c.transportista for c in rutas[1].celdas] == ["Alfa", "zeta"]


def test_el_motor_no_muta_la_entrada() -> None:
    envios = tuple(lote("A", 12))
    antes = list(envios)
    agrupar(envios)
    assert list(envios) == antes
```

- [ ] **Step 3: Correr y verificar que fallan**

Run: `pytest tests/test_fletes_engine.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'packs.fletes.engine'`

- [ ] **Step 4: Implementar la primera mitad de `engine.py`**

`packs/fletes/engine.py`:

```python
"""Motor del pack fletes: puro, sin reloj, Decimal en todo el calculo.

Agrupa envios por ruta (origen -> destino, dirigida) y transportista, mide costo/kg
y OTD, decide quien tiene muestra suficiente, puntua y recomienda. Se llama OTD y
no OTIF: el "in full" no esta en un archivo de fletes, y nombrarlo seria fabricarlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .loader import Envio, name_key

MIN_ENVIOS_DEFAULT = 10

MOTIVO_POCOS_ENVIOS = "pocos_envios"
MOTIVO_SIN_FECHAS = "sin_fechas"


@dataclass(frozen=True)
class Celda:
    """Un transportista en una ruta. Los tres ultimos campos los llena `puntuar`."""

    ruta: str
    transportista: str
    envios: int
    kg_total: Decimal
    costo_total: Decimal
    costo_kg: Decimal
    envios_fechados: int
    otd: Decimal | None
    elegible: bool
    motivo: str | None
    indice_costo: Decimal | None = None
    indice_servicio: Decimal | None = None
    puntaje: Decimal | None = None


@dataclass(frozen=True)
class RutaAgrupada:
    ruta: str
    celdas: tuple[Celda, ...]
    dias_periodo: int


def agrupar(envios: Sequence[Envio], min_envios: int = MIN_ENVIOS_DEFAULT) -> tuple[RutaAgrupada, ...]:
    if min_envios < 1:
        raise ValueError(f"min_envios debe ser >= 1 (es {min_envios})")

    nombres_ruta: dict[tuple[str, str], str] = {}
    fechas: dict[tuple[str, str], list[date]] = {}
    por_celda: dict[tuple[str, str], dict[str, list[Envio]]] = {}
    nombres_transp: dict[tuple[tuple[str, str], str], str] = {}

    for e in envios:
        rkey = (name_key(e.origen), name_key(e.destino))
        tkey = name_key(e.transportista)
        nombres_ruta.setdefault(rkey, f"{e.origen} -> {e.destino}")
        nombres_transp.setdefault((rkey, tkey), e.transportista)
        fechas.setdefault(rkey, []).append(e.fecha_despacho)
        por_celda.setdefault(rkey, {}).setdefault(tkey, []).append(e)

    rutas: list[RutaAgrupada] = []
    for rkey, celdas_envios in por_celda.items():
        nombre = nombres_ruta[rkey]
        celdas = sorted(
            (_celda(nombre, nombres_transp[(rkey, tkey)], lista, min_envios) for tkey, lista in celdas_envios.items()),
            key=lambda c: c.transportista.casefold(),
        )
        dias = (max(fechas[rkey]) - min(fechas[rkey])).days + 1
        rutas.append(RutaAgrupada(ruta=nombre, celdas=tuple(celdas), dias_periodo=dias))
    return tuple(sorted(rutas, key=lambda r: r.ruta.casefold()))


def _celda(ruta: str, transportista: str, envios: list[Envio], min_envios: int) -> Celda:
    kg = sum((e.kg for e in envios), Decimal(0))
    costo = sum((e.costo for e in envios), Decimal(0))
    fechados = [e for e in envios if e.fecha_prometida is not None and e.fecha_entrega is not None]
    a_tiempo = sum(1 for e in fechados if e.fecha_entrega <= e.fecha_prometida)  # type: ignore[operator]
    otd = Decimal(a_tiempo) / Decimal(len(fechados)) if fechados else None

    if len(fechados) >= min_envios:
        elegible, motivo = True, None
    elif not fechados:
        elegible, motivo = False, MOTIVO_SIN_FECHAS
    else:
        elegible, motivo = False, MOTIVO_POCOS_ENVIOS

    return Celda(
        ruta=ruta,
        transportista=transportista,
        envios=len(envios),
        kg_total=kg,
        costo_total=costo,
        costo_kg=costo / kg,
        envios_fechados=len(fechados),
        otd=otd,
        elegible=elegible,
        motivo=motivo,
    )
```

- [ ] **Step 5: Correr y verificar que pasan**

Run: `pytest tests/test_fletes_engine.py tests/test_pack_boundary.py -q`
Expected: todos PASS.

- [ ] **Step 6: Lint**

Run: `ruff check packs/fletes tests/test_fletes_engine.py tests/_fletes_fixtures.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add packs/fletes/engine.py tests/_fletes_fixtures.py tests/test_fletes_engine.py
git commit -m "feat(fletes): metricas por transportista y ruta, OTD vacio nunca cero

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Motor — puntaje y recomendación

**Files:**
- Modify: `packs/fletes/engine.py` (agregar al final)
- Test: `tests/test_fletes_engine.py` (agregar al final)

**Interfaces:**
- Consumes: `Celda`, `RutaAgrupada`, `agrupar`, `MIN_ENVIOS_DEFAULT` (Task 2); `Envio` (Task 1).
- Produces (en `packs/fletes/engine.py`):
  - constantes `MIN_DIAS_ANUALIZAR = 28`, `RUTA_SIN_ALTERNATIVA = "sin_alternativa"`, `RUTA_ACTUAL_NO_ELEGIBLE = "actual_no_elegible"`, `RUTA_MENOS_DE_DOS = "menos_de_dos_elegibles"`, `RUTA_ACTUAL_ES_EL_MEJOR = "actual_ya_es_el_mejor"`
  - `@dataclass(frozen=True) class Pesos`: `costo: Decimal = Decimal("0.6"), servicio: Decimal = Decimal("0.4")`; `ValueError` si alguno < 0 o no suman 1.
  - `@dataclass(frozen=True) class Recomendacion`: `ruta: str, actual: Celda, mejor: Celda, ahorro_periodo: Decimal, ahorro_anual: Decimal | None, dias_periodo: int, delta_otd_pp: Decimal`
  - `@dataclass(frozen=True) class ResultadoRuta`: `ruta: str, celdas: tuple[Celda, ...], actual: str, dias_periodo: int, recomendacion: Recomendacion | None, motivo: str | None` — `celdas` = elegibles por ranking, después no elegibles por nombre.
  - `@dataclass(frozen=True) class Comparacion`: `rutas: tuple[ResultadoRuta, ...], moneda: str, pesos: Pesos, min_envios: int, envios: int, kg_total: Decimal`; propiedades `recomendaciones -> tuple[Recomendacion, ...]`, `ahorro_anual_total -> Decimal`, `rutas_comparables -> int` (rutas con >= 2 elegibles).
  - `puntuar(ruta: RutaAgrupada, pesos: Pesos) -> ResultadoRuta`
  - `comparar(envios: Sequence[Envio], pesos: Pesos = Pesos(), min_envios: int = MIN_ENVIOS_DEFAULT) -> Comparacion` — `ValueError` si `envios` vacío o con más de una moneda.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_fletes_engine.py` (y sumar estos nombres al `import` de `packs.fletes.engine`: `MIN_DIAS_ANUALIZAR, RUTA_ACTUAL_ES_EL_MEJOR, RUTA_ACTUAL_NO_ELEGIBLE, RUTA_MENOS_DE_DOS, RUTA_SIN_ALTERNATIVA, Pesos, comparar, puntuar`):

```python
def _ruta(envios, **kw):
    (ruta,) = agrupar(envios, **kw)
    return ruta


def test_pesos_por_defecto_y_validacion() -> None:
    assert Pesos() == Pesos(Decimal("0.6"), Decimal("0.4"))
    with pytest.raises(ValueError, match="sumar 1"):
        Pesos(Decimal("0.5"), Decimal("0.4"))
    with pytest.raises(ValueError, match="negativ"):
        Pesos(Decimal("1.2"), Decimal("-0.2"))


def test_puntaje_desglosado_contra_el_mejor_de_la_ruta() -> None:
    # A: 1.5/kg, OTD 0.8 (15 envios, 1500 kg) - B: 1.2/kg, OTD 1.0 (12 envios)
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")
    res = puntuar(_ruta(envios), Pesos())
    a = next(c for c in res.celdas if c.transportista == "A")
    b = next(c for c in res.celdas if c.transportista == "B")
    assert a.indice_costo == Decimal("1.2") / Decimal("1.5")
    assert a.indice_servicio == Decimal("0.8")
    assert a.puntaje == Decimal("0.6") * a.indice_costo + Decimal("0.4") * Decimal("0.8")
    assert (b.indice_costo, b.indice_servicio, b.puntaje) == (Decimal(1), Decimal(1), Decimal(1))
    assert [c.transportista for c in res.celdas] == ["B", "A"]


def test_recomienda_y_calcula_ahorro_y_delta_otd() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
    ruta = _ruta(envios)
    res = puntuar(ruta, Pesos())
    rec = res.recomendacion
    assert res.actual == "A" and res.motivo is None
    assert rec.mejor.transportista == "B" and rec.actual.transportista == "A"
    assert rec.ahorro_periodo == (Decimal("1.5") - Decimal("1.2")) * Decimal("1500")
    assert ruta.dias_periodo == 57
    assert rec.ahorro_anual == rec.ahorro_periodo * Decimal(365) / Decimal(57)
    assert rec.delta_otd_pp == (Decimal(1) - Decimal("0.8")) * 100


def test_ahorro_negativo_se_informa_con_signo() -> None:
    # B es mas caro pero mucho mas puntual; con peso de servicio alto gana igual
    envios = lote("A", 15, a_tiempo=5, costo="100", paso=4) + lote("B", 12, costo="110", paso=4)
    rec = puntuar(_ruta(envios), Pesos(Decimal("0.2"), Decimal("0.8"))).recomendacion
    assert rec.mejor.transportista == "B"
    assert rec.ahorro_periodo < 0


def test_periodo_corto_no_se_anualiza() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")  # 15 dias
    rec = puntuar(_ruta(envios), Pesos()).recomendacion
    assert rec.dias_periodo < MIN_DIAS_ANUALIZAR
    assert rec.ahorro_anual is None
    assert rec.ahorro_periodo > 0


def test_un_solo_transportista_es_sin_alternativa() -> None:
    res = puntuar(_ruta(lote("A", 12)), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_SIN_ALTERNATIVA)


def test_actual_sin_muestra_no_recibe_recomendacion() -> None:
    envios = lote("A", 30, fechados=5) + lote("B", 12) + lote("C", 12, costo="90")
    res = puntuar(_ruta(envios), Pesos())
    assert res.actual == "A"
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_NO_ELEGIBLE)


def test_menos_de_dos_elegibles() -> None:
    envios = lote("A", 12) + lote("B", 11, fechados=8, costo="90")
    res = puntuar(_ruta(envios), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_MENOS_DE_DOS)


def test_actual_ya_es_el_mejor() -> None:
    envios = lote("A", 15, costo="100") + lote("B", 12, costo="150")
    res = puntuar(_ruta(envios), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_ES_EL_MEJOR)


def test_no_elegibles_quedan_sin_puntaje_y_al_final() -> None:
    envios = lote("A", 15) + lote("B", 12, costo="90") + lote("C", 4, costo="10")
    res = puntuar(_ruta(envios), Pesos())
    assert [c.transportista for c in res.celdas][-1] == "C"
    c = res.celdas[-1]
    assert (c.indice_costo, c.indice_servicio, c.puntaje) == (None, None, None)


def test_empate_de_puntaje_desempata_por_costo_y_despues_por_nombre() -> None:
    envios = lote("Beta", 12) + lote("Alfa", 12)  # identicos
    res = puntuar(_ruta(envios), Pesos())
    assert [c.transportista for c in res.celdas] == ["Alfa", "Beta"]


def test_actual_es_el_de_mas_kg_con_desempate_por_nombre() -> None:
    envios = lote("Beta", 12) + lote("Alfa", 12)
    assert puntuar(_ruta(envios), Pesos()).actual == "Alfa"


def test_costo_cero_tiene_indice_uno() -> None:
    envios = lote("A", 12, costo="0") + lote("B", 12, costo="100")
    res = puntuar(_ruta(envios), Pesos())
    a = next(c for c in res.celdas if c.transportista == "A")
    b = next(c for c in res.celdas if c.transportista == "B")
    assert a.indice_costo == Decimal(1)
    assert b.indice_costo == Decimal(0)


def test_puntajes_quedan_entre_cero_y_uno() -> None:
    envios = lote("A", 12, a_tiempo=0, costo="1000") + lote("B", 12, costo="1")
    for c in puntuar(_ruta(envios), Pesos()).celdas:
        assert Decimal(0) <= c.puntaje <= Decimal(1)


def test_comparar_arma_la_corrida_completa() -> None:
    envios = (lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
              + lote("N", 10, destino="Antofagasta"))
    comp = comparar(envios)
    assert comp.moneda == "CLP"
    assert comp.envios == 37
    assert comp.kg_total == Decimal(3700)
    assert comp.pesos == Pesos() and comp.min_envios == MIN_ENVIOS_DEFAULT
    assert [r.ruta for r in comp.rutas] == ["Santiago -> Antofagasta", "Santiago -> Concepcion"]
    assert len(comp.recomendaciones) == 1
    assert comp.rutas_comparables == 1
    assert comp.ahorro_anual_total == comp.recomendaciones[0].ahorro_anual


def test_ahorro_anual_total_ignora_los_no_anualizables() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")
    assert comparar(envios).ahorro_anual_total == Decimal(0)


def test_comparar_rechaza_vacio_y_monedas_mezcladas() -> None:
    with pytest.raises(ValueError):
        comparar([])
    with pytest.raises(ValueError, match="moneda"):
        comparar([envio(1, "A"), envio(2, "A", dia=1, moneda="USD")])


def test_mismos_datos_mismo_resultado() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
    assert comparar(envios) == comparar(list(reversed(envios)))
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `pytest tests/test_fletes_engine.py -q`
Expected: FAIL con `ImportError: cannot import name 'Pesos' from 'packs.fletes.engine'`

- [ ] **Step 3: Implementar la segunda mitad de `engine.py`**

Agregar `replace` al import de `dataclasses` (`from dataclasses import dataclass, replace`) y al final de `packs/fletes/engine.py`:

```python
MIN_DIAS_ANUALIZAR = 28

RUTA_SIN_ALTERNATIVA = "sin_alternativa"
RUTA_ACTUAL_NO_ELEGIBLE = "actual_no_elegible"
RUTA_MENOS_DE_DOS = "menos_de_dos_elegibles"
RUTA_ACTUAL_ES_EL_MEJOR = "actual_ya_es_el_mejor"


@dataclass(frozen=True)
class Pesos:
    costo: Decimal = Decimal("0.6")
    servicio: Decimal = Decimal("0.4")

    def __post_init__(self) -> None:
        if self.costo < 0 or self.servicio < 0:
            raise ValueError("los pesos no pueden ser negativos")
        if self.costo + self.servicio != 1:
            raise ValueError(f"los pesos deben sumar 1 (suman {self.costo + self.servicio})")


@dataclass(frozen=True)
class Recomendacion:
    ruta: str
    actual: Celda
    mejor: Celda
    ahorro_periodo: Decimal
    ahorro_anual: Decimal | None
    dias_periodo: int
    delta_otd_pp: Decimal


@dataclass(frozen=True)
class ResultadoRuta:
    ruta: str
    celdas: tuple[Celda, ...]
    actual: str
    dias_periodo: int
    recomendacion: Recomendacion | None
    motivo: str | None


@dataclass(frozen=True)
class Comparacion:
    rutas: tuple[ResultadoRuta, ...]
    moneda: str
    pesos: Pesos
    min_envios: int
    envios: int
    kg_total: Decimal

    @property
    def recomendaciones(self) -> tuple[Recomendacion, ...]:
        return tuple(r.recomendacion for r in self.rutas if r.recomendacion is not None)

    @property
    def ahorro_anual_total(self) -> Decimal:
        return sum(
            (rec.ahorro_anual for rec in self.recomendaciones if rec.ahorro_anual is not None),
            Decimal(0),
        )

    @property
    def rutas_comparables(self) -> int:
        return sum(1 for r in self.rutas if sum(1 for c in r.celdas if c.elegible) >= 2)


def puntuar(ruta: RutaAgrupada, pesos: Pesos) -> ResultadoRuta:
    ranking = sorted(
        _puntuar_elegibles([c for c in ruta.celdas if c.elegible], pesos),
        key=lambda c: (-c.puntaje, c.costo_kg, c.transportista.casefold()),  # type: ignore[operator]
    )
    no_elegibles = [c for c in ruta.celdas if not c.elegible]
    actual = sorted(ruta.celdas, key=lambda c: (-c.kg_total, c.transportista.casefold()))[0]

    motivo = _motivo(ruta, actual, ranking)
    recomendacion = None
    if motivo is None:
        actual_puntuado = next(c for c in ranking if c.transportista == actual.transportista)
        recomendacion = _recomendacion(ruta, actual_puntuado, ranking[0])

    return ResultadoRuta(
        ruta=ruta.ruta,
        celdas=(*ranking, *no_elegibles),
        actual=actual.transportista,
        dias_periodo=ruta.dias_periodo,
        recomendacion=recomendacion,
        motivo=motivo,
    )


def comparar(
    envios: Sequence[Envio],
    pesos: Pesos = Pesos(),
    min_envios: int = MIN_ENVIOS_DEFAULT,
) -> Comparacion:
    if not envios:
        raise ValueError("no hay envios para comparar")
    monedas = sorted({e.moneda for e in envios})
    if len(monedas) > 1:
        raise ValueError(f"mas de una moneda ({', '.join(monedas)}): no se convierte")
    return Comparacion(
        rutas=tuple(puntuar(r, pesos) for r in agrupar(envios, min_envios)),
        moneda=monedas[0],
        pesos=pesos,
        min_envios=min_envios,
        envios=len(envios),
        kg_total=sum((e.kg for e in envios), Decimal(0)),
    )


def _puntuar_elegibles(elegibles: list[Celda], pesos: Pesos) -> list[Celda]:
    if not elegibles:
        return []
    minimo = min(c.costo_kg for c in elegibles)
    puntuadas: list[Celda] = []
    for c in elegibles:
        indice_costo = Decimal(1) if c.costo_kg == 0 else minimo / c.costo_kg
        indice_servicio = c.otd  # elegible => tiene envios fechados => otd no es None
        puntaje = pesos.costo * indice_costo + pesos.servicio * indice_servicio  # type: ignore[operator]
        puntuadas.append(replace(c, indice_costo=indice_costo, indice_servicio=indice_servicio, puntaje=puntaje))
    return puntuadas


def _motivo(ruta: RutaAgrupada, actual: Celda, ranking: list[Celda]) -> str | None:
    if len(ruta.celdas) == 1:
        return RUTA_SIN_ALTERNATIVA
    if not actual.elegible:
        return RUTA_ACTUAL_NO_ELEGIBLE
    if len(ranking) < 2:
        return RUTA_MENOS_DE_DOS
    if ranking[0].transportista == actual.transportista:
        return RUTA_ACTUAL_ES_EL_MEJOR
    return None


def _recomendacion(ruta: RutaAgrupada, actual: Celda, mejor: Celda) -> Recomendacion:
    ahorro = (actual.costo_kg - mejor.costo_kg) * actual.kg_total
    anual = (
        ahorro * Decimal(365) / Decimal(ruta.dias_periodo)
        if ruta.dias_periodo >= MIN_DIAS_ANUALIZAR
        else None
    )
    return Recomendacion(
        ruta=ruta.ruta,
        actual=actual,
        mejor=mejor,
        ahorro_periodo=ahorro,
        ahorro_anual=anual,
        dias_periodo=ruta.dias_periodo,
        delta_otd_pp=(mejor.otd - actual.otd) * 100,  # type: ignore[operator]
    )
```

Nota sobre `test_mismos_datos_mismo_resultado`: `agrupar` toma la grafía de la primera aparición; con nombres idénticos el orden de entrada no cambia nada. Si falla, el motor depende del orden de entrada y hay que corregir el motor, no el test.

- [ ] **Step 4: Correr y verificar que pasan**

Run: `pytest tests/test_fletes_engine.py tests/test_pack_boundary.py -q`
Expected: todos PASS.

- [ ] **Step 5: Lint**

Run: `ruff check packs/fletes tests/test_fletes_engine.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add packs/fletes/engine.py tests/test_fletes_engine.py
git commit -m "feat(fletes): puntaje desglosado y recomendacion por ruta

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Tool, entregables, opciones y caso integral

**Files:**
- Create: `packs/fletes/tool.py`
- Modify: `packs/fletes/__init__.py` (reemplazo completo)
- Test: `tests/test_fletes_pack.py`

**Interfaces:**
- Consumes: `LoadError`, `load_envios`, `REQUIRED_COLUMNS`, `OPTIONAL_COLUMNS` (Task 1); `Comparacion`, `Pesos`, `comparar`, `MIN_ENVIOS_DEFAULT`, constantes `MOTIVO_*` y `RUTA_*` (Tasks 2-3); de `nosilo_core`: `Tool`, `ToolRegistry`, `Prepared`, `Produced` (`nosilo_core.registry`), `JobRequest`, `STATUS_OK`, `STATUS_NEEDS_DATA`, `STATUS_NEEDS_CLARIFICATION` (`nosilo_core.types`), `ExecutionOption`, `GuidedOutcome`, `HandoffPacket`, `Residual`, `as_options`, `as_handoff` (`nosilo_core.guided`), `LLMProvider` (`nosilo_core.llm`).
- Produces:
  - `TOOL_KEY = "carrier_lane_compare"`, `CARRIER_LANE_COMPARE: Tool`
  - `prepare(request: JobRequest, provider: LLMProvider) -> Prepared` — payload `{"envios": tuple[Envio, ...], "pesos": Pesos, "min_envios": int}`
  - `run(prepared: object, params: dict) -> Produced` — `report` es `Comparacion`
  - `qa(report: object) -> list[str]`
  - `deliver(report: object, out_dir: Path, client: str) -> dict[str, Path]` — claves `"matriz"` (`fletes_matriz.csv`) e `"informe"` (`fletes_informe.md`)
  - `options(report: object) -> GuidedOutcome`
  - `register(registry: ToolRegistry) -> ToolRegistry`, `build_fletes_tools() -> ToolRegistry`

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_fletes_pack.py`:

```python
"""El pack fletes completo: contrato del Tool, entregables, opciones y un caso integral."""

from __future__ import annotations

import csv
from decimal import Decimal

from nosilo_core.guided import HANDOFF, OPTIONS, verify_guided
from nosilo_core.registry import ToolRegistry
from nosilo_core.types import STATUS_NEEDS_CLARIFICATION, STATUS_NEEDS_DATA, STATUS_OK, JobRequest
from packs.fletes import build_fletes_tools, register
from packs.fletes.engine import (
    RUTA_MENOS_DE_DOS,
    RUTA_SIN_ALTERNATIVA,
    Comparacion,
    Pesos,
)
from packs.fletes.tool import CARRIER_LANE_COMPARE, deliver, options, prepare, qa, run
from tests._fletes_fixtures import lote

HEADER = "envio_id,transportista,origen,destino,fecha_despacho,costo,moneda,kg,fecha_prometida,fecha_entrega"


def _csv(tmp_path, envios) -> str:
    path = tmp_path / "envios.csv"
    lines = [HEADER]
    for e in envios:
        lines.append(",".join([
            e.envio_id, e.transportista, e.origen, e.destino, e.fecha_despacho.isoformat(),
            str(e.costo), e.moneda, str(e.kg),
            e.fecha_prometida.isoformat() if e.fecha_prometida else "",
            e.fecha_entrega.isoformat() if e.fecha_entrega else "",
        ]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _caso_integral():
    """3 rutas, 5 transportistas, 60 envios.

    Santiago -> Concepcion: Rapido (actual, 15 env, 1.5/kg, OTD 0.8) vs Lento (12 env, 1.2/kg, OTD 1.0)
        => recomienda Lento.
    Santiago -> Antofagasta: solo Norte => sin_alternativa.
    Valparaiso -> Santiago: Costa (actual, 12 env x 50 kg, 2.0/kg) vs Express (11 env, solo 8 fechados)
        => menos_de_dos_elegibles.
    """
    return (
        lote("Rapido", 15, a_tiempo=12, costo="150", paso=4)
        + lote("Lento", 12, costo="120", paso=4)
        + lote("Norte", 10, destino="Antofagasta", paso=3)
        + lote("Costa", 12, origen="Valparaiso", destino="Santiago", costo="100", kg="50", paso=2)
        + lote("Express", 11, fechados=8, origen="Valparaiso", destino="Santiago", costo="90", kg="50", paso=2)
    )


def _prepared(tmp_path, envios=None, **params):
    request = JobRequest(brief="comparar transportistas", data_path=_csv(tmp_path, envios or _caso_integral()),
                         params=params)
    return prepare(request, provider=None)


def _comparacion(tmp_path, envios=None) -> Comparacion:
    prepared = _prepared(tmp_path, envios)
    assert prepared.status == STATUS_OK, prepared.messages
    return run(prepared.payload, {}).report


def test_se_registra_con_una_llamada() -> None:
    registry = register(ToolRegistry())
    assert [t.key for t in registry.list()] == ["carrier_lane_compare"]
    assert registry.get("carrier_lane_compare").requires_data


def test_declara_su_contrato_de_columnas() -> None:
    assert "transportista" in CARRIER_LANE_COMPARE.required_columns
    assert CARRIER_LANE_COMPARE.optional_columns == ("fecha_prometida", "fecha_entrega")


def test_un_brief_en_castellano_rutea_al_tool() -> None:
    ranked = build_fletes_tools().match("quiero comparar transportistas por ruta y ver el costo de flete")
    assert ranked[0][0].key == "carrier_lane_compare"


def test_un_brief_en_ingles_rutea_al_tool() -> None:
    ranked = build_fletes_tools().match("compare carriers by freight cost per lane")
    assert ranked[0][0].key == "carrier_lane_compare"


def test_prepare_sin_archivo_pide_datos() -> None:
    prepared = prepare(JobRequest(brief="comparar transportistas"), provider=None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "envio_id" in prepared.messages[0]


def test_prepare_devuelve_el_error_exacto_del_loader(tmp_path) -> None:
    path = tmp_path / "envios.csv"
    path.write_text(HEADER + "\nE1,A,S,C,2026-01-05,abc,CLP,10,,\n", encoding="utf-8")
    prepared = prepare(JobRequest(brief="x", data_path=str(path)), provider=None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "fila 2, columna costo" in prepared.messages[0]


def test_pesos_y_min_envios_vienen_de_params(tmp_path) -> None:
    prepared = _prepared(tmp_path, peso_costo="0.3", peso_servicio="0.7", min_envios=5)
    assert prepared.status == STATUS_OK
    assert prepared.payload["pesos"] == Pesos(Decimal("0.3"), Decimal("0.7"))
    assert prepared.payload["min_envios"] == 5


def test_pesos_que_no_suman_uno_piden_aclaracion(tmp_path) -> None:
    prepared = _prepared(tmp_path, peso_costo="0.5")
    assert prepared.status == STATUS_NEEDS_CLARIFICATION
    assert "sumar 1" in prepared.messages[0]


def test_parametros_ilegibles_piden_aclaracion(tmp_path) -> None:
    assert _prepared(tmp_path, peso_costo="mucho", peso_servicio="0.4").status == STATUS_NEEDS_CLARIFICATION
    assert _prepared(tmp_path, min_envios=0).status == STATUS_NEEDS_CLARIFICATION
    assert _prepared(tmp_path, min_envios="diez").status == STATUS_NEEDS_CLARIFICATION


def test_caso_integral_recomienda_lo_esperado(tmp_path) -> None:
    comp = _comparacion(tmp_path)
    assert comp.envios == 60
    por_ruta = {r.ruta: r for r in comp.rutas}
    assert por_ruta["Santiago -> Antofagasta"].motivo == RUTA_SIN_ALTERNATIVA
    assert por_ruta["Valparaiso -> Santiago"].motivo == RUTA_MENOS_DE_DOS
    (rec,) = comp.recomendaciones
    assert (rec.ruta, rec.actual.transportista, rec.mejor.transportista) == (
        "Santiago -> Concepcion", "Rapido", "Lento")
    esperado_periodo = (Decimal(150) / Decimal(100) - Decimal(120) / Decimal(100)) * Decimal(15 * 100)
    assert rec.ahorro_periodo == esperado_periodo
    dias = 14 * 4 + 1  # Rapido va del dia 0 al 56
    assert rec.ahorro_anual == esperado_periodo * Decimal(365) / Decimal(dias)


def test_run_resume_la_corrida(tmp_path) -> None:
    prepared = _prepared(tmp_path)
    produced = run(prepared.payload, {})
    assert "3 ruta" in produced.summary
    assert "1 recomendacion" in produced.summary


def test_qa_pasa_el_caso_integral(tmp_path) -> None:
    assert qa(_comparacion(tmp_path)) == []


def test_qa_veta_cuando_ninguna_ruta_es_comparable(tmp_path) -> None:
    comp = _comparacion(tmp_path, lote("A", 12) + lote("B", 12, destino="Antofagasta"))
    issues = qa(comp)
    assert issues and "problema de dato" in issues[0]


def test_qa_detecta_kg_que_no_cuadran(tmp_path) -> None:
    from dataclasses import replace
    comp = _comparacion(tmp_path)
    roto = replace(comp, kg_total=comp.kg_total + 1)
    assert any("kg" in issue for issue in qa(roto))


def test_deliver_escribe_matriz_e_informe(tmp_path) -> None:
    comp = _comparacion(tmp_path)
    out = deliver(comp, tmp_path / "out", "Cliente X")
    assert set(out) == {"matriz", "informe"}
    with out["matriz"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    express = next(r for r in rows if r["transportista"] == "Express")
    assert express["elegible"] == "no" and express["motivo"] == "pocos_envios"
    assert express["puntaje"] == ""          # vacio, nunca 0
    lento = next(r for r in rows if r["transportista"] == "Lento")
    assert lento["peso_costo"] == "0.6" and lento["peso_servicio"] == "0.4"
    assert lento["indice_costo"] and lento["indice_servicio"] and lento["puntaje"]
    informe = out["informe"].read_text(encoding="utf-8")
    assert "Cliente X" in informe
    assert "Santiago -> Concepcion" in informe and "Lento" in informe
    assert "OTD" in informe and "OTIF" not in informe.replace("no OTIF", "")
    assert "sin alternativa" in informe


def test_options_ofrece_las_recomendaciones(tmp_path) -> None:
    outcome = options(_comparacion(tmp_path))
    assert outcome.status == OPTIONS
    assert "Lento" in outcome.options[0].label
    assert verify_guided(outcome) == []


def test_sin_recomendaciones_entrega_un_handoff_que_cumple_el_contrato(tmp_path) -> None:
    comp = _comparacion(tmp_path, lote("A", 15, costo="100") + lote("B", 12, costo="150"))
    outcome = options(comp)
    assert outcome.status == HANDOFF
    assert verify_guided(outcome) == []
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `pytest tests/test_fletes_pack.py -q`
Expected: FAIL con `ImportError: cannot import name 'build_fletes_tools' from 'packs.fletes'`

- [ ] **Step 3: Implementar `tool.py`**

`packs/fletes/tool.py`:

```python
"""El comparador de transportistas, enchufado al contrato Tool del core.

Igual que source_to_pay: un `register()` lo pone en el mismo ciclo
prepare -> run -> qa -> deliver, con el mismo veto "QA falla => no hay entregable".
No escribe en ningun sistema: entrega opciones para que una persona decida.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nosilo_core.guided import ExecutionOption, GuidedOutcome, HandoffPacket, Residual, as_handoff, as_options
from nosilo_core.llm import LLMProvider
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from nosilo_core.types import STATUS_NEEDS_CLARIFICATION, STATUS_NEEDS_DATA, STATUS_OK, JobRequest

from .engine import (
    MIN_ENVIOS_DEFAULT,
    MOTIVO_POCOS_ENVIOS,
    MOTIVO_SIN_FECHAS,
    RUTA_ACTUAL_ES_EL_MEJOR,
    RUTA_ACTUAL_NO_ELEGIBLE,
    RUTA_MENOS_DE_DOS,
    RUTA_SIN_ALTERNATIVA,
    Celda,
    Comparacion,
    Pesos,
    comparar,
)
from .loader import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, LoadError, load_envios

TOOL_KEY = "carrier_lane_compare"

MOTIVOS_TEXTO = {
    MOTIVO_POCOS_ENVIOS: "menos envios fechados que el minimo",
    MOTIVO_SIN_FECHAS: "sin fecha prometida y de entrega",
    RUTA_SIN_ALTERNATIVA: "sin alternativa: un solo transportista",
    RUTA_ACTUAL_NO_ELEGIBLE: "el transportista actual no tiene muestra suficiente",
    RUTA_MENOS_DE_DOS: "menos de dos transportistas con muestra suficiente",
    RUTA_ACTUAL_ES_EL_MEJOR: "el transportista actual ya es el mejor puntaje",
}


class _ParamError(ValueError):
    pass


def _pesos(params: dict) -> Pesos:
    try:
        costo = Decimal(str(params.get("peso_costo", Pesos().costo)))
        servicio = Decimal(str(params.get("peso_servicio", Pesos().servicio)))
    except InvalidOperation:
        raise _ParamError("peso_costo y peso_servicio deben ser numeros (por ejemplo 0.6 y 0.4)") from None
    if not (costo.is_finite() and servicio.is_finite()):
        raise _ParamError("peso_costo y peso_servicio deben ser numeros finitos")
    try:
        return Pesos(costo, servicio)
    except ValueError as exc:
        raise _ParamError(f"{exc}: indica peso_costo y peso_servicio juntos") from None


def _min_envios(params: dict) -> int:
    raw = params.get("min_envios", MIN_ENVIOS_DEFAULT)
    try:
        value = int(str(raw))
    except ValueError:
        raise _ParamError(f"min_envios debe ser un entero (es {raw!r})") from None
    if value < 1:
        raise _ParamError(f"min_envios debe ser >= 1 (es {value})")
    return value


def prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status=STATUS_NEEDS_DATA,
            messages=["carrier_lane_compare necesita un CSV de envios con columnas: "
                      + ", ".join(REQUIRED_COLUMNS) + " (opcionales: " + ", ".join(OPTIONAL_COLUMNS) + ")"],
        )
    try:
        envios = load_envios(request.data_path)
    except LoadError as exc:
        return Prepared(status=STATUS_NEEDS_DATA, messages=[str(exc)])
    params = request.params or {}
    try:
        pesos = _pesos(params)
        min_envios = _min_envios(params)
    except _ParamError as exc:
        return Prepared(status=STATUS_NEEDS_CLARIFICATION, messages=[str(exc)])
    return Prepared(status=STATUS_OK, payload={"envios": envios, "pesos": pesos, "min_envios": min_envios})


def run(prepared: object, params: dict) -> Produced:
    payload: dict = prepared  # type: ignore[assignment]
    comp = comparar(payload["envios"], payload["pesos"], payload["min_envios"])
    return Produced(report=comp, summary=_summary(comp))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1')):,}"


def _summary(comp: Comparacion) -> str:
    return (
        f"{len(comp.rutas)} ruta(s), {comp.rutas_comparables} comparable(s), "
        f"{len(comp.recomendaciones)} recomendacion(es); ahorro anualizado {_money(comp.ahorro_anual_total)} {comp.moneda}"
    )


def qa(report: object) -> list[str]:
    comp: Comparacion = report  # type: ignore[assignment]
    issues: list[str] = []
    if comp.rutas_comparables == 0:
        issues.append(
            f"ninguna ruta tiene dos transportistas con al menos {comp.min_envios} envios fechados: "
            "es un problema de dato (faltan fechas o historia), no una ausencia de oportunidades"
        )
    for ruta in comp.rutas:
        for c in ruta.celdas:
            if c.puntaje is not None and not Decimal(0) <= c.puntaje <= Decimal(1):
                issues.append(f"{ruta.ruta} / {c.transportista}: puntaje {c.puntaje} fuera de [0, 1]")
            if c.elegible != (c.puntaje is not None):
                issues.append(f"{ruta.ruta} / {c.transportista}: elegibilidad y puntaje no coinciden")
    kg_celdas = sum((c.kg_total for r in comp.rutas for c in r.celdas), Decimal(0))
    if kg_celdas != comp.kg_total:
        issues.append(f"los kg por ruta ({kg_celdas}) no suman los kg del archivo ({comp.kg_total})")
    return issues


def _fmt(value: Decimal | None, places: int) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal(1).scaleb(-places)))


MATRIZ_COLUMNAS = (
    "ruta", "transportista", "es_actual", "envios", "kg_total", "costo_total", "costo_kg",
    "envios_fechados", "otd", "elegible", "motivo", "indice_costo", "indice_servicio",
    "peso_costo", "peso_servicio", "puntaje",
)


def _fila(ruta_actual: str, c: Celda, comp: Comparacion) -> list[str]:
    return [
        c.ruta, c.transportista, "si" if c.transportista == ruta_actual else "no", str(c.envios),
        _fmt(c.kg_total, 2), _fmt(c.costo_total, 2), _fmt(c.costo_kg, 4), str(c.envios_fechados),
        _fmt(c.otd, 4), "si" if c.elegible else "no", c.motivo or "",
        _fmt(c.indice_costo, 4), _fmt(c.indice_servicio, 4),
        str(comp.pesos.costo), str(comp.pesos.servicio), _fmt(c.puntaje, 4),
    ]


def deliver(report: object, out_dir: Path, client: str) -> dict[str, Path]:
    comp: Comparacion = report  # type: ignore[assignment]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    matriz = out_dir / "fletes_matriz.csv"
    with matriz.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MATRIZ_COLUMNAS)
        for ruta in comp.rutas:
            for c in ruta.celdas:
                writer.writerow(_fila(ruta.actual, c, comp))

    informe = out_dir / "fletes_informe.md"
    informe.write_text(_render(comp, client), encoding="utf-8")
    return {"matriz": matriz, "informe": informe}


def _orden_recomendaciones(comp: Comparacion):
    return sorted(
        comp.recomendaciones,
        key=lambda r: (r.ahorro_anual is None, -(r.ahorro_anual or Decimal(0)), -r.ahorro_periodo),
    )


def _render(comp: Comparacion, client: str) -> str:
    lines = [
        f"# Comparador de transportistas - {client}",
        "",
        f"- Envios analizados: **{comp.envios}** en **{len(comp.rutas)}** ruta(s); moneda **{comp.moneda}**",
        f"- Pesos del puntaje: costo **{comp.pesos.costo}**, servicio (OTD) **{comp.pesos.servicio}**",
        f"- Muestra minima para recomendar: **{comp.min_envios}** envios con fecha prometida y de entrega",
        f"- Ahorro anualizado de las recomendaciones: **{_money(comp.ahorro_anual_total)} {comp.moneda}**",
        "",
        "Servicio = OTD (entregado en o antes de la fecha prometida), no OTIF: un archivo de fletes "
        "no dice si el pedido llego completo.",
        "",
        "## Recomendaciones",
        "",
    ]
    recs = _orden_recomendaciones(comp)
    if not recs:
        lines.append("Ninguna.")
    for r in recs:
        anual = (f"{_money(r.ahorro_anual)} {comp.moneda}/ano" if r.ahorro_anual is not None
                 else f"no se anualiza (periodo de {r.dias_periodo} dias)")
        lines.append(
            f"- **{r.ruta}**: pasar de {r.actual.transportista} a {r.mejor.transportista}. "
            f"Ahorro del periodo {_money(r.ahorro_periodo)} {comp.moneda} ({anual}); "
            f"OTD {r.delta_otd_pp.quantize(Decimal('0.1')):+} pp; "
            f"puntaje {_fmt(r.mejor.puntaje, 3)} contra {_fmt(r.actual.puntaje, 3)}."
        )
    lines += ["", "## Rutas sin recomendacion", ""]
    sin = [r for r in comp.rutas if r.recomendacion is None]
    if not sin:
        lines.append("Ninguna.")
    for r in sin:
        lines.append(f"- **{r.ruta}**: {MOTIVOS_TEXTO.get(r.motivo or '', r.motivo or '')}")
    lines += [
        "",
        "Los nombres de transportista se agrupan sin distinguir mayusculas ni espacios; "
        "alias distintos (por ejemplo una razon social y una marca) cuentan como dos transportistas.",
        "",
    ]
    return "\n".join(lines)


def options(report: object) -> GuidedOutcome:
    comp: Comparacion = report  # type: ignore[assignment]
    recs = _orden_recomendaciones(comp)
    if recs:
        return as_options(
            summary=_summary(comp),
            options=[
                ExecutionOption(
                    label=f"Mover {r.ruta} de {r.actual.transportista} a {r.mejor.transportista}",
                    summary=(f"Ahorro del periodo {_money(r.ahorro_periodo)} {comp.moneda}; "
                             f"OTD {r.delta_otd_pp.quantize(Decimal('0.1')):+} pp"),
                    score=float(r.ahorro_anual if r.ahorro_anual is not None else r.ahorro_periodo),
                    tradeoffs=(f"puntaje {_fmt(r.mejor.puntaje, 3)} contra {_fmt(r.actual.puntaje, 3)} "
                               f"con pesos costo {comp.pesos.costo} / servicio {comp.pesos.servicio}"),
                )
                for r in recs
            ],
            residuals=[
                Residual(
                    description="Negociar con los transportistas y reasignar el volumen",
                    risk_if_skipped="Se sigue pagando de mas en las rutas recomendadas",
                )
            ],
        )
    return as_handoff(
        summary=_summary(comp) + " - sin cambios recomendados",
        packets=[
            HandoffPacket(
                title="Revisar la matriz de transportistas",
                steps=[
                    "Abrir fletes_matriz.csv y revisar las rutas sin recomendacion y su motivo.",
                    "Si falta muestra, sumar historia o fechas de entrega y volver a correr.",
                    "Si el actual ya es el mejor, usar la matriz como referencia en la proxima negociacion.",
                ],
            )
        ],
        residuals=[
            Residual(
                description="Completar fechas de entrega donde faltan",
                risk_if_skipped="Transportistas sin fechas quedan fuera de la comparacion",
            )
        ],
    )


CARRIER_LANE_COMPARE = Tool(
    key=TOOL_KEY,
    title="Comparador de transportistas por ruta (fletes)",
    description=(
        "Compara los transportistas del propio cliente ruta por ruta: costo por kg, "
        "puntualidad (OTD) y un puntaje desglosado, con recomendacion de reasignacion y su ahorro."
    ),
    intent_keywords=(
        "comparar transportistas", "comparador de transportistas", "transportistas por ruta",
        "costo de flete", "flete por ruta", "compare carriers", "carrier comparison",
        "freight cost per lane", "carrier scorecard", "lane carrier",
    ),
    requires_data=True,
    prepare=prepare,
    run=run,
    qa=qa,
    deliver=deliver,
    options=options,
    required_columns=REQUIRED_COLUMNS,
    optional_columns=OPTIONAL_COLUMNS,
)


def register(registry: ToolRegistry) -> ToolRegistry:
    """Toda la superficie de integracion del pack: una llamada, sin tocar el orquestador."""
    registry.register(CARRIER_LANE_COMPARE)
    return registry


def build_fletes_tools() -> ToolRegistry:
    """Un registry con solo este pack."""
    return register(ToolRegistry())
```

- [ ] **Step 4: Reemplazar `packs/fletes/__init__.py`**

```python
"""Fletes: compara los transportistas del propio cliente, ruta por ruta.

El tercer uso del core (despues de supply chain y source-to-pay). Lee el historial
de envios del cliente, mide costo por kg y puntualidad (OTD) por transportista y
ruta, y recomienda reasignar volumen con el ahorro calculado. No cotiza, no
reserva y no escribe en ningun sistema: es una capa de decision.

``loader``
    El CSV de envios con validacion estricta: una fila mala rechaza el archivo
    con fila, columna y motivo.
``engine``
    Puro, sin reloj, Decimal. Un transportista sin fechas tiene OTD vacio, nunca
    cero, y queda fuera del puntaje con su motivo a la vista.
``tool``
    Un ``register()`` lo pone en el ciclo prepare -> run -> qa -> deliver.

Importa solo stdlib y ``nosilo_core``: ``tests/test_pack_boundary.py`` lo exige.
"""

from .engine import (
    MIN_DIAS_ANUALIZAR,
    MIN_ENVIOS_DEFAULT,
    Celda,
    Comparacion,
    Pesos,
    Recomendacion,
    ResultadoRuta,
    comparar,
)
from .loader import Envio, LoadError, load_envios
from .tool import CARRIER_LANE_COMPARE, TOOL_KEY, build_fletes_tools, register

__all__ = [
    "CARRIER_LANE_COMPARE",
    "Celda",
    "Comparacion",
    "Envio",
    "LoadError",
    "MIN_DIAS_ANUALIZAR",
    "MIN_ENVIOS_DEFAULT",
    "Pesos",
    "Recomendacion",
    "ResultadoRuta",
    "TOOL_KEY",
    "build_fletes_tools",
    "comparar",
    "load_envios",
    "register",
]
```

- [ ] **Step 5: Correr y verificar que pasan**

Run: `pytest tests/test_fletes_pack.py tests/test_fletes_engine.py tests/test_fletes_loader.py tests/test_pack_boundary.py tests/test_core_boundary.py tests/test_s2p_pack.py -q`
Expected: todos PASS. Si `test_un_brief_*_rutea_al_tool` falla, revisar `ToolRegistry.match` en `nosilo_core/registry.py` (cuenta coincidencias de keyword en el brief en minúsculas) y ajustar `intent_keywords`, no el test.

- [ ] **Step 6: Lint**

Run: `ruff check packs/fletes tests/test_fletes_pack.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add packs/fletes/tool.py packs/fletes/__init__.py tests/test_fletes_pack.py
git commit -m "feat(fletes): tool carrier_lane_compare con matriz, informe y opciones

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Verificación completa y PR borrador

**Files:**
- Ninguno nuevo.

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: rama `feat/pack-fletes` pusheada y PR borrador.

- [ ] **Step 1: Cobertura del pack**

Run: `pytest tests/test_fletes_loader.py tests/test_fletes_engine.py tests/test_fletes_pack.py --cov=packs/fletes --cov-report=term-missing -q`
Expected: PASS y cobertura de `packs/fletes` >= 90%. Las líneas sin cubrir se cubren con un test o se justifican en el PR.

- [ ] **Step 2: Suite completa**

Run: `pytest tests/ -q -x -p no:cacheprovider`
Expected: todo PASS (los tests marcados `integracion` se excluyen solos). Si algo ajeno al pack falla, comparar contra `origin/main` sin la rama antes de tocar nada.

- [ ] **Step 3: Lint del repo entero**

Run: `ruff check .`
Expected: `All checks passed!`

- [ ] **Step 4: Confirmar que no se tocó nada fuera del alcance**

Run: `git diff --stat origin/main...HEAD`
Expected: solo `packs/fletes/*`, `tests/test_fletes_*.py`, `tests/_fletes_fixtures.py`, `docs/superpowers/specs/2026-10-11-pack-fletes-design.md`, `docs/superpowers/plans/2026-10-11-pack-fletes.md`.

- [ ] **Step 5: Push y PR borrador**

```bash
git push -u origin feat/pack-fletes
gh pr create --draft --base main --title "feat(fletes): pack comparador de transportistas por ruta" --body "$(cat <<'EOF'
## Que es
Tercer pack sobre `nosilo_core` (`packs/fletes/`): lee un CSV de envios del cliente y devuelve, por ruta, la matriz transportista x ruta con costo/kg, OTD y un puntaje costo/servicio desglosado, mas una recomendacion de reasignacion con su ahorro. No cotiza, no reserva, no escribe en ningun sistema.

Spec: `docs/superpowers/specs/2026-10-11-pack-fletes-design.md` - Plan: `docs/superpowers/plans/2026-10-11-pack-fletes.md`

## Reglas que fija
- OTD vacio es `None`, nunca 0; se llama OTD, no OTIF.
- Muestra minima sobre envios FECHADOS (default 10).
- Puntaje normalizado contra el mejor de la ruta, nunca solo: viaja con sus dos indices y los pesos.
- Ahorro con signo; sin anualizar bajo 28 dias.
- Solo stdlib + `nosilo_core` (lo exige `tests/test_pack_boundary.py`). No se registra en `scm_agent/tools.py`.

## Test plan
- [ ] `pytest tests/test_fletes_*.py` verde
- [ ] `pytest tests/` verde
- [ ] `ruff check .` limpio
- [ ] `git diff --stat origin/main...HEAD` solo toca el pack, sus tests y docs

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: URL del PR. Después, revisar el estado real de la CI en el PR (no suponerlo).
