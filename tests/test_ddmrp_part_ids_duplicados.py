"""Un ``part_id`` repetido no puede producir una orden de compra en silencio.

``prepare_records`` arma una fila por linea del CSV, sin groupby -- el mismo
contrato que ``plan_liquidation`` documenta. Pero ``run`` despues resuelve tres
cosas por ``part_id`` con diccionarios: el proveedor (``supplier_by_sku``), el lead
time (``lead_time_days``) y la vuelta de la linea de OC a su parte (``by_sku``).
Con el id repetido los tres se rompen a la vez, y ninguno avisa:

  * gana el proveedor de la ULTIMA fila para TODAS,
  * gana el DLT de la ULTIMA fila, asi que ``build_proposal`` fecha la llegada de
    una parte bufferizada a 7 dias con el lead time de 60 de otra,
  * ``by_sku`` colapsa a una sola entrada: las dos ``DdmrpPart`` reciben el MISMO
    ``po_line`` (el de la ultima), y la otra linea queda huerfana en el deck.

El resultado no es un numero raro que alguien mira dos veces: es una OC ejecutable
con el proveedor equivocado, la fecha equivocada y la cantidad duplicada. Un
comprador la manda.

QUE HACE EL JOB CON ESO, y por que cambio. Agrupar sigue sin ser opcion: en DDMRP
una parte tiene UN buffer, y dos filas con lead time de 7 y de 60 no tienen
promedio. Pero la primera respuesta fue rechazar el archivo entero, y eso era
demasiado ancho. El BUFFER de cada fila es honesto: sale de SU propio ADU y SU
propio DLT y de nada mas, igual que ``plan_liquidation`` mantiene a cada fila
sobre su costo y su demanda. Lo unico ambiguo es la orden de compra. En un
producto de autoservicio no hay nadie al lado del cliente para explicarle por que
su CSV no produjo nada, asi que matar la corrida entera por un residuo es el mismo
error que ``verify()`` se niega a cometer con una linea sin proveedor.

La conducta que estos tests fijan: no se agrupa nada, no se descarta ninguna fila,
se retiene el UNICO artefacto que no se puede resolver -- la OC -- y el entregable
lo dice fuerte, con los ids nombrados.
"""

from datetime import date

import pandas as pd

from jobs import ddmrp_job as ddj

_TODAY = date(2026, 1, 1)


def _df(part_ids: list[str], dlts: list[float], suppliers: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "part_id": part_ids,
        "adu": [10.0] * len(part_ids),
        "dlt": dlts,
        "on_hand": [0.0] * len(part_ids),
        "on_order": [0.0] * len(part_ids),
        "qualified_demand": [0.0] * len(part_ids),
        "supplier": suppliers,
    })


def _reporte(part_ids, dlts, suppliers):
    return ddj.run(ddj.prepare_records(_df(part_ids, dlts, suppliers)), today=_TODAY)


# -- nada se ordena por un id ambiguo -----------------------------------------


def test_ninguna_fila_de_un_id_repetido_recibe_linea_de_oc():
    """El filo: con el id repetido las dos filas compartian la MISMA ``po_line``
    (la de la ultima leida). Ahora ninguna de las dos tiene."""
    reporte = _reporte(["P1", "P1"], [7.0, 60.0], ["ACME", "ZENITH"])

    assert [p.po_line for p in reporte.parts] == [None, None]
    assert reporte.proposal.all_lines == ()
    assert reporte.proposal.orders == ()
    assert reporte.proposal.unsourced == ()


def test_el_proveedor_de_la_ultima_fila_no_se_aplica_a_la_primera():
    """El cruce concreto que producia la OC ejecutable con el proveedor
    equivocado: ninguna linea puede salir a nombre de ZENITH ni de ACME."""
    reporte = _reporte(["P1", "P1"], [7.0, 60.0], ["ACME", "ZENITH"])

    proveedores = {ln.supplier for ln in reporte.proposal.all_lines}
    assert proveedores == set()
    assert reporte.proposal.restock_by_sku() == {}


def test_el_lead_time_de_una_fila_no_fecha_la_llegada_de_la_otra():
    """Cada fila conserva SU propio DLT en el buffer, que es lo que el cruce
    destruia: con la version rota las dos quedaban fechadas a 60 dias."""
    reporte = _reporte(["P1", "P1"], [7.0, 60.0], ["ACME", "ZENITH"])

    assert sorted(p.zones.dlt for p in reporte.parts) == [7.0, 60.0]
    # Y ninguna llegada fabricada de ningun lado.
    assert all(p.po_line is None for p in reporte.parts)


# -- y nada se pierde ---------------------------------------------------------


