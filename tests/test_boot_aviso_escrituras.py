"""El arranque dice que pasa con las escrituras que necesitan aprobacion.

Desde que ``nosilo_core.writeback.apply()`` falla cerrado, un deploy sin
``LINCHPIN_APPROVAL_SECRET`` lee y calcula, pero rechaza toda escritura que
necesita aprobacion (Odoo, Shopify, Excel, promociones de autonomia). Este aviso
lo dice al arrancar, por el mismo logger que el banner de la API key y sin
tocarlo: la precondicion 4 de la spec de empaquetado sigue siendo sobre la key
(``tests/test_boot_auth_banner.py``).

Se prueba el emisor y no el import de ``webapp.app``, por la misma razon que el
banner: importarlo dos veces en una sesion de pytest no re-ejecuta el modulo.
"""

from __future__ import annotations

import logging

import pytest

from webapp import security

SECRETO = "LINCHPIN_APPROVAL_SECRET"
SIN_FIRMA = "LINCHPIN_ALLOW_UNSIGNED_APPROVALS"


def _estado(monkeypatch: pytest.MonkeyPatch, *, secreto: str, sin_firma: bool) -> None:
    monkeypatch.setattr(security, "APPROVAL_SECRET", secreto)
    monkeypatch.setattr(security, "ALLOW_UNSIGNED_APPROVALS", sin_firma)


def _registros(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "linchpin.security"]


@pytest.mark.parametrize("sin_firma", [False, True])
def test_con_secreto_no_hay_nada_que_avisar(monkeypatch: pytest.MonkeyPatch, sin_firma: bool) -> None:
    """Con secreto, apply() verifica la firma y el interruptor de desarrollo es inerte."""
    _estado(monkeypatch, secreto="un-secreto", sin_firma=sin_firma)
    assert security.aviso_de_escrituras() == []


def test_sin_secreto_dice_que_las_escrituras_se_rechazan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un aviso que no nombra la variable no dice como salir."""
    _estado(monkeypatch, secreto="", sin_firma=False)
    texto = "\n".join(security.aviso_de_escrituras())
    assert SECRETO in texto
    assert "rechaza" in texto.lower()


def test_con_el_opt_in_avisa_que_se_pueden_fabricar(monkeypatch: pytest.MonkeyPatch) -> None:
    """El estado riesgoso no puede leerse igual que el seguro."""
    _estado(monkeypatch, secreto="", sin_firma=True)
    riesgoso = "\n".join(security.aviso_de_escrituras())
    assert SIN_FIRMA in riesgoso
    assert "fabricar" in riesgoso.lower()

    _estado(monkeypatch, secreto="", sin_firma=False)
    assert riesgoso != "\n".join(security.aviso_de_escrituras())


@pytest.mark.parametrize("sin_firma", [False, True])
def test_el_aviso_sale_en_warning_porque_es_lo_unico_que_se_ve(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sin_firma: bool
) -> None:
    """WARNING en los dos estados, tambien en el seguro. Medido arrancando la app con
    LINCHPIN_LOG_LEVEL=INFO: una linea INFO de `linchpin.security` no sale, porque
    `observability.configure_logging()` solo configura `linchpin.access` y este
    logger cae en el handler de ultimo recurso de Python, que imprime de WARNING
    para arriba. Y por el criterio del banner: un secreto que falta es un olvido
    posible, no una decision."""
    _estado(monkeypatch, secreto="", sin_firma=sin_firma)
    with caplog.at_level(logging.INFO, logger="linchpin.security"):
        security.emitir_aviso_de_escrituras()
    assert _registros(caplog) and {r.levelno for r in _registros(caplog)} == {logging.WARNING}


def test_el_banner_de_la_api_key_no_cambia(monkeypatch: pytest.MonkeyPatch) -> None:
    """La precondicion 4 es sobre la key: con la key puesta su banner sigue vacio,
    falte o no el secreto de aprobacion. El aviso es un canal aparte."""
    monkeypatch.setenv("KERN_API_KEY", "lpk_x")
    monkeypatch.delenv("LINCHPIN_API_KEY", raising=False)
    _estado(monkeypatch, secreto="", sin_firma=False)
    assert security.banner_de_arranque() == []
