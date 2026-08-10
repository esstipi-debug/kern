"""El loop no puede publicar un puntaje que no midio.

Esa es la propiedad que estos tests defienden, y es la que la primera version
del script rompia: publicaba 10.0/10 con el smoke apuntando a un archivo
inexistente, el simworld corriendo `pytest` sobre un script sin tests, y la
bateria nunca invocada. El puntaje no mentia por un calculo malo sino por
omision -- lo que no corrio simplemente no restaba.

De ahi la forma de casi todos los tests de abajo: separan **"no midio"** de
**"midio y fallo"**. Son estados distintos y confundirlos es exactamente el bug.

Ninguno corre la bateria, el smoke ni el simworld de verdad (77 s entre los
tres). Se los sustituye; lo unico que se importa real es la firma de `run_grid`,
en el unico test que pide un contrato con el mundo de afuera.
"""

from __future__ import annotations

import sys
import types

import pytest

from scripts import nosilo_kern_loop as loop

# --------------------------------------------------------------------------
# Puntaje: lo que no corrio no puntua NI entra al denominador
# --------------------------------------------------------------------------


def _comp(nombre: str, peso: float, obtenido: float | None) -> loop.Componente:
    return loop.Componente(nombre=nombre, peso=peso, obtenido=obtenido, detalle="")


def test_un_componente_que_no_corrio_no_suma_puntos_ni_escala():
    """El denominador tiene que encogerse con el numerador.

    Si el peso de un componente ausente siguiera contando, un `--check` daria
    1,00/10 y se leeria como catastrofe; si contara como acierto, daria 10/10 y
    se leeria como todo bien. Las dos lecturas son falsas.
    """
    p = loop.Puntaje((_comp("bateria", 6.0, 3.6), _comp("capacidades", 3.0, None)))
    assert p.obtenido == 3.6
    assert p.escala == 6.0
    assert p.parcial is True


def test_un_puntaje_completo_va_sobre_diez():
    p = loop.Puntaje((
        _comp("bateria", 6.0, 3.6), _comp("capacidades", 3.0, 3.0), _comp("higiene", 1.0, 1.0),
    ))
    assert (p.obtenido, p.escala, p.parcial) == (7.6, 10.0, False)


def test_los_pesos_declarados_suman_diez():
    """Si alguien agrega un componente y olvida rebalancear, el completo deja de
    ser sobre 10 y la cifra publicada cambia de significado en silencio."""
    assert loop.PESO_BATERIA + loop.PESO_CAPACIDADES + loop.PESO_HIGIENE == 10.0


def test_la_firma_solo_nombra_lo_que_corrio():
    p = loop.Puntaje((_comp("bateria", 6.0, 3.6), _comp("capacidades", 3.0, None)))
    assert p.firma == "bateria"


def test_check_y_full_no_comparten_firma():
    """La tendencia se calcula comparando firmas. Si un `--check` y un `--full`
    tuvieran la misma, el delta entre ellos seria un numero inventado."""
    check = loop.Puntaje((_comp("higiene", 1.0, 1.0), _comp("bateria", 6.0, None)))
    full = loop.Puntaje((_comp("higiene", 1.0, 1.0), _comp("bateria", 6.0, 3.6)))
    assert check.firma != full.firma


# --------------------------------------------------------------------------
# Capacidades: el codigo de salida manda, no el texto
# --------------------------------------------------------------------------

#: Salida real y verde, con el warning de pandas que rompia la deteccion por texto.
SALIDA_VERDE_CON_WARNING = '''
jobs/pricing.py:49: UserWarning: Could not infer format, so each element will be
    out["date"] = pd.to_datetime(raw[mapping["date"]], errors="coerce")

5 passed, 10 warnings in 61.47s (0:01:01)
'''


@pytest.mark.parametrize("rc", sorted(loop.PYTEST_NO_MIDIO))
def test_pytest_que_no_midio_no_puntua_y_es_critico(monkeypatch, rc):
    """rc=4 (ruta inexistente) y rc=5 (no colecto tests) son el bug original.

    Lo importante es `obtenido is None`: un 0.0 diria "corri y falle", que es
    una afirmacion mas fuerte de la que se puede sostener.
    """
    monkeypatch.setattr(loop, "run_cmd", lambda *a, **k: (rc, "no tests ran"))
    issues: list[loop.DetectIssue] = []
    componente = loop.puntuar_capacidades(issues)
    assert componente.obtenido is None
    assert [i.kind for i in issues] == ["smoke_no_midio"]
    assert issues[0].severity == "critical"


