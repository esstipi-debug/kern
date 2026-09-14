"""La banda de precio, y por qué el punto solo no alcanza.

`optimize_sku_price` devolvía **un número**. La estadística detrás es rigurosa
—empirical Bayes con shrinkage DerSimonian-Laird, IC por statsmodels con
fallback cerrado, y cuatro compuertas que se niegan a proponer precio sin
señal— pero todo eso mide **precisión**, no **identificación**.

Un intervalo de confianza dice cuán preciso es el estimador *suponiendo que el
modelo es correcto*. No dice nada sobre endogeneidad: el precio lo fija alguien
que condiciona sobre información de demanda que el modelo no observa, así que el
scatter precio-cantidad es un lugar de equilibrios, no una curva de demanda. El
`identified` de este código significa «hubo variación de precio», que es una
condición necesaria y no suficiente.

Dos consecuencias, y son las que estos tests fijan:

1. El punto viaja con su banda. La banda es el IC de la elasticidad propagado
   por la misma fórmula `p* = c·ε/(ε+1)`, que es monótona creciente en ε, e
   intersectado con las mismas restricciones que acotan el punto.
2. Si el precio actual ya cae dentro de la banda, el veredicto es
   **`no_change`**: la evidencia no distingue el precio actual del óptimo, y
   proponer un movimiento sería leerle precisión a un intervalo que no la tiene.
"""

from __future__ import annotations

import pytest

from src.elasticity_batch import SkuElasticityFit
from src.price_optimizer import (
    STATUS_NEEDS_DATA,
    STATUS_NO_CHANGE,
    STATUS_OK,
    optimize_sku_price,
)


def _fit(
    *,
    elasticity: float = -2.0,
    ci_low: float = -2.5,
    ci_high: float = -1.5,
    identified: bool = True,
    ci_excludes_zero: bool = True,
    price_low: float | None = None,
    price_high: float | None = None,
) -> SkuElasticityFit:
    return SkuElasticityFit(
        product_id="SKU-1",
        category="_default",
        elasticity=elasticity,
        se=0.25,
        ci_low=ci_low,
        ci_high=ci_high,
        r_squared=0.8,
        n_points=24,
        identified=identified,
        ci_excludes_zero=ci_excludes_zero,
        category_elasticity=elasticity,
        category_n_contributors=1,
        shrinkage_weight=1.0,
        shrunk_elasticity=elasticity,
        price_low=price_low,
        price_high=price_high,
    )


class TestBanda:
    def test_la_banda_sale_del_IC_y_contiene_al_punto(self) -> None:
        r = optimize_sku_price(_fit(), landed_cost=10.0, max_move_pct=None)
        assert r.proposed_price_low is not None and r.proposed_price_high is not None
        # p*(-2.5) = 10 * 2.5/1.5 = 16.67 ; p*(-1.5) = 10 * 1.5/0.5 = 30.00
        assert r.proposed_price_low == pytest.approx(16.667, abs=0.01)
        assert r.proposed_price_high == pytest.approx(30.0, abs=0.01)
        assert r.proposed_price_low <= r.proposed_price <= r.proposed_price_high

    def test_un_IC_mas_angosto_da_una_banda_mas_angosta(self) -> None:
        ancha = optimize_sku_price(_fit(ci_low=-3.0, ci_high=-1.2), landed_cost=10.0, max_move_pct=None)
        angosta = optimize_sku_price(_fit(ci_low=-2.1, ci_high=-1.9), landed_cost=10.0, max_move_pct=None)
        ancho = ancha.proposed_price_high - ancha.proposed_price_low
        angosto = angosta.proposed_price_high - angosta.proposed_price_low
        assert angosto < ancho

    def test_el_piso_levanta_el_extremo_bajo_de_la_banda(self) -> None:
        """El piso es una restricción, no una estimación: si la banda de
        evidencia baja del piso, la parte de abajo no es alcanzable.

        Y `floor_applied` NO se prende acá, con razón: describe el punto, y el
        punto (p* = 20) está por encima del piso (18). Que el piso muerda la
        banda sin morder el punto es el caso normal, no una inconsistencia —
        por eso la banda se lee en sus propios valores y no en el flag."""
        r = optimize_sku_price(_fit(), landed_cost=10.0, min_margin_pct=0.8, max_move_pct=None)
        assert r.proposed_price_low == pytest.approx(18.0, abs=0.01)  # 10 * 1.8
        assert r.proposed_price == pytest.approx(20.0, abs=0.01)
        assert r.floor_applied is False

    def test_el_techo_baja_el_extremo_alto_de_la_banda(self) -> None:
        r = optimize_sku_price(_fit(), landed_cost=10.0, max_price=20.0, max_move_pct=None)
        assert r.proposed_price_high == pytest.approx(20.0, abs=0.01)

    def test_la_banda_nunca_se_invierte(self) -> None:
        """Piso por encima del techo del IC: la banda colapsa al piso, no queda
        con el extremo bajo arriba del alto."""
        r = optimize_sku_price(_fit(), landed_cost=10.0, min_margin_pct=0.99, max_move_pct=None)
        assert r.proposed_price_low <= r.proposed_price_high


