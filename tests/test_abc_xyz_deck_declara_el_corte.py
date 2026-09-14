"""El corte tiene que llegar al ENTREGABLE, no morir en el dataclass.

T03 pide, con esas palabras, "el criterio de corte usado (ej. 80/15/5 de facturacion)".
``AbcXyzReport.cut_criterion`` ya existia y ya estaba testeado
(tests/test_abc_xyz_cut_criterion.py), pero se quedaba en la libreria: ni el CSV
operativo ni el deck lo decian. El cliente seguia leyendo "SKU-A es clase A" sin ver
que significa A, que es exactamente el defecto que aquel test fue escrito para matar,
arreglado a mitad de camino.

La propiedad falsable es la misma de siempre y es la que importa: el texto tiene que
MOVERSE con la corrida. Un deck que imprime 80/15/5 siempre pasaria un test de
presencia y estaria mintiendo sobre cualquier corrida que corto en otro lado.

Cubre tambien dos cifras que el deck sacaba de la nada: el nivel de servicio de la
clase A estaba hardcodeado en 98% y ``service_level_by_class`` (que src/classification.py
soporta y tres packs de config/verticals/ declaran) no llegaba al job.
"""

from __future__ import annotations

import csv

from jobs.abc_xyz_job import AbcXyzReport, build_deck, run, write_operational

_ITEMS = [
    {"product_id": "A1", "unit_cost": 50.0, "demand": [80.0] * 12},
    {"product_id": "B1", "unit_cost": 10.0, "demand": [30.0] * 12},
    {"product_id": "C1", "unit_cost": 2.0,
     "demand": [0.0, 9.0, 0.0, 20.0, 0.0, 1.0, 0.0, 15.0, 0.0, 3.0, 0.0, 2.0]},
]


def _deck_md(report: AbcXyzReport) -> str:
    return build_deck(report, client="Acme").to_markdown()


def test_el_deck_declara_el_corte_de_la_corrida():
    md = _deck_md(run(_ITEMS))

    assert "80/15/5" in md
    assert md.isascii()                      # cp1252-safe


def test_el_corte_del_deck_se_mueve_con_la_corrida():
    """Sin esto, un literal 80/15/5 en la plantilla aprobaria igual."""
    md = _deck_md(run(_ITEMS, abc_thresholds=(0.70, 0.90)))

    assert "70/20/10" in md
    assert "80/15/5" not in md


def test_un_reporte_sin_cortes_registrados_dice_not_recorded_y_no_inventa_uno():
    bare = AbcXyzReport(
        classifications=(), summary={}, n_skus=0, n_a=0, n_cz=0, a_value_share=0.0,
    )

    md = _deck_md(bare)

    assert "not recorded" in md
    assert "80/15/5" not in md, "un corte no registrado es ausente, no el de costumbre"


def test_el_csv_operativo_lleva_el_corte_en_cada_fila(tmp_path):
    """El CSV se reabre meses despues sin el deck al lado: la letra viaja, el corte tambien."""
    paths = write_operational(run(_ITEMS), tmp_path)

    with open(paths["csv"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows, "el CSV salio vacio"
    assert all("80/15/5" in r["cut_criterion"] for r in rows)
    # las columnas viejas siguen ahi: el cambio es aditivo
    assert {"product_id", "abc", "cell", "service_level", "policy"} <= set(rows[0])


def test_el_csv_de_un_reporte_sin_corte_deja_la_celda_vacia(tmp_path):
    bare = AbcXyzReport(
        classifications=run(_ITEMS).classifications, summary={}, n_skus=3, n_a=1,
        n_cz=0, a_value_share=1.0,
    )

    paths = write_operational(bare, tmp_path)
    with open(paths["csv"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert all(r["cut_criterion"] == "" for r in rows)


def test_el_nivel_de_servicio_de_la_clase_a_sale_de_las_clasificaciones():
    """El pack de repuestos-industriales sube A a 99%: el deck tiene que decir 99, no 98."""
    default_md = _deck_md(run(_ITEMS))
    pack_md = _deck_md(run(_ITEMS, service_level_by_class={"A": 0.99}))

    assert "at 98%" in default_md
    assert "at 99%" in pack_md
    assert "at 98%" not in pack_md, "el 98 era un literal en el deck, no un dato"


def test_service_level_by_class_llega_a_las_clasificaciones():
    report = run(_ITEMS, service_level_by_class={"A": 0.99})

    niveles_a = {c.service_level for c in report.classifications if c.abc == "A"}
    assert niveles_a == {0.99}
    # parcial: el pack toca A y B/C se quedan donde estaban
    assert {c.service_level for c in report.classifications if c.abc == "C"} == {0.90}


def test_un_portafolio_sin_clase_a_no_inventa_un_nivel_de_servicio():
    """default=None en el helper: sin clase A la frase se omite, no se rellena."""
    md = _deck_md(run([]))

    assert "SKUs at" not in md
    assert md.isascii()
