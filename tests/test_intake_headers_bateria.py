"""El marcador del auto-mapeo: cuanto detecta `detect_columns`, y cuanto mapea de mas.

Lee `tests/fixtures/intake_headers_labeled.csv` -- 238 columnas etiquetadas
repartidas en 28 exports sinteticos -- y corre `detect_columns` de verdad contra
cada uno. Los dos numeros:

  cobertura           campos canonicos detectados / campos presentes
  falsos positivos    distractores que igual mapearon / distractores

El segundo existe porque el primero solo es trampa facil: una tabla que mapea
todo a algo saca 100 % de cobertura y es inservible.

## De donde salen los pisos

**De correr esto, no de una meta.** Se fijan en lo medido y se mueven en una sola
direccion. Escribir aqui un numero que el corpus todavia no alcanza es
exactamente lo que `08-MEDICION.md` §4 documenta de los cinco entregables que
imprimian `3/3 coherence check(s) passed` sobre un `reorder_point` que nadie
suministro.

## Que NO dice este marcador

El corpus es una reconstruccion de convenciones de export LatAm, no una muestra
de exports reales de cliente -- ver el docstring de
`scripts/build_intake_headers_corpus.py`. Asi que 51 % es *cobertura contra un
corpus declarado*, y nunca *el 51 % de los archivos que llegan*. Citarlo de la
segunda forma seria inventar una poblacion.
"""

from __future__ import annotations

import csv
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pytest

from jobs.intake import (
    CANONICAL_OPTIONAL,
    CANONICAL_REQUIRED,
    RECEIPT_OPTIONAL,
    RECEIPT_REQUIRED,
    detect_columns,
)
from scripts.build_intake_headers_corpus import CABECERA_CSV, construir_corpus, filas_csv

FIXTURE = Path(__file__).parent / "fixtures" / "intake_headers_labeled.csv"

#: Los juegos reales de `jobs.intake`, no los de `CAMPOS_POR_FORMA`. El corpus
#: declara que campos METE en cada export; la bateria tiene que preguntarle a
#: `detect_columns` por todo lo que ese formulario acepta -- incluido `revenue`,
#: que el corpus nunca produce y que es justo por donde entra el falso positivo.
JUEGOS = {
    "demanda": (CANONICAL_REQUIRED, CANONICAL_OPTIONAL),
    "recepciones": (RECEIPT_REQUIRED, RECEIPT_OPTIONAL),
}

#: Medidos el 2026-08-20 contra `jobs/intake.py` en `origin/main` (79fdb10):
#: cobertura 79/154 = 0,5130 · falsos positivos 5/84 = 0,0595.
#: Trinquete: suben (o baja el techo) cuando la tabla mejore, nunca al reves.
PISO_COBERTURA = 0.51
TECHO_FALSOS_POSITIVOS = 0.06


@dataclass
class Marcador:
    aciertos: int = 0
    fallos: int = 0
    distractores: int = 0
    no_detectadas: list[tuple[str, str]] = field(default_factory=list)
    mapeadas_de_mas: list[tuple[str, str]] = field(default_factory=list)

    @property
    def cobertura(self) -> float:
        total = self.aciertos + self.fallos
        return self.aciertos / total if total else 0.0

    @property
    def tasa_falsos_positivos(self) -> float:
        return len(self.mapeadas_de_mas) / self.distractores if self.distractores else 0.0


def _exports() -> OrderedDict[str, list[dict[str, str]]]:
    with FIXTURE.open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    agrupado: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
    for fila in filas:
        agrupado.setdefault(fila["case_id"], []).append(fila)
    return agrupado


def _puntuar() -> Marcador:
    m = Marcador()
    for columnas in _exports().values():
        requeridos, opcionales = JUEGOS[columnas[0]["forma"]]
        # Una fila basta: `detect_columns` mira encabezados, no contenido.
        df = pd.DataFrame({c["encabezado"]: ["x"] for c in columnas})
        mapeo = detect_columns(df, required=requeridos, optional=opcionales).mapping

        for c in columnas:
            campo, encabezado = c["campo_canonico"], c["encabezado"]
            if campo == "ninguno":
                m.distractores += 1
                m.mapeadas_de_mas += [
                    (encabezado, k) for k, v in mapeo.items() if v == encabezado
                ]
            elif mapeo.get(campo) == encabezado:
                m.aciertos += 1
            else:
                m.fallos += 1
                m.no_detectadas.append((campo, encabezado))
    return m


