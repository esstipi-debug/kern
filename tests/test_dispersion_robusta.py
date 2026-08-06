"""La dispersion robusta: un pico aislado no puede dimensionar el buffer del ano.

**El bug que esto ataca, medido antes de escribir codigo.** El barrido de
simworld sobre 30 mundos de un ano dio: con el nivel 3 de suciedad completo
Kern captura 3,7% de la brecha entre un punto de reorden fijo y un oraculo;
quitando SOLO ``inject_unmarked_promo`` -- un pico de 4x en una semana, sin
ninguna columna que lo declare -- sube a 11,0%. Delta +7,3 puntos sobre un
margen de 6,0. El unico inyector del nivel que supera el ruido.

El mecanismo es aritmetico y no tiene nada de sutil: ``error_std`` es
``np.std``, que no es robusta. Un pico la domina, y esa sigma dimensiona el
stock de seguridad de las 52 semanas. El pico dura una.

Estos tests fijan las dos mitades del arreglo: que la robusta ignore el pico, y
que **no se aplique sola**. Cambiar la dispersion por defecto moveria el buffer
de todos los clientes a la vez sin que nadie lo pida.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from jobs import inventory_optimization as inv
from src.forecasting import forecast_demand


def _serie(n: int = 52, semilla: int = 11) -> list[float]:
    rng = np.random.default_rng(semilla)
    return [float(max(1.0, round(x))) for x in rng.normal(100, 12, n)]


def _con_pico(base: list[float], indice: int = 25, factor: float = 4.0) -> list[float]:
    """La misma serie con UNA promocion sin marcar, igual que inject_unmarked_promo."""
    out = list(base)
    out[indice] *= factor
    return out


# --------------------------------------------------------------------------
# El estimador
# --------------------------------------------------------------------------


def test_un_pico_aislado_dispara_la_std_y_no_mueve_la_robusta():
    """El numero que justifica todo el cambio."""
    base = _serie()
    limpio = forecast_demand(base)
    sucio = forecast_demand(_con_pico(base))

    assert sucio.error_std > limpio.error_std * 2.5, (
        f"el pico deberia inflar la std: {limpio.error_std:.2f} -> {sucio.error_std:.2f}"
    )
    assert sucio.error_std_robusto == pytest.approx(limpio.error_std_robusto, rel=0.05), (
        f"la robusta se movio: {limpio.error_std_robusto:.2f} -> "
        f"{sucio.error_std_robusto:.2f}"
    )


def test_sin_pico_las_dos_dispersiones_concuerdan():
    """Si la robusta diera algo muy distinto en datos limpios, no seria un
    estimador de la misma cantidad: seria otro criterio disfrazado."""
    r = forecast_demand(_serie())
    assert r.error_std_robusto == pytest.approx(r.error_std, rel=0.35)


def test_la_robusta_aguanta_varios_picos():
    """Punto de ruptura del 50%: hasta la mitad de periodos anomalos."""
    base = _serie()
    sucio = list(base)
    for i in (10, 25, 40):
        sucio[i] *= 4.0
    limpio, con_picos = forecast_demand(base), forecast_demand(sucio)
    assert con_picos.error_std_robusto == pytest.approx(limpio.error_std_robusto, rel=0.25)


def test_no_es_estimable_con_muy_pocos_periodos():
    """NaN, no cero. Un cero aqui se leeria como 'no hace falta buffer', que es
    una afirmacion; la verdad es 'no lo se'."""
    r = forecast_demand([10.0, 12.0, 11.0])
    assert math.isnan(r.error_std_robusto) or r.error_std_robusto > 0


def test_demanda_intermitente_no_colapsa_el_buffer():
    """Con muchos ceros la MAD es nula. Devolver 0 dejaria sin buffer justo a
    los SKU mas dificiles de pronosticar."""
    intermitente = [0.0] * 40 + [50.0, 0.0, 0.0, 60.0, 0.0, 0.0, 0.0, 55.0, 0.0, 0.0, 0.0, 0.0]
    r = forecast_demand(intermitente)
    if not math.isnan(r.error_std_robusto):
        assert r.error_std_robusto > 0
    entradas = r.to_engine_inputs(dispersion="robusta")
    assert entradas["demand_std_per_period"] > 0, "el buffer se colapso a cero"


# --------------------------------------------------------------------------
# La palanca: explicita, nunca sola
# --------------------------------------------------------------------------


def test_por_defecto_no_cambia_nada():
    """El test que hace seguro anadir esto. Si el default se moviera, el buffer
    de todos los clientes cambiaria en el siguiente despliegue."""
    r = forecast_demand(_con_pico(_serie()))
    assert (r.to_engine_inputs()["demand_std_per_period"]
            == r.to_engine_inputs(dispersion="clasica")["demand_std_per_period"]
            == r.error_std)


def test_pedir_la_robusta_la_usa():
    r = forecast_demand(_con_pico(_serie()))
    assert (r.to_engine_inputs(dispersion="robusta")["demand_std_per_period"]
            == r.error_std_robusto)


def test_una_dispersion_desconocida_falla_fuerte():
    """Un typo no puede caer silenciosamente en el comportamiento por defecto."""
    r = forecast_demand(_serie())
    with pytest.raises(ValueError, match="dispersion desconocida"):
        r.to_engine_inputs(dispersion="robusto")  # el nombre correcto es 'robusta'


def test_la_robusta_no_se_usa_cuando_no_es_estimable():
    """Cae a la clasica, no a cero ni a NaN."""
    r = forecast_demand([10.0, 12.0])
    sigma = r.to_engine_inputs(dispersion="robusta")["demand_std_per_period"]
    assert math.isfinite(sigma)


# --------------------------------------------------------------------------
# De punta a punta por el entregable
# --------------------------------------------------------------------------


def _cartera_con_pico() -> pd.DataFrame:
    q = _con_pico(_serie())
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(q), freq="W").astype(str),
        "product_id": ["SKU-A"] * len(q),
        "quantity": q,
        "unit_cost": [10.0] * len(q),
        "lead_time_days": [14] * len(q),
    })


def test_el_job_con_dispersion_robusta_pide_mucho_menos_buffer():
    df = _cartera_con_pico()
    clasica = inv.run(df).recommendations[0]
    robusta = inv.run(df, dispersion="robusta").recommendations[0]
    assert robusta.safety_stock < clasica.safety_stock / 2, (
        f"clasica={clasica.safety_stock:.1f} robusta={robusta.safety_stock:.1f}"
    )


def test_el_entregable_declara_con_que_dispersion_se_dimensiono():
    """Dos informes con buffers muy distintos parecerian contradecirse si el
    parametro que los separa no viaja en el propio informe."""
    df = _cartera_con_pico()
    assert inv.run(df).params["dispersion"] == "clasica"
    assert inv.run(df, dispersion="robusta").params["dispersion"] == "robusta"


def test_el_pronostico_no_cambia_solo_el_buffer():
    """La promocion ocurrio de verdad: el punto pronosticado debe reflejarla.
    Lo que no debe es sostener el buffer del ano entero."""
    df = _cartera_con_pico()
    clasica = inv.run(df).recommendations[0]
    robusta = inv.run(df, dispersion="robusta").recommendations[0]
    assert robusta.forecast == pytest.approx(clasica.forecast)
