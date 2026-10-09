"""`KERN_HORIZONTE_POR_SKU.md` no puede despegarse de lo que se midio.

Este documento publica la palanca accionable mas grande de la rama (+16,2 puntos), asi
que es el que mas facil se cita de mas. Estos tests fijan las cuatro formas en que se
vuelve falso:

1. **la palanca deja de pagar** -- o deja de pagar en TODOS los mundos, que es lo que la
   separa de un promedio con dos mundos buenos;
2. **el carril desplegable deja de ser equivalente al diagnostico** -- si conocer el
   futuro verdadero empezara a comprar algo, la palanca dejaria de ser desplegable con
   los datos que el cliente tiene, que es la mitad del hallazgo;
3. **el ahorro deja de ser puro** -- si el fill cayera, ya no seria "compra menos y sirve
   lo mismo" sino un intercambio, y el documento estaria vendiendo otra cosa;
4. **se publica el numero sin su limite** -- en el marcador TODOS los SKU mueren la misma
   semana, asi que el +16,2 es el techo de la idea, no una promesa.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "documentation" / "lineas_base" / "horizonte_por_sku.json"
DOC = RAIZ / "documentation" / "KERN_HORIZONTE_POR_SKU.md"


def _plano(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("−", "-").replace("**", "").replace("*", ""))


@pytest.fixture(scope="module")
def datos() -> dict:
    return json.loads(BASE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> str:
    return _plano(DOC.read_text(encoding="utf-8"))


def test_la_palanca_paga_en_TODOS_los_mundos(datos, doc):
    """Un promedio puede venir de dos mundos buenos y tres neutros. Aca no: el documento
    publica 5/5 y eso es lo que lo hace citable."""
    est = datos["resumen"]["horizonte_estimado"]
    assert est["mejora_vs_kern"] > 0.10, (
        f"la palanca cayo a {est['mejora_vs_kern']:+.4f}; el documento publica +0,1618"
    )
    n = len(datos["parametros"]["semillas"])
    assert est["mundos_que_mejoran"] == n, (
        f"mejora en {est['mundos_que_mejoran']}/{n} mundos, no en todos"
    )
    for fila in datos["por_semilla"]:
        assert fila["horizonte_estimado"]["capture"] > fila["kern"]["capture"], (
            f"semilla {fila['seed']}: el carril topeado no le gana al control"
        )
    assert "5/5" in doc


def test_saber_el_futuro_verdadero_NO_compra_nada(datos, doc):
    """El hallazgo de H1, y la razon por la que la palanca es DESPLEGABLE.

    Si el carril que ve la demanda futura verdadera empezara a sacarle una ventaja real
    al que usa la media observada, la palanca pasaria a necesitar informacion que nadie
    tiene y el documento habria que reescribirlo entero.
    """
    r = datos["resumen"]
    ventaja_del_real = r["horizonte_real"]["capture"] - r["horizonte_estimado"]["capture"]
    assert ventaja_del_real < 0.02, (
        f"el carril con futuro verdadero saca {ventaja_del_real:+.4f} sobre el "
        "desplegable; H1 afirma que son equivalentes"
    )
    # Y por semilla: la diferencia es ruido salvo en los dos mundos que el documento nombra.
    grandes = [
        f["seed"] for f in datos["por_semilla"]
        if abs(f["horizonte_estimado"]["capture"] - f["horizonte_real"]["capture"]) > 0.02
    ]
    assert grandes == [1000, 1002], (
        f"los mundos donde los dos carriles difieren cambiaron: {grandes}; el documento "
        "nombra 1000 y 1002 y llama ruido al resto"
    )
    assert "los dos carriles son equivalentes" in doc


def test_el_ahorro_es_puro_y_no_un_intercambio(datos, doc):
    """Compra menos, inmoviliza mucho menos, y el servicio NO se mueve. Si el fill
    cayera, el documento estaria describiendo un intercambio y no un ahorro."""
    r = datos["resumen"]
    k, h = r["kern"], r["horizonte_estimado"]
    assert h["unidades_pedidas"] < k["unidades_pedidas"]
    assert h["procurement"] < k["procurement"]
    assert h["pagado_no_vendido_usd"] < 0.70 * k["pagado_no_vendido_usd"], (
        "el capital inmovilizado dejo de caer ~40%"
    )
    assert abs(h["fill"] - k["fill"]) < 0.01, (
        f"el fill se movio {h['fill'] - k['fill']:+.4f}: ya no es 'sirve lo mismo'"
    )
    ahorro = k["procurement"] - h["procurement"]
    exceso = h["shortage"] - k["shortage"]
    assert exceso < 0.10 * ahorro, (
        f"el quiebre extra ({exceso:,.0f}) dejo de ser chico contra el ahorro "
        f"({ahorro:,.0f}); el documento lo llama 'casi puro'"
    )


def test_el_documento_declara_que_en_el_marcador_todos_los_SKU_mueren_juntos(doc):
    """El limite que vuelve honesto al titular. Sin esto, +16,2 se lee como una promesa
    para cualquier cartera, y el marcador solo prueba el caso en que TODA la linea se
    discontinua a la vez."""
    assert "TODOS los SKU mueren la misma semana" in doc
    assert "no tienen ninguna" in doc
    assert "no se le promete a nadie" in doc
    # Y que declara que no es una implementacion todavia.
    assert "No es una implementacion" in doc.replace("ó", "o")


def test_el_tope_se_aplica_a_las_DOS_mitades_de_la_politica():
    """La regla se cumple en el codigo, no solo en la prosa: topear una sola mitad no
    cambia la orden, y por eso el documento dice 'las dos, o no sirve'."""
    from scripts.horizonte_por_sku import _topar
    from src.simworld.runners import Policy

    pol = Policy(reorder_point={"A": 100.0, "B": 50.0}, order_up_to={"A": 300.0, "B": 90.0})
    topada = _topar(pol, {"A": 40.0})
    assert topada.reorder_point["A"] == 40.0 and topada.order_up_to["A"] == 40.0
    # Un SKU sin `restante` queda INTACTO: topear con un cero que nadie midio seria
    # decirle que no compre nunca mas.
    assert topada.reorder_point["B"] == 50.0 and topada.order_up_to["B"] == 90.0
    # Y un restante negativo no puede volverse un tope negativo.
    assert _topar(pol, {"A": -5.0}).order_up_to["A"] == 0.0
