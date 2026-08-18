"""Los hooks de `.claude/` no pueden callarse cuando deberian actuar.

Esa es la propiedad que defienden estos tests, y no es teorica: durante el
desarrollo, `ruff-on-write.sh` devolvia 0 sin haber mirado el archivo porque no
reconocia las rutas estilo MSYS (`/c/Users/...`) que produce Git Bash. Un hook
que falla en silencio es peor que no tenerlo — se parece a "todo limpio".

Se los ejercita como los ejercita Claude Code: JSON por stdin, veredicto por
codigo de salida. Nada de importar funciones internas; lo que se prueba es el
contrato observable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
HOOKS = RAIZ / ".claude" / "hooks"
GUARD = HOOKS / "uv-guard.sh"
RUFF_HOOK = HOOKS / "ruff-on-write.sh"

def _resolver_bash() -> str | None:
    """El bash que realmente corre estos hooks.

    En Windows no alcanza con pasar `"bash"` a subprocess: `CreateProcess`
    encuentra primero `System32\\bash.exe`, que es el **relay de WSL**, y falla
    con `execvpe(/bin/bash): No such file or directory`. `shutil.which` puede
    devolver Git Bash y aun asi subprocess arranca el relay, porque no comparten
    orden de busqueda. Por eso se resuelve a ruta absoluta y se descarta
    System32 explicitamente.
    """
    candidatos = [shutil.which("bash")]
    candidatos += [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ]
    for candidato in candidatos:
        if not candidato:
            continue
        ruta = Path(candidato)
        if not ruta.is_file():
            continue
        if ruta.parent.name.lower() == "system32":
            continue
        return str(ruta)
    return None


BASH = _resolver_bash()

pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="los hooks son bash; sin un bash real (no el relay de WSL) no hay nada que probar",
)


def _correr(hook: Path, payload) -> subprocess.CompletedProcess:
    """Invoca el hook igual que Claude Code: JSON por stdin, veredicto por rc."""
    entorno = dict(os.environ, CLAUDE_PROJECT_DIR=str(RAIZ))
    return subprocess.run(
        [BASH, str(hook)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(RAIZ),
        env=entorno,
        timeout=180,
    )


# --------------------------------------------------------------------------
# Contrato con settings.json: lo declarado tiene que existir
# --------------------------------------------------------------------------


def test_los_hooks_declarados_en_settings_existen():
    """Un hook declarado que no existe es un gate que nadie nota que falta.

    Misma clase de bug que tenia el loop nosilo/kern: apuntar a un archivo
    inexistente y leer el silencio como exito.
    """
    settings = json.loads((RAIZ / ".claude" / "settings.json").read_text(encoding="utf-8"))
    declarados = [
        entrada["command"]
        for eventos in settings["hooks"].values()
        for grupo in eventos
        for entrada in grupo["hooks"]
    ]
    assert declarados, "settings.json no declara ningun hook"
    for comando in declarados:
        ruta = RAIZ / comando.replace("$CLAUDE_PROJECT_DIR/", "")
        assert ruta.is_file(), f"hook declarado que no existe: {comando}"


def test_los_dos_hooks_nuevos_estan_conectados_y_el_viejo_sigue():
    settings = json.loads((RAIZ / ".claude" / "settings.json").read_text(encoding="utf-8"))
    texto = json.dumps(settings)
    assert "uv-guard.sh" in texto
    assert "ruff-on-write.sh" in texto
    assert "session-start.sh" in texto, "agregar hooks no puede perder el que ya existia"


# --------------------------------------------------------------------------
# uv-guard: bloquea lo que muta el entorno, deja pasar la lectura
# --------------------------------------------------------------------------

BLOQUEAR = [
    "pip install requests",
    "pip3 install -r requirements.txt",
    "python -m pip install numpy",
    "pip -q install numpy",
    "pip uninstall kern",
    "poetry add pandas",
    "poetry install",
    "poetry remove pandas",
    "poetry update",
    "pipenv install",
    "cd /tmp && pip install foo",
    "pytest -q ; pip install foo",
    "PIP_INDEX_URL=http://x pip install foo",
]

PASAR = [
    "uv pip install requests",
    "uv add pandas",
    "uv sync",
    "pip download foo",
    "pip list",
    "pip show numpy",
    "pip --version",
    # Lectura con bandera ADELANTE del subcomando. Estos son los que pinan la
    # logica de `subcomando()`: si dejara de saltear banderas, leeria `-q` como
    # subcomando, no lo encontraria en la lista de lectura, y bloquearia un
    # `pip list` legitimo. Los casos de bloqueo no detectan esa regresion —
    # `pip -q install` sigue bloqueando igual, solo que por el motivo
    # equivocado. (Descubierto probando la suite por mutacion.)
    "pip -q list",
    "pip --disable-pip-version-check show numpy",
    "poetry -n show",
    "poetry show",
    "pytest tests/ -q",
    "python -m pytest tests/test_claude_hooks.py",
    "grep -r 'pip install' docs/",
    "git commit -m 'no usar pip install'",
    "bash scripts/setup-test-env.sh",
]


@pytest.mark.parametrize("comando", BLOQUEAR)
def test_el_guard_bloquea_lo_que_muta_el_entorno(comando):
    resultado = _correr(GUARD, {"tool_name": "Bash", "tool_input": {"command": comando}})
    assert resultado.returncode == 2, f"deberia bloquear: {comando}"
    assert "uv.lock" in resultado.stderr


@pytest.mark.parametrize("comando", PASAR)
def test_el_guard_deja_pasar_lo_que_no_toca_el_lock(comando):
    """Prohibir la lectura no protege el lock y entrena a ignorar el hook.

    `pip -q install` y el encadenado estan del otro lado a proposito: una
    bandera antes del subcomando esquivaba el guard entero hasta que se
    arreglo la extraccion del subcomando.
    """
    resultado = _correr(GUARD, {"tool_name": "Bash", "tool_input": {"command": comando}})
    assert resultado.returncode == 0, f"no deberia bloquear: {comando}\n{resultado.stderr}"


def test_el_guard_sugiere_el_equivalente_en_uv():
    """Bloquear sin decir por donde seguir solo produce reintentos."""
    resultado = _correr(GUARD, {"tool_name": "Bash", "tool_input": {"command": "poetry add x"}})
    assert "uv add" in resultado.stderr


@pytest.mark.parametrize("payload", ["no soy json", "", "{}", '{"tool_input": {}}'])
def test_el_guard_no_rompe_con_entrada_degenerada(payload):
    """Un hook que crashea con basura en stdin bloquea trabajo legitimo."""
    assert _correr(GUARD, payload).returncode == 0


# --------------------------------------------------------------------------
# ruff-on-write
# --------------------------------------------------------------------------


@pytest.fixture
def dir_temporal():
    """Un directorio dentro del repo: el hook ignora a proposito lo de afuera.

    No puede empezar con punto — ruff excluye ocultos por defecto, y entonces el
    hook pareceria funcionar cuando en realidad no se corrio nada. Ese error ya
    se cometio una vez armando estos mismos casos a mano.
    """
    ruta = Path(tempfile.mkdtemp(dir=str(RAIZ), prefix="tmp_hooktest_"))
    try:
        yield ruta
    finally:
        shutil.rmtree(ruta, ignore_errors=True)


def _editar(archivo: Path, ruta_declarada: str | None = None) -> subprocess.CompletedProcess:
    return _correr(
        RUFF_HOOK,
        {"tool_name": "Edit", "tool_input": {"file_path": ruta_declarada or str(archivo)}},
    )


def test_las_violaciones_arreglables_se_arreglan_solas(dir_temporal):
    archivo = dir_temporal / "sucio.py"
    archivo.write_text("import os\nimport json\n\n\ndef f(x):\n    return x\n", encoding="utf-8")
    resultado = _editar(archivo)
    assert resultado.returncode == 0, resultado.stderr
    assert "import os" not in archivo.read_text(encoding="utf-8")


def test_lo_que_ruff_no_arregla_vuelve_como_feedback(dir_temporal):
    """F821 no tiene autofix: tiene que volver al agente, no perderse."""
    archivo = dir_temporal / "roto.py"
    archivo.write_text("def g():\n    return no_existe_en_ningun_lado\n", encoding="utf-8")
    resultado = _editar(archivo)
    assert resultado.returncode == 2
    assert "F821" in resultado.stderr


def test_un_archivo_ya_limpio_no_se_toca(dir_temporal):
    archivo = dir_temporal / "limpio.py"
    original = "def f(x):\n    return x\n"
    archivo.write_text(original, encoding="utf-8")
    assert _editar(archivo).returncode == 0
    assert archivo.read_text(encoding="utf-8") == original


def test_acepta_la_forma_msys_de_la_ruta(dir_temporal):
    """Git Bash produce `/c/Users/...`; Claude Code entrega la forma Windows.

    Sin normalizar las dos, el hook devolvia 0 sin mirar el archivo. Es el bug
    que motivo la mitad de este archivo.
    """
    archivo = dir_temporal / "msys.py"
    archivo.write_text("def g():\n    return no_definido_aca\n", encoding="utf-8")
    partes = archivo.resolve().as_posix().split(":", 1)
    if len(partes) != 2:
        pytest.skip("ruta no-Windows: la forma MSYS no aplica")
    msys = "/{}{}".format(partes[0].lower(), partes[1])
    resultado = _editar(archivo, ruta_declarada=msys)
    assert resultado.returncode == 2, f"la forma MSYS quedo sin mirar: {msys}"
    assert "F821" in resultado.stderr


def test_un_archivo_que_no_es_python_se_ignora(dir_temporal):
    archivo = dir_temporal / "notas.md"
    original = "x  =   1\n"
    archivo.write_text(original, encoding="utf-8")
    assert _editar(archivo).returncode == 0
    assert archivo.read_text(encoding="utf-8") == original


def test_un_python_fuera_del_repo_queda_intacto():
    """Se puede trabajar en otro codebase desde aca; no es asunto de este hook."""
    with tempfile.TemporaryDirectory() as afuera:
        archivo = Path(afuera) / "ajeno.py"
        original = "import os\n"
        archivo.write_text(original, encoding="utf-8")
        assert _editar(archivo).returncode == 0
        assert archivo.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("payload", ["no soy json", "", "{}", '{"tool_input": {}}'])
def test_el_hook_de_ruff_no_rompe_con_entrada_degenerada(payload):
    assert _correr(RUFF_HOOK, payload).returncode == 0


def test_un_archivo_inexistente_no_rompe_el_hook():
    assert _editar(RAIZ / "no_existe_en_ningun_lado.py").returncode == 0


def test_el_hook_nunca_reformatea(dir_temporal):
    """`ruff format` reformatearia 589 de 769 archivos del repo.

    Que el hook no lo corra es una decision, no un olvido. Si algun dia alguien
    lo agrega, este test tiene que ser lo que lo frene y obligue a discutirlo
    como cambio de repo y no como efecto secundario de tocar un archivo.
    """
    archivo = dir_temporal / "feo.py"
    feo = "def f(x):\n    y  =  x\n    return y\n"
    archivo.write_text(feo, encoding="utf-8")
    _editar(archivo)
    assert archivo.read_text(encoding="utf-8") == feo

    # Se miran solo las lineas ejecutables: la cabecera del hook menciona
    # `ruff format` justamente para explicar por que NO lo corre, y buscar la
    # cadena en el archivo entero convierte esa explicacion en un falso
    # positivo. (Paso: fue el primer intento de este test.)
    codigo = "\n".join(
        linea for linea in RUFF_HOOK.read_text(encoding="utf-8").splitlines()
        if not linea.strip().startswith("#")
    ).replace("--output-format", "")
    assert "format" not in codigo