def test_si_falta_el_archivo_del_smoke_no_puntua_y_es_critico(monkeypatch):
    """El bug original exacto: apuntar a un test que no existe."""
    monkeypatch.setattr(loop, "TEST_CAPACIDADES", "tests/test_que_no_existe.py")
    issues: list[loop.DetectIssue] = []
    componente = loop.puntuar_capacidades(issues)
    assert componente.obtenido is None
    assert [i.kind for i in issues] == ["smoke_ausente"]
    assert issues[0].severity == "critical"


def test_un_smoke_que_falla_puntua_cero_pero_consta_que_corrio(monkeypatch):
    monkeypatch.setattr(loop, "run_cmd", lambda *a, **k: (1, "2 failed, 3 passed in 1.0s"))
    issues: list[loop.DetectIssue] = []
    componente = loop.puntuar_capacidades(issues)
    assert componente.obtenido == 0.0
    assert [i.kind for i in issues] == ["smoke_falla"]


def test_un_warning_de_pandas_no_convierte_cinco_passed_en_falla(monkeypatch):
    """`errors="coerce"` contiene la subcadena `" error"`.

    Buscarla en el cuerpo marcaba como falla una corrida de 5 passed -- medido
    sobre la salida real, no hipotetico. Por eso el veredicto sale del rc.
    """
    monkeypatch.setattr(loop, "run_cmd", lambda *a, **k: (0, SALIDA_VERDE_CON_WARNING))
    issues: list[loop.DetectIssue] = []
    componente = loop.puntuar_capacidades(issues)
    assert componente.obtenido == loop.PESO_CAPACIDADES
    assert issues == []


def test_un_timeout_no_puntua(monkeypatch):
    monkeypatch.setattr(loop, "run_cmd", lambda *a, **k: (-1, "TIMEOUT"))
    issues: list[loop.DetectIssue] = []
    assert loop.puntuar_capacidades(issues).obtenido is None
    assert [i.kind for i in issues] == ["smoke_no_corre"]


# --------------------------------------------------------------------------
# DETECT: imports viejos
# --------------------------------------------------------------------------


def _repo_falso(monkeypatch, tmp_path, contenido: str | None, nombre: str = "mod.py"):
    if contenido is not None:
        (tmp_path / nombre).write_text(contenido, encoding="utf-8")
    monkeypatch.setattr(loop, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(loop, "CORE_CONSUMERS", (nombre,))


def test_detecta_el_import_viejo_con_su_linea(monkeypatch, tmp_path):
    _repo_falso(monkeypatch, tmp_path, "import os\nfrom src.guided import algo\n")
    issues = loop.detectar_imports_viejos()
    assert [i.kind for i in issues] == ["import_viejo"]
    assert issues[0].line == 2
    assert "nosilo_core.guided" in issues[0].suggested_fix


def test_el_import_ya_migrado_no_se_marca(monkeypatch, tmp_path):
    _repo_falso(monkeypatch, tmp_path, "from nosilo_core.guided import algo\n")
    assert loop.detectar_imports_viejos() == []


def test_un_consumidor_ausente_es_hallazgo_alto(monkeypatch, tmp_path):
    _repo_falso(monkeypatch, tmp_path, None, nombre="no_existe.py")
    issues = loop.detectar_imports_viejos()
    assert [(i.kind, i.severity) for i in issues] == [("consumidor_faltante", "high")]


# --------------------------------------------------------------------------
# PROPOSE: las zonas excluidas se reportan, nunca se proponen
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [
    "src/writeback.py",
    "src/writeback_store.py",
    "src/connectors/odoo.py",
    "src/mcp_keys.py",
    "webapp/security.py",
    "webapp/mcp_auth.py",
    "pyproject.toml",
    "Dockerfile",
])
def test_las_zonas_sensibles_estan_excluidas(ruta):
    assert loop.es_zona_excluida(ruta)


def test_un_hallazgo_en_zona_excluida_se_reporta_pero_no_se_propone(capsys):
    """Ver el desvio en writeback si; que un arreglo automatico lo toque no."""
    issues = [loop.DetectIssue(
        "import_viejo", "high", "m", "src/writeback.py", 3, "reemplazar X por Y")]
    loop.proponer_arreglos(issues)
    salida = capsys.readouterr().out
    assert "OMITIDO (zona excluida)" in salida
    assert "reemplazar X por Y" not in salida