@pytest.fixture(scope="module")
def marcador() -> Marcador:
    return _puntuar()


def test_el_fixture_esta_sincronizado_con_el_generador():
    """Un corpus congelado que ya no es lo que el generador produce miente dos veces.

    Va primero a proposito: si esto falla, los otros tres numeros son sobre un
    corpus que nadie puede reproducir. Se arregla corriendo
    `python scripts/build_intake_headers_corpus.py`.
    """
    with FIXTURE.open(encoding="utf-8") as f:
        en_disco = list(csv.reader(f))
    esperado = [list(CABECERA_CSV)] + [list(f) for f in filas_csv(construir_corpus())]
    assert en_disco == esperado, (
        "El fixture y el generador divergieron. Regenera con "
        "`python scripts/build_intake_headers_corpus.py` y revisa el diff: "
        "un corpus que cambia mueve el marcador sin que la tabla haya mejorado."
    )


def test_la_cobertura_no_baja_del_piso(marcador: Marcador):
    por_campo = Counter(campo for campo, _ in marcador.no_detectadas)
    detalle = " · ".join(f"{c}:{n}" for c, n in por_campo.most_common(5))
    assert marcador.cobertura >= PISO_COBERTURA, (
        f"cobertura {marcador.cobertura:.4f} < piso {PISO_COBERTURA}. "
        f"Campos que mas se pierden: {detalle}"
    )


def test_los_falsos_positivos_no_pasan_del_techo(marcador: Marcador):
    detalle = " · ".join(f"{h!r}->{k}" for h, k in marcador.mapeadas_de_mas[:5])
    assert marcador.tasa_falsos_positivos <= TECHO_FALSOS_POSITIVOS, (
        f"falsos positivos {marcador.tasa_falsos_positivos:.4f} > techo "
        f"{TECHO_FALSOS_POSITIVOS}. Columnas que mapearon sin serlo: {detalle}"
    )


def test_hallazgo_un_total_de_documento_entra_como_revenue(marcador: Marcador):
    """El unico falso positivo con consecuencia sobre una cifra, no sobre un nombre.

    `total` y `line_total` estan en los alias de `revenue`, y `revenue` existe
    solo para recuperar un costo unitario cuando el archivo no trae costo:
    `jobs/intake.py:465` lo deriva como `revenue / quantity`.

    Alcance real, que es mas estrecho de lo que parece: la derivacion es un
    `elif` sobre `"unit_cost" in m`, asi que un archivo CON columna de costo no
    se toca. Muerde en el que no la trae -- ahi un total de DOCUMENTO dividido
    por la cantidad de UNA linea da un costo unitario inflado, y el
    `IntakeQuality` no lo marca como sintetizado porque la columna si existia.

    Este test describe el estado de hoy, no lo aprueba. Cuando se arregle
    fallara, y ese es el momento de decidir el arreglo: `total` a secas fuera
    de los alias de `revenue`, o un desempate que exija que la columna tenga
    tantos valores distintos como lineas.
    """
    culpables = {h for h, campo in marcador.mapeadas_de_mas if campo == "revenue"}
    assert culpables, "El falso positivo documentado desaparecio: actualiza este test."
    assert all("total" in h.lower() for h in culpables), (
        f"Ademas del total, entran por revenue: {culpables}"
    )


def test_el_marcador_se_puede_imprimir(marcador: Marcador, capsys):
    """Correr la bateria con `-s` tiene que dar los dos numeros y sus fallos.

    Sin esto el marcador solo se ve cuando falla, que es cuando ya es tarde
    para usarlo como instrumento.
    """
    with capsys.disabled():
        print(
            f"\n  cobertura         {marcador.aciertos}/"
            f"{marcador.aciertos + marcador.fallos} = {marcador.cobertura:.4f}"
            f"\n  falsos positivos  {len(marcador.mapeadas_de_mas)}/"
            f"{marcador.distractores} = {marcador.tasa_falsos_positivos:.4f}"
        )
    assert marcador.aciertos + marcador.fallos > 0