class TestNoChange:
    def test_precio_actual_dentro_de_la_banda_es_no_change(self) -> None:
        # Banda [16.67, 30.00]; el precio actual está adentro.
        r = optimize_sku_price(_fit(), landed_cost=10.0, current_price=22.0, max_move_pct=None)
        assert r.status == STATUS_NO_CHANGE
        assert r.reason is not None and "no dominado" in r.reason

    def test_precio_actual_fuera_de_la_banda_sigue_proponiendo(self) -> None:
        r = optimize_sku_price(_fit(), landed_cost=10.0, current_price=8.0, max_move_pct=None)
        assert r.status == STATUS_OK

    def test_los_bordes_de_la_banda_cuentan_como_dentro(self) -> None:
        r = optimize_sku_price(_fit(), landed_cost=10.0, max_move_pct=None)
        borde = optimize_sku_price(
            _fit(), landed_cost=10.0, current_price=r.proposed_price_low, max_move_pct=None,
        )
        assert borde.status == STATUS_NO_CHANGE

    def test_sin_precio_actual_no_se_puede_decir_no_change(self) -> None:
        """`no_change` es una afirmación sobre el precio actual. Sin precio
        actual no hay nada que comparar, y hay que proponer."""
        r = optimize_sku_price(_fit(), landed_cost=10.0, current_price=None, max_move_pct=None)
        assert r.status == STATUS_OK

    def test_no_change_igual_trae_la_banda(self) -> None:
        r = optimize_sku_price(_fit(), landed_cost=10.0, current_price=22.0, max_move_pct=None)
        assert r.proposed_price_low is not None and r.proposed_price_high is not None


class TestSalvedad:
    def test_toda_propuesta_carga_la_salvedad_de_identificacion(self) -> None:
        for current in (None, 22.0, 8.0):
            r = optimize_sku_price(_fit(), landed_cost=10.0, current_price=current, max_move_pct=None)
            assert r.identification_caveat
            assert "endogeneidad" in r.identification_caveat.lower()

    def test_needs_data_no_trae_banda_ni_salvedad(self) -> None:
        """Sin propuesta no hay nada de qué advertir, y una salvedad colgando de
        un resultado sin precio diluye la que sí importa."""
        r = optimize_sku_price(_fit(identified=False), landed_cost=10.0)
        assert r.status == STATUS_NEEDS_DATA
        assert r.proposed_price_low is None and r.proposed_price_high is None
        assert r.identification_caveat == ""


class TestCompuertasIntactas:
    """Las cuatro negativas que ya existían siguen negando igual."""

    @pytest.mark.parametrize("kwargs", [
        {"identified": False},
        {"ci_excludes_zero": False},
        {"elasticity": -0.5},          # inelástico
        {"ci_high": -0.9},             # el IC no excluye -1
    ])
    def test_sigue_pidiendo_datos(self, kwargs: dict) -> None:
        r = optimize_sku_price(_fit(**kwargs), landed_cost=10.0)
        assert r.status == STATUS_NEEDS_DATA
        assert r.proposed_price is None