def test_un_hallazgo_fuera_de_zona_excluida_si_se_propone(capsys):
    issues = [loop.DetectIssue(
        "import_viejo", "high", "m", "jobs/qa.py", 3, "reemplazar X por Y")]
    loop.proponer_arreglos(issues)
    salida = capsys.readouterr().out
    assert "reemplazar X por Y" in salida
    assert "OMITIDO" not in salida


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------


def test_un_estado_corrupto_se_archiva_y_no_voltea_el_loop(monkeypatch, tmp_path):
    """Una escritura interrumpida no puede dejar el bucle muerto."""
    estado = tmp_path / "estado.json"
    estado.write_text('{"runs": [ROTO', encoding="utf-8")
    monkeypatch.setattr(loop, "STATE_PATH", estado)
    assert loop.load_state() == {"runs": []}
    assert estado.with_suffix(".corrupto.json").exists()


def test_un_estado_que_no_es_objeto_se_descarta(monkeypatch, tmp_path):
    estado = tmp_path / "estado.json"
    estado.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(loop, "STATE_PATH", estado)
    assert loop.load_state() == {"runs": []}


def test_un_estado_sano_se_lee_tal_cual(monkeypatch, tmp_path):
    estado = tmp_path / "estado.json"
    estado.write_text('{"runs": [], "last_bateria_pct": 45.0}', encoding="utf-8")
    monkeypatch.setattr(loop, "STATE_PATH", estado)
    assert loop.load_state()["last_bateria_pct"] == 45.0


# --------------------------------------------------------------------------
# Simworld: alarma de regresion, no puntaje
# --------------------------------------------------------------------------


def _simworld_que_devuelve(monkeypatch, capturas: dict[str, float]) -> None:
    """Sustituye `examples.run_world_sim` entero: importarlo de verdad cuesta
    ~10 s y correrlo ~10 s mas, y ninguno de los dos costos prueba nada aca."""
    modulo = types.ModuleType("examples.run_world_sim")
    modulo.run_grid = lambda **_: {
        "kern": {n: {"capture": v} for n, v in capturas.items()}}
    monkeypatch.setitem(sys.modules, "examples.run_world_sim", modulo)


def test_una_captura_que_se_movio_levanta_regresion(monkeypatch):
    _simworld_que_devuelve(monkeypatch, {"forced-L0": -0.446})
    issues: list[loop.DetectIssue] = []
    capturas = loop.correr_simworld(issues, {"forced-L0": -0.396})
    assert capturas == {"forced-L0": -0.446}
    assert [(i.kind, i.severity) for i in issues] == [("simworld_regresion", "medium")]


def test_una_captura_identica_no_levanta_nada(monkeypatch):
    _simworld_que_devuelve(monkeypatch, {"forced-L0": -0.446})
    issues: list[loop.DetectIssue] = []
    loop.correr_simworld(issues, {"forced-L0": -0.446})
    assert issues == []


def test_un_movimiento_bajo_la_tolerancia_no_levanta_nada(monkeypatch):
    _simworld_que_devuelve(monkeypatch, {"forced-L0": -0.446})
    issues: list[loop.DetectIssue] = []
    loop.correr_simworld(issues, {"forced-L0": -0.446 + loop.TOLERANCIA_CAPTURA / 2})
    assert issues == []


def test_sin_base_previa_se_registra_pero_no_se_acusa(monkeypatch):
    """La primera corrida no tiene contra que comparar y no debe inventar una."""
    _simworld_que_devuelve(monkeypatch, {"forced-L0": -0.446})
    issues: list[loop.DetectIssue] = []
    assert loop.correr_simworld(issues, {}) == {"forced-L0": -0.446}
    assert issues == []


def test_si_el_simworld_no_importa_se_reporta_y_no_tumba_el_loop(monkeypatch):
    modulo = types.ModuleType("examples.run_world_sim")  # sin `run_grid`
    monkeypatch.setitem(sys.modules, "examples.run_world_sim", modulo)
    issues: list[loop.DetectIssue] = []
    assert loop.correr_simworld(issues, {}) == {}
    assert [i.kind for i in issues] == ["simworld_no_importa"]


def test_la_config_del_simworld_coincide_con_la_firma_de_run_grid():
    """El unico contrato con el mundo real de este archivo.

    Es la clase de bug que origino todo: el loop llamando a algo que no existe
    con la forma que cree. Si `run_grid` renombra un parametro, esto lo agarra
    aca y no como un `TypeError` a mitad de una corrida de 77 s.
    """
    import inspect

    from examples.run_world_sim import run_grid

    parametros = set(inspect.signature(run_grid).parameters)
    assert set(loop.SIMWORLD_BASE) <= parametros
    assert "out_dir" in parametros
