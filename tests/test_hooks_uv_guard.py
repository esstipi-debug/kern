"""El guard de uv, en la suite y no en una verificacion manual.

Con la CI roja desde el 2026-08-04, los hooks de `.claude/hooks/` son de los
pocos controles que quedan -- y hasta ahora eran los unicos sin un solo test.
Su commit dice "Verificado: 23/23 casos", a mano; una verificacion que no se
vuelve a correr no protege del siguiente cambio.

Lo que se fija aca, en orden de cuanto duele que se rompa:

1. Los falsos NEGATIVOS que se encontraron probando el guard: `./venv/bin/pip
   install`, `sudo pip install` y `poetry run pip install` pasaban enteros. No
   son evasiones: son la forma normal de escribirlos, y `./venv/bin/pip install`
   es la manera MAS probable de instalar sin pensar.
2. El falso POSITIVO, que es peor porque bloquea trabajo bueno: un `;` dentro de
   comillas inventaba un comando, asi que un mensaje de commit que mencionara
   pip se bloqueaba. Con `exit 2` el modelo reescribe el comando -- el hook
   terminaba mutilando el mensaje.
3. Que la LECTURA siga pasando. Un guard que bloquea `pip list` no protege el
   lock y entrena a ignorarlo, que es como muere un control.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RUTA = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "uv_guard.py"
_spec = importlib.util.spec_from_file_location("uv_guard", _RUTA)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def bloquea(comando: str) -> bool:
    return guard.revisar(comando) is not None


# --- 1. lo que tiene que bloquear -------------------------------------------


@pytest.mark.parametrize(
    "comando",
    [
        "pip install requests",
        "pip3 install requests",
        "pip -q install requests",              # una bandera adelante no esquiva
        "python -m pip install requests",
        "python3.11 -m pip install requests",
        "pip uninstall requests",
        "poetry add requests",
        "poetry install",
        "pipenv install",
        "ls && pip install requests",           # segundo comando de una cadena
        "cat req.txt | pip install -r -",       # al otro lado de un pipe
        "PIP_INDEX_URL=x pip install requests",  # con asignacion de entorno
        "pytest -q\npip install requests",      # segunda LINEA, no segundo segmento
    ],
)
def test_lo_que_muta_el_entorno_se_bloquea(comando):
    assert bloquea(comando), comando


@pytest.mark.parametrize(
    "comando",
    [
        "./venv/bin/pip install requests",
        "venv/Scripts/pip.exe install requests",   # la forma Windows
        "/usr/bin/pip3 install requests",
        "sudo pip install requests",
        "sudo -H pip install requests",
        "poetry run pip install requests",
        "pipenv run pip install requests",
    ],
)
def test_las_formas_que_el_ancla_por_nombre_pelado_dejaba_pasar(comando):
    """Falsos negativos medidos contra la version por regex.

    Ninguno es una evasion: son como se escribe normalmente. Un pip con ruta
    sigue siendo pip, y `poetry run` no es lectura de lo que corre adentro.
    """
    assert bloquea(comando), comando


# --- 2. lo que NO puede bloquear --------------------------------------------


@pytest.mark.parametrize(
    "comando",
    [
        "pip list",
        "pip show requests",
        "pip freeze",
        "pip download requests",
        "pip --version",
        "pip",
        "poetry show",
        "poetry run pytest -q",                  # `run` de algo que no muta
        "uv pip install requests",               # el punto entero del guard
        "uv add requests",
        "pytest -q",
        'grep -rn "pip install" README.md',
        "echo pip install",
    ],
)
def test_la_lectura_y_lo_ajeno_pasan_sin_ruido(comando):
    """Un guard que bloquea `pip list` entrena a ignorar el hook."""
    assert not bloquea(comando), comando


@pytest.mark.parametrize(
    "comando",
    [
        'git commit -m "usar uv en vez de pip; pip install queda prohibido"',
        'echo "hola; pip install x"',
        "python -c 'import x; print(1)'",
    ],
)
def test_un_punto_y_coma_entre_comillas_no_inventa_un_comando(comando):
    """EL FALSO POSITIVO, que es el que duele.

    El split por regex sobre `;` no respetaba comillas. Bloquear un `git commit`
    es peor que dejar pasar un `pip install`: el modelo reescribe el comando ante
    un exit 2, o sea que el hook terminaba mutilando el mensaje del commit.
    """
    assert not bloquea(comando), comando


# --- 3. el mensaje ----------------------------------------------------------


def test_el_bloqueo_dice_que_usar_en_vez_de_solo_prohibir():
    mensaje = guard.revisar("pip install requests")
    assert "uv pip install" in mensaje
    assert "uv.lock" in mensaje


def test_una_herramienta_sin_equivalente_igual_explica_la_regla():
    """`pip wheel` no tiene traduccion 1:1 y aun asi no puede salir un mensaje
    mudo: el hook tiene que decir por que, o se lee como un error del sistema."""
    mensaje = guard.revisar("pip wheel .")
    assert mensaje and "uv" in mensaje


def test_comillas_sin_cerrar_no_apagan_el_guard():
    """`shlex` levanta con comillas desbalanceadas. Perder el guard entero por un
    comando mal escrito es peor que un falso positivo, asi que hay respaldo."""
    assert bloquea('pip install requests "')


# --- 4. el segmento que se reporta ------------------------------------------


def test_se_reporta_el_segmento_que_ofende_y_no_la_linea_entera():
    """En `ls && pip install x` lo accionable es la mitad de la derecha."""
    mensaje = guard.revisar("ls -la && pip install requests")
    assert "pip install requests" in mensaje
    assert "ls -la" not in mensaje
