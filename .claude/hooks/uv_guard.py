"""Decide si un comando Bash mutaria el entorno sin tocar ``uv.lock``.

Vive en un .py y no dentro del heredoc de ``uv-guard.sh`` por una sola razon: el
guard es, con la CI roja, uno de los pocos controles que quedan, y un control que
no se puede testear no es un control -- los 23 casos que verificaron su primera
version se corrieron a mano y no quedaron en ninguna suite. Aca son importables,
y ``tests/test_hooks_uv_guard.py`` los fija.

El contrato es el mismo de antes: bloquea SOLO lo que muta el entorno instalado.
La lectura (``pip list``, ``pip show``, ``poetry show``) pasa sin ruido, porque
prohibirla no protege el lock y solo entrena a ignorar el hook.

Tres cosas que la version por regex no acertaba, y que son formas normales de
escribir un comando, no evasiones:

* ``./venv/bin/pip install`` y ``/usr/bin/pip3 install`` -- el ancla ``^pip``
  solo veia el nombre pelado. Un pip con ruta sigue siendo pip, y es la forma
  MAS probable de instalar sin pensar, no la menos.
* ``sudo pip install`` -- misma historia.
* ``poetry run pip install`` -- ``run`` esta en la lista de lectura (correcto:
  ``poetry run pytest`` no muta nada), asi que el guard se quedaba en el ``run``
  y no miraba lo que se corria adentro.

Y un falso POSITIVO, que es el que mas duele porque bloquea trabajo bueno: el
split por regex sobre ``;`` no respetaba comillas, asi que
``git commit -m "usar uv en vez de pip; pip install queda prohibido"`` se leia
como dos comandos y el segundo se bloqueaba. Con ``exit 2`` el modelo reescribe
el comando, o sea que el hook terminaba mutilando un mensaje de commit. Ahora la
tokenizacion es ``shlex``, que trata el string entrecomillado como UN token.
"""

from __future__ import annotations

import re
import shlex

#: Subcomandos que no tocan el entorno instalado.
LECTURA = {
    "pip": {"download", "list", "show", "freeze", "check", "config", "cache", "index"},
    "poetry": {"show", "check", "search", "about", "env", "version"},
    "pipenv": {"graph", "check"},
}

EQUIVALENTE = {
    ("pip", "install"): "uv pip install  (o `uv add` si va a pyproject.toml)",
    ("pip", "uninstall"): "uv pip uninstall",
    ("pip", "sync"): "uv pip sync",
    ("poetry", "add"): "uv add",
    ("poetry", "remove"): "uv remove",
    ("poetry", "install"): "uv sync",
    ("poetry", "update"): "uv lock --upgrade",
    ("poetry", "lock"): "uv lock",
    ("pipenv", "install"): "uv sync",
    ("pipenv", "uninstall"): "uv remove",
}

#: `run` NO va en LECTURA: se trata aparte, recursando en lo que corre adentro.
#: Dejarlo ahi hacia que `poetry run pip install` pasara entero.
_RUN = {"poetry", "pipenv"}

_HERRAMIENTA = re.compile(r"^(pip3?|poetry|pipenv)$")
_PYTHON = re.compile(r"^python(\d(\.\d+)?)?$")
_ENV_ASSIGN = re.compile(r"^\w+=")
#: Un token hecho solo de estos caracteres es un operador del shell, no un
#: comando. `shlex` con `punctuation_chars` los agrupa (`&&` sale entero).
_OPERADORES = set(";&|()<>")


def _base(token: str) -> str:
    """``./venv/bin/pip.exe`` -> ``pip``. Un pip con ruta sigue siendo pip."""
    nombre = re.split(r"[\\/]", token)[-1]
    return nombre[:-4] if nombre.lower().endswith(".exe") else nombre


def _es_operador(token: str) -> bool:
    return bool(token) and set(token) <= _OPERADORES


def segmentar(linea: str) -> list[list[str]]:
    """Una linea -> los comandos que contiene, cada uno como lista de tokens.

    ``shlex`` en modo POSIX colapsa un string entrecomillado a UN token, que es
    lo que impide que un ``;`` dentro de comillas invente un comando. Si las
    comillas estan desbalanceadas levanta: ahi se cae al split ingenuo, porque
    perder el guard entero por un comando mal escrito es peor que un falso
    positivo.
    """
    try:
        lex = shlex.shlex(linea, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return [trozo.split() for trozo in re.split(r"&&|\|\||[;|]", linea)]

    salida: list[list[str]] = []
    actual: list[str] = []
    for token in tokens:
        if _es_operador(token):
            salida.append(actual)
            actual = []
        else:
            actual.append(token)
    salida.append(actual)
    return salida


def analizar(tokens: list[str]) -> tuple[str, str, str] | None:
    """``(herramienta, canonica, subcomando)`` si el segmento muta; None si no."""
    i = 0
    while i < len(tokens) and _ENV_ASSIGN.match(tokens[i]):
        i += 1
    if i < len(tokens) and _base(tokens[i]) == "sudo":
        i += 1
        while i < len(tokens) and tokens[i].startswith("-"):
            i += 1
    if i + 1 < len(tokens) and _PYTHON.match(_base(tokens[i])) and tokens[i + 1] == "-m":
        i += 2
    if i >= len(tokens):
        return None

    coincidencia = _HERRAMIENTA.match(_base(tokens[i]))
    if not coincidencia:
        return None
    herramienta = coincidencia.group(1)
    canonica = "pip" if herramienta == "pip3" else herramienta

    resto = tokens[i + 1:]
    # El primer token que no sea bandera: `pip -q install x` tiene que leerse
    # como `install`, o cualquier bandera adelante esquiva el guard entero.
    sub = next((t for t in resto if not t.startswith("-")), "")

    if canonica in _RUN and sub == "run":
        return analizar(resto[resto.index(sub) + 1:])
    if not sub or sub in LECTURA.get(canonica, set()):
        return None
    return herramienta, canonica, sub


def revisar(comando: str) -> str | None:
    """El mensaje de bloqueo, o None si el comando puede correr.

    Las lineas se parten antes de tokenizar: en POSIX el salto de linea es
    espacio en blanco, asi que sin esto dos comandos en lineas distintas se
    leerian como uno y el segundo no se miraria.
    """
    for linea in comando.splitlines():
        for tokens in segmentar(linea):
            hallazgo = analizar(tokens)
            if hallazgo is None:
                continue
            herramienta, canonica, sub = hallazgo
            sugerencia = EQUIVALENTE.get((canonica, sub))
            return "Bloqueado: `%s %s` mutaria el entorno sin tocar uv.lock.\n%s\nComando: %s" % (
                herramienta,
                sub,
                "Usa en su lugar: %s" % sugerencia if sugerencia else "Este repo maneja dependencias con uv.",
                " ".join(tokens),
            )
    return None
