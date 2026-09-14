"""El gate de la *pricing pair* sobre `_position_index`.

`_position_index` promediaba TODOS los precios de competidores observados, sin
mirar si el competidor tenía stock, si la lectura seguía vigente, ni con cuánta
confianza el match la ató a nuestro SKU.

El sesgo que eso produce tiene signo, y es el que estos tests fijan: un listing
agotado conserva su último precio, que suele ser viejo y bajo, así que el
promedio baja y el índice sube. El sistema le dice al cliente **que está caro
cuando no lo está** — y `jobs.price_priority` convierte eso en `igualar_precio`,
o sea en una recomendación de bajar el precio contra un competidor que no está
vendiendo nada.

La definición operativa —la *pricing pair*— es que un competidor solo restringe
nuestro precio si ambos lados tienen precio **y** stock. Un competidor agotado no
es una restricción competitiva.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from jobs.price_intelligence import (
    DEFAULT_MIN_MATCH_CONFIDENCE,
    _position_index,
    eligible_competitor_prices,
)
from src.pricing_intel.models import CompetitorOffer

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def _offer(
    *,
    price: str,
    availability: str = "InStock",
    hours_ago: float = 1.0,
    match_confidence: float = 0.95,
    ref: str = "https://example-retailer.test/p/x",
) -> CompetitorOffer:
    return CompetitorOffer(
        observed_at=NOW - timedelta(hours=hours_ago),
        site="example-retailer.test",
        competitor_sku_ref=ref,
        matched_product_id="SKU-100",
        match_confidence=match_confidence,
        price=Decimal(price),
        currency="USD",
        price_normalized=Decimal(price),
        shipping=None,
        availability=availability,
        promo_flag=False,
        list_price=None,
        acquisition_tier="L1",
        extractor="jsonld",
        extractor_version="1",
        extraction_confidence=0.98,
    )


class TestEligibility:
    def test_todo_en_stock_fresco_y_confiable_pasa_entero(self) -> None:
        offers = [_offer(price="100", ref="a"), _offer(price="110", ref="b")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == (Decimal("100"), Decimal("110"))
        assert pair.n_eligible == 2
        assert pair.excluded == {}

    def test_agotado_queda_afuera(self) -> None:
        offers = [_offer(price="100", ref="a"), _offer(price="60", availability="OutOfStock", ref="b")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == (Decimal("100"),)
        assert pair.excluded == {"not_in_stock": 1}

    def test_preorder_queda_afuera(self) -> None:
        """`Preorder` no es stock. No se puede comprar hoy, así que no restringe
        nuestro precio hoy. Se excluye por la misma razón que `OutOfStock` y se
        cuenta bajo el mismo motivo — el motivo nombra la condición, no el valor
        del campo."""
        offers = [_offer(price="100", ref="a"), _offer(price="60", availability="Preorder", ref="b")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == (Decimal("100"),)
        assert pair.excluded == {"not_in_stock": 1}

    def test_lectura_vencida_queda_afuera(self) -> None:
        offers = [_offer(price="100", ref="a"), _offer(price="60", hours_ago=72.0, ref="b")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == (Decimal("100"),)
        assert pair.excluded == {"stale": 1}

    def test_el_borde_del_sla_es_inclusivo(self) -> None:
        """Exactamente en el SLA todavía cuenta. El SLA es el plazo dentro del
        cual la lectura sirve, no el instante en que deja de servir."""
        offers = [_offer(price="100", hours_ago=48.0, ref="a")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.n_eligible == 1

    def test_match_dudoso_queda_afuera(self) -> None:
        offers = [
            _offer(price="100", ref="a"),
            _offer(price="60", match_confidence=DEFAULT_MIN_MATCH_CONFIDENCE - 0.01, ref="b"),
        ]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == (Decimal("100"),)
        assert pair.excluded == {"low_match_confidence": 1}

    def test_una_oferta_excluida_por_dos_razones_se_cuenta_una_vez(self) -> None:
        """La atribución es determinista y el total cierra: la suma de
        `excluded` más `n_eligible` es siempre la cantidad de ofertas de
        entrada. Sin eso, el reporte de exclusiones no se puede leer."""
        offers = [_offer(price="60", availability="OutOfStock", hours_ago=99.0, match_confidence=0.1)]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.n_eligible == 0
        assert sum(pair.excluded.values()) == 1
        assert pair.excluded == {"not_in_stock": 1}

    def test_sin_ofertas_elegibles_no_hay_indice(self) -> None:
        offers = [_offer(price="60", availability="OutOfStock", ref="a")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert pair.prices == ()
        assert _position_index(Decimal("100"), list(pair.prices)) is None


class TestSesgo:
    """El sesgo que motiva el gate, con números."""

    def test_un_agotado_viejo_y_barato_nos_hace_parecer_caros(self) -> None:
        offers = [
            _offer(price="100", ref="a"),
            _offer(price="102", ref="b"),
            # Agotado hace tres días, con el precio de liquidación con el que se
            # quedó sin stock. No le vende a nadie, y sin embargo arrastraba el
            # promedio hacia abajo.
            _offer(price="55", availability="OutOfStock", hours_ago=72.0, ref="c"),
        ]
        our_price = Decimal("101")

        sin_gate = _position_index(our_price, [o.price_normalized for o in offers])
        con_gate = _position_index(our_price, list(eligible_competitor_prices(offers, sla_hours=48.0, now=NOW).prices))

        assert sin_gate is not None and con_gate is not None
        # Sin gate el promedio es 85,67 y el índice da 1,18: "estás 18% caro".
        assert sin_gate > Decimal("1.15")
        # Con gate el promedio es 101 y el índice da 1,00: estamos en el mercado.
        assert con_gate == Decimal(1)
        assert con_gate < sin_gate

    def test_el_indice_sigue_siendo_none_y_no_1_cuando_no_hay_señal(self) -> None:
        """El gate no puede introducir un 1,0 fabricado por la puerta de atrás:
        quedarse sin competidores elegibles tiene que ser indistinguible de no
        haber tenido ninguno."""
        offers = [_offer(price="90", availability="OutOfStock", ref="a")]
        pair = eligible_competitor_prices(offers, sla_hours=48.0, now=NOW)
        assert _position_index(Decimal("100"), list(pair.prices)) is None
