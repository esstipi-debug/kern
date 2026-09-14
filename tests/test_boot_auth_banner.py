"""Arranque sin API key: banner explicito, no silencio ni crash.

Precondicion 4 de docs/superpowers/specs/2026-08-05-self-hosted-image-packaging.md.
La spec fija tambien el contrato y el matiz que lo hace util:

    | `KERN_API_KEY` | protege `POST /api/jobs` | vacio = sin auth, y **lo dice
      fuerte al arrancar** |

    ... la diferencia entre "no configuraste" y "configuraste vacio" es
    explicita.

Lo que habia antes de esto, medido importando `webapp.app` con el entorno
limpio: `ENV='development'`, `API_KEY=''`, `RATE_LIMIT=0` y **cero lineas de log
sobre seguridad**. `production_warnings()` arranca con `if ENV != "production":
return []`, asi que el unico aviso que existia estaba apagado justo en el caso
que importa -- un cliente que corre `docker run` sin variables y pone el puerto
en su red.
"""

from __future__ import annotations

import logging

import pytest

from webapp import security

CONTRATO = "KERN_API_KEY"
LEGADO = "LINCHPIN_API_KEY"


def _limpiar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTRATO, raising=False)
    monkeypatch.delenv(LEGADO, raising=False)


# -- que valor toma la key ---------------------------------------------------


def test_la_variable_del_contrato_alcanza_sola(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un cliente self-serve solo conoce KERN_API_KEY; con eso tiene que bastar."""
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "lpk_del_cliente")
    assert security.leer_api_key() == "lpk_del_cliente"


def test_la_variable_legada_sigue_funcionando(monkeypatch: pytest.MonkeyPatch) -> None:
    """Este deploy la usa como secret de Fly; no se toca."""
    _limpiar(monkeypatch)
    monkeypatch.setenv(LEGADO, "lpk_del_operador")
    assert security.leer_api_key() == "lpk_del_operador"


def test_la_legada_gana_si_estan_las_dos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismo criterio conservador que KERN_DATA_DIR: lo que prod ya setea manda.

    Solo puede pasar en el deploy del operador, y ahi la variable vieja es la
    que esta configurada como secret.
    """
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "del_contrato")
    monkeypatch.setenv(LEGADO, "del_legado")
    assert security.leer_api_key() == "del_legado"


# -- los tres estados, que es lo que pide la spec ----------------------------


def test_sin_ninguna_variable_el_estado_es_sin_configurar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limpiar(monkeypatch)
    assert security.estado_api_key() == "sin_configurar"


def test_variable_vacia_es_apagado_a_proposito(monkeypatch: pytest.MonkeyPatch) -> None:
    """"No configuraste" y "configuraste vacio" NO son el mismo hecho.

    Es la distincion que pide la spec: quien apaga la auth a proposito en su LAN
    no tiene que recibir la misma alarma que quien se la olvido.
    """
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "")
    assert security.estado_api_key() == "apagada_a_proposito"


def test_con_valor_el_estado_es_configurada(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "lpk_x")
    assert security.estado_api_key() == "configurada"


def test_solo_espacios_cuenta_como_apagado_a_proposito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una variable en blanco es una decision escrita, no una key de un espacio."""
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "   ")
    assert security.estado_api_key() == "apagada_a_proposito"
    assert security.leer_api_key() == ""


# -- el banner ---------------------------------------------------------------


def test_el_banner_grita_en_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """El aviso viejo se apagaba justo donde hace falta.

    `production_warnings()` devuelve [] salvo en LINCHPIN_ENV=production, y un
    cliente que corre `docker run` no setea LINCHPIN_ENV. El banner nuevo no
    depende del entorno declarado.
    """
    _limpiar(monkeypatch)
    monkeypatch.setattr(security, "ENV", "development")
    lineas = security.banner_de_arranque()
    assert lineas, "development sin key no puede salir en silencio"
    texto = "\n".join(lineas)
    assert CONTRATO in texto, "el banner tiene que nombrar la variable del contrato"
    assert "/api/jobs" in texto, "tiene que decir QUE queda expuesto"


def test_el_banner_dice_como_arreglarlo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un aviso que no dice el siguiente paso se ignora."""
    _limpiar(monkeypatch)
    texto = "\n".join(security.banner_de_arranque())
    assert "docker run" in texto or "-e KERN_API_KEY" in texto


def test_el_banner_distingue_apagado_a_proposito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sigue avisando que esta abierto, sin tratarlo como un olvido."""
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "")
    lineas = security.banner_de_arranque()
    assert lineas, "aun apagada a proposito, el hecho se dice"
    texto = "\n".join(lineas).lower()
    assert "sin autenticacion" in texto or "unauthenticated" in texto
    olvido = "\n".join(
        (lambda: (_limpiar(monkeypatch), security.banner_de_arranque())[1])()
    )
    assert texto != olvido.lower(), "los dos estados no pueden leerse igual"


def test_sin_banner_cuando_la_key_esta_puesta(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv(CONTRATO, "lpk_x")
    assert security.banner_de_arranque() == []


# -- el contrato con el arranque --------------------------------------------


def test_arrancar_sin_key_no_revienta(monkeypatch: pytest.MonkeyPatch) -> None:
    """La spec dice "no silencio NI crash". Esta mitad tambien se fija."""
    _limpiar(monkeypatch)
    security.banner_de_arranque()  # no levanta


def test_el_banner_llega_al_log_al_importar_la_app(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Que la funcion exista no sirve si nadie la llama en el arranque.

    Se prueba el emisor, no el import de `webapp.app` -- importarlo dos veces
    en la misma sesion de pytest levanta el grafo de conocimiento otra vez y no
    re-ejecuta el modulo, asi que el caplog llegaria vacio por construccion.
    """
    _limpiar(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="linchpin.security"):
        security.emitir_banner_de_arranque()
    assert caplog.records, "el banner no se emitio"
    assert any(CONTRATO in r.getMessage() for r in caplog.records)


def test_production_warnings_sigue_intacto(monkeypatch: pytest.MonkeyPatch) -> None:
    """El banner es un canal nuevo, no un reemplazo.

    `LINCHPIN_REQUIRE_SECURE` se niega a bootear si `production_warnings()` trae
    algo. Hacer que esa lista dejara de estar vacia en development habria roto el
    arranque de cualquier dev que la tuviera puesta.
    """
    _limpiar(monkeypatch)
    monkeypatch.setattr(security, "ENV", "development")
    assert security.production_warnings() == []
