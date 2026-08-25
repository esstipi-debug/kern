"""La CI no puede quedarse sin gate en silencio otra vez.

Contexto medido, no hipotetico: `NOSILO_CORE_TOKEN` nunca se configuro, los dos
jobs abortaron en el paso de autenticacion durante dieciseis dias, y en ese lapso
todo lo mergeado entro sin que un solo test corriera del lado de GitHub.

Lo que se fija aca:

1. Los dos jobs usan la MISMA action. El paso estaba duplicado inline en los dos,
   y una copia que se queda atras falla en el job que nadie mira.
2. Las tres ramas de la action son exhaustivas y mutuamente excluyentes sobre las
   cuatro combinaciones de (hay ssh-key?, hay token?). Un `if` mal editado deja
   una combinacion sin ninguna rama -- y ahi no se configura credencial, el paso
   pasa en verde, y el fallo aparece despues en `pip install` con un mensaje que
   no dice que falto.
3. Sin credencial se sale distinto de cero NOMBRANDO las dos opciones. Un paso
   que sigue sin autenticar convierte "no hay gate" en "no se nota".
4. El host key va pineado. Un `ssh-keyscan` confia en lo que le conteste, y esta
   conexion lleva una credencial.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOW = RAIZ / ".github" / "workflows" / "tests.yml"
ACTION = RAIZ / ".github" / "actions" / "authenticate-core" / "action.yml"
RUTA_ACTION = "./.github/actions/authenticate-core"


def _cargar(ruta: Path) -> dict:
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


def _pasos_de_auth(job: dict) -> list[dict]:
    return [
        p
        for p in job.get("steps", [])
        if "Authenticate to the private core" in str(p.get("name", ""))
    ]


# --- 1. una sola fuente ------------------------------------------------------


def test_los_dos_jobs_usan_la_action_y_no_una_copia_inline():
    wf = _cargar(WORKFLOW)
    jobs_con_auth = {
        nombre: job for nombre, job in wf["jobs"].items() if _pasos_de_auth(job)
    }
    assert len(jobs_con_auth) >= 2, "se esperaban al menos dos jobs autenticando"

    for nombre, job in jobs_con_auth.items():
        for paso in _pasos_de_auth(job):
            assert paso.get("uses") == RUTA_ACTION, (
                f"el job {nombre!r} no usa la action compartida: {paso}"
            )
            assert "run" not in paso, f"el job {nombre!r} volvio a tener el paso inline"


def test_ningun_job_reescribe_la_url_por_su_cuenta():
    """Si alguien vuelve a poner un `insteadOf` suelto, la action deja de ser la
    unica fuente y la segunda copia es la que se queda vieja."""
    texto = WORKFLOW.read_text(encoding="utf-8")
    assert "insteadOf" not in texto, "hay una reescritura de URL fuera de la action"


def test_los_dos_jobs_le_pasan_las_dos_credenciales():
    wf = _cargar(WORKFLOW)
    for nombre, job in wf["jobs"].items():
        for paso in _pasos_de_auth(job):
            con = paso.get("with") or {}
            assert "NOSILO_CORE_SSH_KEY" in str(con.get("ssh-key", "")), nombre
            assert "NOSILO_CORE_TOKEN" in str(con.get("token", "")), nombre


def test_la_action_no_se_invoca_cuando_los_secrets_estan_vacios():
    """Invocar la action con inputs vacios es un exit 1 a proposito.

    GitHub rechaza `if: ${{ secrets.* }}` al parsear el workflow (run de 0s,
    sin jobs). Los secrets se leen como env en un paso `id: core` y la action
    se salta con `steps.core.outputs.present`.
    """
    texto = _lineas_ejecutables(WORKFLOW)
    assert "if: ${{ secrets." not in texto
    wf = _cargar(WORKFLOW)
    for nombre, job in wf["jobs"].items():
        detect = [p for p in job.get("steps", []) if p.get("id") == "core"]
        assert len(detect) == 1, nombre
        env = detect[0].get("env") or {}
        assert "NOSILO_CORE_SSH_KEY" in str(env.get("SSH_KEY", "")), nombre
        assert "NOSILO_CORE_TOKEN" in str(env.get("TOKEN", "")), nombre
        for paso in _pasos_de_auth(job):
            cond = str(paso.get("if") or "")
            assert "steps.core.outputs.present" in cond, nombre
            assert "secrets." not in cond, nombre


# --- 2. las ramas cubren los cuatro casos, sin solaparse ---------------------


def _evaluar(condicion: str, *, hay_ssh: bool, hay_token: bool) -> bool:
    """Evalua las expresiones `inputs.x != ''` / `== ''` que usa la action.

    Deliberadamente limitado: si alguien escribe una condicion mas rica que
    esto, el test revienta en vez de aprobarla sin entenderla.
    """
    valores = {"inputs.ssh-key": "k" if hay_ssh else "", "inputs.token": "t" if hay_token else ""}
    resultado = None
    for trozo in condicion.split("&&"):
        trozo = trozo.strip()
        for clave, valor in valores.items():
            if trozo == f"{clave} != ''":
                parcial = valor != ""
                break
            if trozo == f"{clave} == ''":
                parcial = valor == ""
                break
        else:
            raise AssertionError(f"condicion no reconocida por el test: {trozo!r}")
        resultado = parcial if resultado is None else (resultado and parcial)
    return bool(resultado)


@pytest.mark.parametrize("hay_ssh,hay_token", list(itertools.product([True, False], repeat=2)))
def test_cada_combinacion_dispara_exactamente_una_rama(hay_ssh, hay_token):
    """EL TEST QUE IMPORTA.

    Cero ramas = el paso pasa sin configurar credencial y el fallo aparece
    despues, en `pip install`, sin decir que falto. Dos ramas = la segunda pisa
    la reescritura de la primera. Las dos formas son silenciosas.
    """
    pasos = _cargar(ACTION)["runs"]["steps"]
    disparan = [
        p["name"] for p in pasos if _evaluar(p["if"], hay_ssh=hay_ssh, hay_token=hay_token)
    ]
    assert len(disparan) == 1, (
        f"con ssh={hay_ssh} token={hay_token} dispararon {len(disparan)}: {disparan}"
    )


def test_el_deploy_key_le_gana_al_pat_cuando_estan_los_dos():
    """El PAT vence y el deploy key no. Con los dos puestos, la CI tiene que
    quedarse con el que no se cae solo dentro de un ano."""
    pasos = _cargar(ACTION)["runs"]["steps"]
    elegido = [p["name"] for p in pasos if _evaluar(p["if"], hay_ssh=True, hay_token=True)]
    assert elegido == ["Con deploy key (no vence)"]


# --- 3. sin credencial se corta, y se dice como arreglarlo -------------------


def test_sin_credencial_sale_distinto_de_cero_y_nombra_LAS_DOS_opciones():
    pasos = _cargar(ACTION)["runs"]["steps"]
    sin = next(p for p in pasos if _evaluar(p["if"], hay_ssh=False, hay_token=False))
    guion = sin["run"]

    assert "exit 1" in guion, "un paso que no corta deja correr el resto sin credencial"
    assert "NOSILO_CORE_SSH_KEY" in guion and "NOSILO_CORE_TOKEN" in guion
    assert "DEPLOYMENT.md" in guion, "sin donde leerlo, el error obliga a adivinar"


def test_el_pat_avisa_que_vence_en_vez_de_pasar_callado():
    """El modo de falla que ya costo dieciseis dias no se anuncia solo. Si la CI
    corre con PAT, cada corrida deja dicho que ese gate tiene fecha."""
    pasos = _cargar(ACTION)["runs"]["steps"]
    con_pat = next(p for p in pasos if _evaluar(p["if"], hay_ssh=False, hay_token=True))
    assert "::warning::" in con_pat["run"]
    assert "VENCE" in con_pat["run"]


# --- 4. la conexion que lleva la credencial ---------------------------------


def _lineas_ejecutables(ruta: Path) -> str:
    """El archivo sin sus comentarios.

    Mismo recurso que `test_claude_hooks.test_el_hook_nunca_reformatea`, y por la
    misma razon: la action MENCIONA `ssh-keyscan` justamente para explicar por
    que NO lo usa, y buscar la cadena en el archivo entero convierte esa
    explicacion en un falso positivo. (Paso: fue el primer intento de este test.)
    """
    return chr(10).join(
        linea for linea in ruta.read_text(encoding="utf-8").splitlines()
        if not linea.strip().startswith("#")
    )


def test_el_host_key_va_pineado_y_no_por_keyscan():
    """`ssh-keyscan` acepta lo que le conteste. Sobre una conexion que lleva una
    clave privada, eso es confiar-en-el-primer-uso contra un intermediario."""
    texto = _lineas_ejecutables(ACTION)
    assert "ssh-keyscan" not in texto
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5" in texto
    assert "StrictHostKeyChecking yes" in texto


def test_ninguna_credencial_se_imprime():
    """Un `echo $TOKEN` para depurar deja la credencial en el log del job, que es
    publico para cualquiera con lectura del repo."""
    texto = _lineas_ejecutables(ACTION)
    for prohibido in ("echo $TOKEN", "echo $SSH_KEY", "echo ${TOKEN}", "echo ${SSH_KEY}",
                      "cat ~/.ssh/nosilo_core"):
        assert prohibido not in texto, prohibido


def test_prod_boot_instala_el_extra_core_cuando_hay_credencial():
    """`.[web,mcp]` no tira de nosilo-core (esta en el extra `[core]`). Sin
    ese extra el import de webapp.app falla igual despues de autenticar.
    """
    texto = WORKFLOW.read_text(encoding="utf-8")
    assert 'pip install -e ".[core,web,mcp]"' in texto


def test_la_dependencia_publicada_sigue_siendo_https():
    """LO QUE HACE QUE ESTE CAMBIO NO RAMIFIQUE.

    El deploy key es SOLO de la CI: se reescribe la url en el runner. Si alguien
    cambia `requirements.txt` a `git+ssh://`, rompe Docker, Fly, Railway y el
    `pip install` de cualquiera que clone sin una clave SSH cargada.
    """
    for archivo in ("requirements.txt", "pyproject.toml"):
        texto = (RAIZ / archivo).read_text(encoding="utf-8")
        assert "nosilo-core @ git+https://" in texto, archivo
        assert "git+ssh://" not in texto, archivo