def test_ninguna_fila_se_descarta_ni_se_promedia():
    """Ni merge ni drop: las tres filas siguen en el plan con su propio buffer."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    assert reporte.n_parts == 3
    assert sorted(p.part_id for p in reporte.parts) == ["P1", "P1", "P2"]
    tog_por_dlt = {p.zones.dlt: p.zones.tog for p in reporte.parts}
    assert len(tog_por_dlt) == 3               # tres buffers distintos, ninguno promediado


def test_una_parte_sana_en_la_misma_tabla_conserva_su_oc_intacta():
    """El id ambiguo no contagia al resto del archivo: P2 sale con SU proveedor y
    SU lead time. Es la diferencia entre retener un artefacto y voltear la corrida."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    por_id = {p.part_id: p for p in reporte.parts if p.part_id == "P2"}
    linea = por_id["P2"].po_line
    assert linea is not None
    assert linea.supplier == "BETA"
    assert (linea.expected_arrival - _TODAY).days == 30
    assert [ln.sku for ln in reporte.proposal.all_lines] == ["P2"]


# -- y se dice, fuerte --------------------------------------------------------


def test_el_reporte_nombra_los_ids_ambiguos_ordenados_y_sin_repetir():
    """Sin el nombre nadie encuentra la fila en el CSV, y un orden inestable no se
    puede diffear entre corridas."""
    reporte = _reporte(
        ["P2", "P1", "P2", "P1", "P3"],
        [7.0, 7.0, 60.0, 60.0, 30.0],
        ["A", "B", "C", "D", "E"],
    )

    assert reporte.ambiguous_part_ids == ("P1", "P2")


def test_el_deck_dice_que_esas_partes_NO_fueron_ordenadas():
    """Un cliente que no ve este hallazgo lee el plan como completo y se entera
    tarde. Tiene que estar el id, el hecho de que no se ordeno, y que hacer."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    md = ddj.build_deck(reporte, client="Acme").to_markdown()

    assert md.isascii()
    assert "P1" in md
    assert "NOT ordered" in md
    assert "nothing was merged" in md and "no row was dropped" in md
    assert "de-duplicate" in md.lower()


def test_el_resumen_ejecutivo_no_promete_ordenes_que_el_archivo_de_oc_no_trae():
    """El resumen es la unica linea que todos leen, y tiene que aguantar leerse
    sola: ``n_order`` cuenta lo que la SENAL pide comprar, y la propuesta puede
    traer menos. Sin la aclaracion, promete 3 ordenes y el CSV entrega 1."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    resumen = ddj.build_deck(reporte, client="Acme").summary

    assert "3 need an order" in resumen               # la senal, sin tocar
    assert "2 of those carry NO draft purchase order" in resumen
    assert reporte.proposal.n_lines == 1              # y es cierto: solo P2 sale


def test_la_fila_del_csv_operativo_explica_por_que_no_tiene_oc(tmp_path):
    """Cinco celdas vacias significan dos cosas distintas -- "no necesita orden" y
    "la orden se retuvo" -- y un comprador no las distingue mirandolas."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    buffers = pd.read_csv(ddj.write_operational(reporte, tmp_path)["csv"])
    ambigua = buffers[buffers["part_id"] == "P1"]

    assert len(ambigua) == 2
    assert all(str(r).strip() == "" or pd.isna(r) for r in ambigua["supplier"])
    assert all("more than one row" in str(r) for r in ambigua["order_reason"])


def test_un_id_ambiguo_no_es_una_falla_de_qa():
    """Misma postura que una linea sin proveedor: es un residuo que se devuelve
    visible, no una razon para suprimir el entregable entero."""
    reporte = _reporte(["P1", "P1", "P2"], [7.0, 60.0, 30.0], ["ACME", "ZENITH", "BETA"])

    assert ddj.verify(reporte) == []


def test_sin_duplicados_la_corrida_sigue_intacta():
    """El control: dos partes distintas conservan CADA UNA su proveedor, su lead
    time y su propia linea de OC, y no se reporta ningun id ambiguo."""
    reporte = _reporte(["P1", "P2"], [7.0, 60.0], ["ACME", "ZENITH"])
    por_parte = {p.part_id: p for p in reporte.parts}

    assert reporte.ambiguous_part_ids == ()
    assert por_parte["P1"].po_line is not None
    assert por_parte["P2"].po_line is not None
    assert por_parte["P1"].po_line.supplier == "ACME"
    assert por_parte["P2"].po_line.supplier == "ZENITH"
    # El lead time de cada linea es el de SU fila, no el de la ultima leida.
    assert (por_parte["P1"].po_line.expected_arrival - _TODAY).days == 7
    assert (por_parte["P2"].po_line.expected_arrival - _TODAY).days == 60
    # Y cada parte apunta a su PROPIA linea, no a una compartida.
    assert por_parte["P1"].po_line is not por_parte["P2"].po_line
    assert ddj.verify(reporte) == []
