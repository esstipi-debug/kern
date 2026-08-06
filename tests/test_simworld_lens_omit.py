"""``omit`` en la lente: descomponer un nivel de suciedad sin tocar el resto.

El barrido de 25 mundos midio que la captura de Kern cae de 10,6% (nivel 2) a
0,6% (nivel 3) -- es decir, a cero. El nivel 3 tiene cuatro inyectores y el
numero agregado no dice cual se come la ventaja. ``omit`` es lo que permite
preguntarlo.

Lo que estos tests protegen es que el parametro sea *inerte por defecto*: si
anadirlo hubiera cambiado en algo la suciedad de una corrida normal, todas las
mediciones anteriores dejarian de ser comparables con las futuras y nadie se
enteraria.
"""

from __future__ import annotations

import numpy as np
import pytest

from examples.run_world_sim import simulate_oracle
from src.simworld.lens import (
    DIRT_BY_LEVEL,
    LEVELS,
    apply_dirt,
    export_history,
    history_frame,
    nombres_de_inyectores,
)
from src.simworld.world import generate_world


@pytest.fixture(scope="module")
def historico():
    world = generate_world(seed=21, n_skus=6, n_suppliers=2, weeks=16)
    outs = simulate_oracle(world)
    return world, outs, history_frame(world, outs)


# --------------------------------------------------------------------------
# Inercia: sin omit, nada cambia
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_omit_vacio_da_exactamente_la_misma_suciedad_de_siempre(historico, level):
    """El test que hace que el parametro sea seguro de anadir."""
    _, _, df = historico
    sin_arg = apply_dirt(df, level=level, seed=5)
    con_vacio = apply_dirt(df, level=level, seed=5, omit=())
    assert sin_arg.equals(con_vacio)


@pytest.mark.parametrize("level", LEVELS)
def test_omitir_un_inyector_de_otro_nivel_no_afecta_a_este(historico, level):
    """Omitir algo que este nivel no corre no puede cambiar su resultado.

    Es la comprobacion de que el filtro mira el nombre del inyector que va a
    correr y no, por ejemplo, aborta el bucle entero.
    """
    _, _, df = historico
    if level == LEVELS[-1]:
        pytest.skip("el ultimo nivel corre todos los inyectores")
    ajenos = set(nombres_de_inyectores()) - set(
        f.__name__ for lvl in range(1, level + 1) for f in DIRT_BY_LEVEL[lvl]
    )
    base = apply_dirt(df, level=level, seed=5)
    con_omit = apply_dirt(df, level=level, seed=5, omit=frozenset(ajenos))
    assert base.equals(con_omit)


# --------------------------------------------------------------------------
# Efecto: con omit, ese inyector no corre
# --------------------------------------------------------------------------


def test_omitir_todos_los_inyectores_deja_el_dato_limpio(historico):
    _, _, df = historico
    sucio_del_todo = apply_dirt(df, level=3, seed=5, omit=nombres_de_inyectores())
    limpio = apply_dirt(df, level=0, seed=5)
    assert sucio_del_todo.equals(limpio)


def test_omitir_los_duplicados_deja_menos_filas_que_el_nivel_completo(historico):
    """Efecto observable y concreto, no un 'algo cambio'."""
    _, _, df = historico
    completo = apply_dirt(df, level=3, seed=5)
    sin_dup = apply_dirt(df, level=3, seed=5, omit=("inject_duplicate_rows",))
    assert len(sin_dup) < len(completo)


def test_omitir_el_costo_cero_quita_los_costos_en_cero(historico):
    _, _, df = historico
    completo = apply_dirt(df, level=3, seed=5)
    sin_cero = apply_dirt(df, level=3, seed=5, omit=("inject_zero_cost",))
    ceros_completo = (completo["unit_cost"].astype(str) == "0.0").sum()
    ceros_sin = (sin_cero["unit_cost"].astype(str) == "0.0").sum()
    assert ceros_completo > 0, "el fixture no ejerce inject_zero_cost"
    assert ceros_sin < ceros_completo


def test_cada_inyector_del_nivel_3_cambia_algo_al_omitirse(historico):
    """Si omitir uno no cambia nada, ese inyector no esta haciendo su trabajo
    en este fixture y la descomposicion lo leeria como 'inofensivo'."""
    _, _, df = historico
    completo = apply_dirt(df, level=3, seed=5)
    inertes = []
    for nombre in nombres_de_inyectores(3):
        if apply_dirt(df, level=3, seed=5, omit=(nombre,)).equals(completo):
            inertes.append(nombre)
    assert not inertes, f"inyectores sin efecto observable: {inertes}"


# --------------------------------------------------------------------------
# El contrato del parametro
# --------------------------------------------------------------------------


def test_un_nombre_de_inyector_inexistente_falla_fuerte(historico):
    """Un typo en omit convertiria una descomposicion en una corrida normal
    disfrazada, y el numero saldria sin ninguna senal de que esta mal."""
    _, _, df = historico
    with pytest.raises(ValueError, match="inyectores desconocidos"):
        apply_dirt(df, level=3, seed=5, omit=("inject_lo_que_sea",))


def test_nombres_de_inyectores_cubre_todos_los_niveles():
    todos = nombres_de_inyectores()
    for lvl in LEVELS:
        for f in DIRT_BY_LEVEL[lvl]:
            assert f.__name__ in todos
    assert len(todos) == sum(len(DIRT_BY_LEVEL[lvl]) for lvl in LEVELS)


def test_nombres_de_inyectores_de_un_nivel_solo_trae_los_suyos():
    assert set(nombres_de_inyectores(3)) == {
        f.__name__ for f in DIRT_BY_LEVEL[3]
    }


def test_export_history_pasa_omit_a_la_lente(historico, tmp_path):
    world, outs, _ = historico
    completo = export_history(world, outs, tmp_path / "a.csv", level=3, seed=5)
    limpio = export_history(world, outs, tmp_path / "b.csv", level=3, seed=5,
                            omit=nombres_de_inyectores())
    assert completo.read_text(encoding="utf-8") != limpio.read_text(encoding="utf-8")


def test_omitir_no_toca_la_columna_que_la_tarea_8_lee(historico):
    """``lead_time_std`` debe quedar identica en todos los niveles -- y omitir
    no puede convertirse en la via por la que eso deje de cumplirse."""
    _, _, df = historico
    if "lead_time_std" not in df.columns:
        pytest.skip("el historico no lleva lead_time_std en esta version")
    base = df["lead_time_std"].to_numpy()
    for omit in ((), ("inject_zero_cost",), nombres_de_inyectores(3)):
        sucio = apply_dirt(df, level=3, seed=5, omit=omit)
        # Las filas pueden duplicarse o desaparecer; lo que no puede es que la
        # columna cambie de valores respecto del conjunto original.
        assert set(np.unique(sucio["lead_time_std"].to_numpy())) <= set(np.unique(base))
