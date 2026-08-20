"""Las comprobaciones deben poder decir NO. Un check que solo sabe aprobar no mide.

Hay dos familias de tests aca y conviene no confundirlas:

- Los que corren la bateria de verdad (``puntuar()``) y congelan el VEREDICTO de
  hoy. Si el producto mejora, se ponen rojos: es el punto.
- Los que arman un candidato sintetico y le rompen una pieza por vez, para
  probar que el corrector sabe decir que no.

Desde el endurecimiento, un candidato sintetico ya no se describe con una lista
de nombres de campo: se describe con los VALORES que emitieron dos corridas. Un
test que solo nombra campos no puede distinguir una capacidad de un
``__dataclass_fields__`` con una linea de mas, que era exactamente el agujero.
"""

from __future__ import annotations

import dataclasses
import importlib
from contextlib import ExitStack, contextmanager

import pytest

import src.bateria.comprobaciones  # noqa: F401  -- registra las comprobaciones
from src.bateria.comprobaciones import (
    _CandidatoActa,
    _CandidatoAjuste,
    _CandidatoCaja,
    _CandidatoDevoluciones,
    _CandidatoExposicion,
    _CandidatoLeadTime,
    _CandidatoMarkdown,
    _CandidatoOC,
    _CandidatoScorecard,
    _CandidatoServicio,
    _Muestra,
    _veredicto_acta,
    _veredicto_ajuste,
    _veredicto_caja,
    _veredicto_devoluciones,
    _veredicto_exposicion,
    _veredicto_lead_time,
    _veredicto_markdown,
    _veredicto_oc,
    _veredicto_scorecard,
    _veredicto_servicio,
)
from src.bateria.scorer import Contexto, Estado, puntuar

_CSV = "stock_on_hand.csv"


def _muestra(**campos: object) -> _Muestra:
    """Una muestra sintetica: por campo, lo que emitieron dos corridas.

    Una tupla son los valores de cada corrida; un valor suelto es un campo
    constante (se repite en las dos), que es como se escribe "existe pero no se
    mueve".
    """
    return _Muestra({
        nombre: valor if isinstance(valor, tuple) else (valor, valor)
        for nombre, valor in campos.items()
    })


@contextmanager
def _campo_inyectado(modulo: str, clase: str, nombre: str, vacio: object):
    """Agrega al entregable un campo con el nombre que el gate busca y nada adentro.

    Es el ataque entero en una linea, y no requiere mala fe: quien lee el
    corrector ve que nombre mira y lo declara. El campo queda en
    ``__dataclass_fields__`` -- que es lo unico que miraban los gates nominales --
    y toda instancia lo devuelve vacio y constante.
    """
    cls = getattr(importlib.import_module(modulo), clase)
    campo = dataclasses.field(default=vacio)
    campo.name = nombre
    campo.type = "object"
    campo._field_type = dataclasses._FIELD
    cls.__dataclass_fields__[nombre] = campo
    setattr(cls, nombre, vacio)
    try:
        yield
    finally:
        del cls.__dataclass_fields__[nombre]
        delattr(cls, nombre)

# product_id, on_hand, daily_demand, unit_cost, days_since_last_sale
_FILAS_CON_RIESGO = (
    "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
    "SKU-MUERTO,500,0.0,12.5,400\n"  # sin venta hace >180d -> dead
    "SKU-EXCESO,900,1.0,8.0,10\n"  # 900 dias de cobertura -> excess
    "SKU-SANO,30,1.0,5.0,3\n"  # 30 dias -> healthy
)

_FILAS_TODO_SANO = (
    "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
    "SKU-SANO-A,30,1.0,5.0,3\n"
    "SKU-SANO-B,45,2.0,7.0,5\n"
)


def _contexto(tmp_path, contenido: str) -> Contexto:
    (tmp_path / _CSV).write_text(contenido, encoding="utf-8")
    return Contexto(data_root=tmp_path)


def _t11(puntaje):
    return next(r for r in puntaje.resultados if r.tarea_id == "T11")


def test_t11_sin_dataset_es_sin_dato_no_fallo_de_codigo(tmp_path):
    """Falta de dato del cliente no puede leerse como bug del motor."""
    resultado = _t11(puntuar(Contexto(data_root=tmp_path)))
    assert resultado.estado is Estado.SIN_DATO
    assert _CSV in resultado.evidencia


def test_t11_cumple_cuando_la_lista_trae_dinero_cobertura_y_accion(tmp_path):
    resultado = _t11(puntuar(_contexto(tmp_path, _FILAS_CON_RIESGO)))
    assert resultado.estado is Estado.CUMPLE, resultado.evidencia
    assert resultado.detalle["n_en_riesgo"] == 2
    assert resultado.detalle["faltantes"] == []
    assert resultado.puntos == 1.0


def test_t11_sin_sku_en_riesgo_no_acredita_la_tarea(tmp_path):
    """Un dataset donde nada esta excedido no prueba que Kern sepa detectarlo."""
    resultado = _t11(puntuar(_contexto(tmp_path, _FILAS_TODO_SANO)))
    assert resultado.estado is Estado.SIN_DATO
    assert resultado.puntos == 0.0


def test_t11_suma_al_marcador_global(tmp_path):
    puntaje = puntuar(_contexto(tmp_path, _FILAS_CON_RIESGO))
    t11 = _t11(puntaje)
    assert t11.puntos == 1.0
    assert puntaje.puntos >= 1.0


def _resultado(puntaje, tarea_id: str):
    return next(r for r in puntaje.resultados if r.tarea_id == tarea_id)


# --- T01 / T02: estructurales, no dependen del dataset -----------------------


def test_t01_cumple_el_entregable_ya_lleva_banda_de_incertidumbre():
    """La fuente: 'Sin banda de incertidumbre NO cuenta'. El job propaga la sigma
    del error de pronostico, declara cual dispersion uso y emite el P10-P90."""
    r = _resultado(puntuar(), "T01")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert set(r.detalle["campos_de_dispersion"]) == {"error_std", "p10", "p90", "sigma_source"}


def test_t02_es_parcial_porque_la_sonda_no_nombra_ningun_store():
    """T02 volvio a PARCIAL, y el CUMPLE anterior estaba sostenido por un bug.

    La sonda de T02 corre ``job.run()`` DOS veces, sobre dos datasets distintos
    (``SKU-EST`` y ``SKU-VOL``), y sin pasar ``store``. Mientras ``store=None``
    resolvia ``default_store()``, las dos corridas compartian la particion sin
    nombre: la segunda leia el ciclo congelado por la PRIMERA y la comparacion se
    movia. O sea que el tercer requisito se acreditaba con el cruce de datos entre
    clientes, que es exactamente el bug que se cerro (ver
    ``jobs/forecast_cycle.NO_STORE_MEMORY`` y
    ``tests/test_forecast_cycle.py::test_two_datasets_without_a_store_do_not_see_each_other``).

    Hoy un llamador que no nombra store no tiene ciclo previo, y la bateria lo
    dice con esas palabras. PARCIAL es el veredicto honesto de la sonda tal como
    esta escrita. La capacidad SI esta cableada -- eso se prueba abajo, contra un
    store explicito -- y el dia que la comprobacion le pase uno propio y aislado,
    T02 vuelve a CUMPLE sin tocar el producto.
    """
    r = _resultado(puntuar(), "T02")

    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.detalle["faltantes"] == ["comparacion contra el ciclo previo"]
    assert r.puntos == 0.5
    assert len(set(r.detalle["sonda_mape"].values())) == 2  # la sonda sigue siendo valida
    assert all(v != 0.0 for v in r.detalle["sonda_sesgo"].values())
    # MAPE y sesgo firmado, los otros dos requisitos, siguen acreditando
    assert "MAPE por SKU" not in r.detalle["faltantes"]
    assert "sesgo firmado" not in r.detalle["faltantes"]


def test_t02_la_capacidad_esta_cableada_cuando_se_nombra_un_store(tmp_path):
    """La otra mitad del test de arriba: lo que falta es el store de la sonda, no
    la capacidad del entregable. Con un store propio y aislado -- las mismas dos
    series con las que sondea T02 -- el ciclo previo queda congelado y los cinco
    campos de comparacion salen con valor y se mueven."""
    from jobs import forecast_job as fj
    from src.bateria.comprobaciones import _SERIE_ESTABLE, _SERIE_VOLATIL
    from src.state.store import StateStore

    store = StateStore(tmp_path / "state")
    estable = fj.run({"SKU-EST": list(_SERIE_ESTABLE)}, store=store, cycle_id="c1")
    volatil = fj.run({"SKU-VOL": list(_SERIE_VOLATIL)}, store=store, cycle_id="c2")

    movidos = _Muestra.de_corridas(
        estable.skus, volatil.skus, (estable,), (volatil,)
    ).se_mueve(("previo", "anterior", "previous", "delta", "vs_"))

    assert volatil.previous_cycle_id == "c1"
    assert sorted(movidos) == [
        "mean_bias_vs_previous",
        "mean_mape_vs_previous",
        "previous_cycle_id",
        "previous_mean_bias",
        "previous_mean_mape",
    ]


# --- El ataque de inyeccion de nombre, contra la bateria entera --------------

#: Los campos que, declarados VACIOS, movian cinco tareas del marcador sin una
#: sola capacidad nueva: +3,5 puntos (37,50% -> 55,00%) sobre los gates nominales.
#: Por tarea: modulo, clase del entregable y campos a inyectar. SIN veredicto
#: esperado, a proposito. La version anterior fijaba el estado de cada tarea y se
#: puso roja el dia que T13 y T14 se GANARON de verdad, al recuperar #219 y #220.
#: Un test que se rompe cuando el producto mejora entrena a editarlo, y el dia que
#: haya que editarlo por la razon equivocada nadie lo va a notar.
_ATAQUE_DE_NOMBRE = (
    ("T02", "jobs.forecast_job", "SkuForecast",
     (("mape_previous", 0.0),)),
    ("T05", "jobs.ddmrp_job", "DdmrpPart",
     (("supplier", ""), ("expected_date", ""))),
    ("T07", "src.sop_engine.purchase_plan", "PurchasePlanLine",
     (("payment_date", ""), ("stockout_units", 0.0))),
    ("T13", "src.risk", "RankedMitigation",
     (("due_date", ""),)),
    ("T14", "jobs.hats_job", "SettlementReport",
     (("demand_plan", ""), ("gap", ""), ("owner", ""), ("due_date", ""))),
)


@pytest.mark.parametrize(
    ("tarea_id", "modulo", "clase", "campos"),
    _ATAQUE_DE_NOMBRE,
    ids=[a[0] for a in _ATAQUE_DE_NOMBRE],
)
def test_declarar_el_campo_vacio_no_mueve_la_tarea(tarea_id, modulo, clase, campos):
    """El test que sostiene todo el endurecimiento.

    Con los gates nominales, agregar estos campos VACIOS al dataclass llevaba el
    marcador de 7,5/20 a 11,0/20 sin construir nada: los gates preguntaban si
    ``__dataclass_fields__`` contenia cierta subcadena. Si este test se pone
    rojo, la vara volvio a medir nombres.

    Se mide limpio PRIMERO y se compara. Lo que se afirma es RELATIVO -- el ataque
    no mueve el veredicto, sea cual sea el veredicto -- para que el test sobreviva
    a que el producto gane la tarea por las buenas.
    """
    limpio = _resultado(puntuar(), tarea_id)

    with ExitStack() as pila:
        for nombre, vacio in campos:
            pila.enter_context(_campo_inyectado(modulo, clase, nombre, vacio))
        atacado = _resultado(puntuar(), tarea_id)

    assert atacado.estado is limpio.estado, (
        f"{tarea_id} paso de {limpio.estado.name} a {atacado.estado.name} solo por declarar "
        f"{[n for n, _ in campos]} vacio(s): {atacado.evidencia}"
    )


@contextmanager
def _campo_cableado(modulo: str, clase: str, nombre: str, calcular):
    """Como :func:`_campo_inyectado` pero el campo lleva un valor de verdad.

    Sirve para lo contrario: probar que el gate endurecido SI se puede pasar. Un
    gate que ningun entregable concebible aprueba esta tan roto como uno que
    aprueba cualquier nombre -- es el error que tenia T10.
    """
    cls = getattr(importlib.import_module(modulo), clase)
    campo = dataclasses.field(default=None)
    campo.name = nombre
    campo.type = "object"
    campo._field_type = dataclasses._FIELD
    cls.__dataclass_fields__[nombre] = campo
    setattr(cls, nombre, property(calcular))
    try:
        yield
    finally:
        del cls.__dataclass_fields__[nombre]
        delattr(cls, nombre)


def test_t02_un_delta_contra_el_ciclo_previo_con_valor_si_acredita():
    """El gate endurecido de T02 no es imposible: es exigente.

    Mismo campo que el ataque, pero cableado: un delta que cambia cuando cambia
    el error del ciclo. Eso es lo que un ciclo previo congelado produciria, y la
    tarea pasa a CUMPLE. Sin esta prueba, "endurecer" seria indistinguible de
    "romper".

    El campo inyectado esta SOLO en la lista, y eso es lo correcto: la sonda de
    T02 corre ``job.run()`` sin ``store``, y desde que la memoria de ciclo es
    opt-in una corrida sin store no congela ni compara (los cinco campos que
    ``jobs/forecast_cycle.py`` cablea de verdad quedan en ``None``, iguales en las
    dos corridas). Antes acompanaban al inyectado porque las dos corridas -- dos
    datasets distintos -- compartian la particion sin nombre. La igualdad se
    mantiene EXACTA (no se afloja a un ``in``) justamente para que la lista siga
    siendo auditable campo por campo.
    """
    with _campo_cableado(
        "jobs.forecast_job", "SkuForecast",
        "mape_vs_previous", lambda self: round(self.mape - 0.10, 6),
    ):
        r = _resultado(puntuar(), "T02")

    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["campos_de_ciclo_previo_con_valor_que_se_mueve"] == ["mape_vs_previous"]


def test_el_ataque_completo_no_mueve_el_marcador():
    """Los diez campos vacios a la vez, contra el marcador publicable."""
    limpio = puntuar().puntos
    with ExitStack() as pila:
        for _, modulo, clase, campos in _ATAQUE_DE_NOMBRE:
            for nombre, vacio in campos:
                pila.enter_context(_campo_inyectado(modulo, clase, nombre, vacio))
        atacado = puntuar().puntos

    assert atacado == limpio, f"cero producto movio el marcador: {limpio} -> {atacado}"


# --- T03: segmentacion con servicio diferenciado -----------------------------

_CSV_VENTAS = "sales_history.csv"

# Costo unitario PLANO: el ABC ordena por unidades y se rotula valor anual.
_VENTAS_COSTO_PLANO = "product_id,quantity,unit_cost\n" + "".join(
    f"SKU-{i:02d},{10 + i * 7},1.0\n" for i in range(12) for _ in range(6)
)

# Costo unitario dispar: el ABC ordena por valor de verdad.
_VENTAS_COSTO_DISPAR = "product_id,quantity,unit_cost\n" + "".join(
    f"SKU-{i:02d},{10 + i * 7},{1.0 + i * 9.5}\n" for i in range(12) for _ in range(6)
)


def _ctx_ventas(tmp_path, contenido: str) -> Contexto:
    (tmp_path / _CSV_VENTAS).write_text(contenido, encoding="utf-8")
    return Contexto(data_root=tmp_path)


def test_t03_sin_ventas_es_sin_dato(tmp_path):
    assert _resultado(puntuar(Contexto(data_root=tmp_path)), "T03").estado is Estado.SIN_DATO


def test_t03_con_costo_plano_es_parcial_el_abc_ordena_por_unidades(tmp_path):
    """Un ranking por unidades rotulado 'valor anual' no es ABC por facturacion."""
    r = _resultado(puntuar(_ctx_ventas(tmp_path, _VENTAS_COSTO_PLANO)), "T03")
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.detalle["ranking_por_unidades"] is True
    assert "UNIDADES" in r.evidencia


def test_t03_cumple_con_costo_dispar_y_servicio_diferenciado(tmp_path):
    r = _resultado(puntuar(_ctx_ventas(tmp_path, _VENTAS_COSTO_DISPAR)), "T03")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.detalle["ranking_por_unidades"] is False
    assert len(r.detalle["niveles_de_servicio"]) > 1


def test_t03_segmenta_de_verdad_celdas_y_niveles_diferenciados(tmp_path):
    """Lo que si se puede acreditar hoy: hay mas de un nivel de servicio."""
    r = _resultado(puntuar(_ctx_ventas(tmp_path, _VENTAS_COSTO_PLANO)), "T03")
    assert len(r.detalle["niveles_de_servicio"]) > 1
    assert len(r.detalle["celdas"]) > 1


# --- T04: politica con SS sensible al lead time ------------------------------


def test_t04_cumple_y_el_ss_reacciona_al_lead_time():
    """El filo del criterio es de comportamiento: duplicar sigma_L debe mover el SS."""
    r = _resultado(puntuar(), "T04")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.detalle["reacciona_al_lead_time"] is True
    base = r.detalle["ss_sin_sigma_lt"]
    con = r.detalle["ss_con_sigma_lt"]
    assert all(b > a for a, b in zip(base, con, strict=True))


def test_t04_el_entregable_declara_si_sigma_lt_fue_medido_o_defaulteado():
    """Un SS calculado con sigma_L=0 por defecto no es lo mismo que uno medido."""
    assert _resultado(puntuar(), "T04").detalle["declara_fuente_de_sigma_lt"] is True


# --- T05: propuesta de OC por proveedor, descontando el en-transito ----------


def _candidato(**cambios) -> _CandidatoOC:
    """Un candidato que cumple TODO el criterio; los tests rompen una pieza por vez."""
    base = {
        "tool": "oc_ideal",
        "entregable": "jobs.oc_job.LineaOC",
        "muestra": _muestra(
            supplier=("S-NORTE", "S-SUR"),
            sku=("SKU-1", "SKU-2"),
            order_qty=(120.0, 80.0),
            required_date=("2026-08-14", "2026-08-28"),
            reason=("bajo ROP", "quiebre proyectado 2026-08-20"),
        ),
        "descuenta_en_transito": True,
        "prueba_en_transito": "con 80 unidades emitidas la cantidad baja: [100] -> [20]",
    }
    return _CandidatoOC(**{**base, **cambios})


def test_t05_cumple_la_oc_sale_por_proveedor_con_fechas_y_razon():
    """Veredicto real: CUMPLE. Antes era NO_CUMPLE y el test lo congelaba asi.

    Lo que cambio es el producto, no la vara. ddmrp ya era el unico que neteaba el
    en-transito (el filo eliminatorio), pero entregaba una senal de buffer por
    parte: sin proveedor y sin fechas eso no es una OC. Ahora ``DdmrpPart`` cuelga
    un ``po_line`` -- proveedor, SKU, cantidad, fecha de pedido, fecha requerida,
    llegada esperada y razon -- armado por ``src/purchase_proposal.py``, y los
    cinco requisitos se acreditan por VALOR QUE SE MUEVE entre las dos corridas de
    la sonda (dos pares de proveedores y dos lead times distintos).

    El docstring anterior decia "si algun dia un entregable agrupa por proveedor,
    este test se pone rojo". Ese dia llego: se edita por la razon correcta y queda
    escrito cual fue. Los otros nueve tests de T05 -- los sinteticos sobre
    ``_veredicto_oc`` / ``_candidato`` -- no se tocan: siguen probando que el
    corrector sabe decir que no.
    """
    r = _resultado(puntuar(), "T05")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["mejor"] == "ddmrp"
    por_tool = {c["tool"]: c for c in r.detalle["candidatos"]}
    assert por_tool["ddmrp"]["descuenta_en_transito"] is True
    assert por_tool["ddmrp"]["faltantes"] == []
    assert por_tool["ddmrp"]["campos_solo_de_nombre"] == []
    assert "po_line.supplier" in por_tool["ddmrp"]["campos"]
    assert "po_line.expected_arrival" in por_tool["ddmrp"]["campos"]
    # Los otros dos entregables NETEAN desde kern#251, y cada uno lo prueba
    # corriendo el job dos veces, no exponiendo un campo. El docstring anterior
    # decia "los otros dos siguen sin netear": ese dia paso, se edita por la razon
    # correcta y queda escrito cual fue.
    #
    # El veredicto sigue ganandose con ddmrp: es el unico que ademas agrupa la OC
    # por proveedor con fechas. Que los tres neteen no mueve el puntaje -- T05 se
    # puntua con el MEJOR candidato -- y por eso este cambio no inflo nada.
    assert por_tool["excel_replenishment"]["descuenta_en_transito"] is True
    assert por_tool["odoo_replenishment"]["descuenta_en_transito"] is True
    # Y lo prueban por VALOR QUE SE MUEVE, no por nombre de campo: la evidencia de
    # cada uno tiene que mostrar las dos corridas.
    for tool in ("excel_replenishment", "odoo_replenishment", "ddmrp"):
        assert "->" in por_tool[tool]["prueba_en_transito"], (
            f"{tool} acredita el neteo sin mostrar dos corridas: "
            f"{por_tool[tool]['prueba_en_transito']}"
        )


def test_t05_cumple_con_oc_completa_que_netea_el_en_transito():
    r = _veredicto_oc((_candidato(),))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t05_ignorar_el_en_transito_es_eliminatorio_aunque_la_oc_este_completa():
    """La fuente: 'si se ignoran las ordenes ya emitidas, sobre-compra y no cuenta'."""
    r = _veredicto_oc((
        _candidato(descuenta_en_transito=False, prueba_en_transito="no hay campo de en-transito"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "en-transito" in r.evidencia


def test_t05_campo_de_en_transito_sin_sonda_no_se_acredita():
    """``None`` = hay campo pero nadie probo que se netee. No se regala el punto."""
    r = _veredicto_oc((
        _candidato(descuenta_en_transito=None, prueba_en_transito="sin sonda de comportamiento"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert "sin sonda" in r.evidencia


def test_t05_es_parcial_cuando_solo_falta_una_pieza_de_la_oc():
    r = _veredicto_oc((_candidato(muestra=_muestra(
        supplier=("S-NORTE", "S-SUR"), sku=("SKU-1", "SKU-2"),
        order_qty=(120.0, 80.0), reason=("bajo ROP", "quiebre proyectado"),
    )),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "fecha de pedido / llegada esperada" in r.evidencia


def test_t05_un_campo_de_proveedor_vacio_no_es_una_oc_por_proveedor():
    """El ataque, en la unidad: el nombre esta y el valor no. No acredita."""
    r = _veredicto_oc((_candidato(muestra=_muestra(
        supplier=("", ""), sku=("SKU-1", "SKU-2"), order_qty=(120.0, 80.0),
        required_date=("2026-08-14", "2026-08-28"), reason=("bajo ROP", "quiebre"),
    )),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "OC agrupada por proveedor" in r.evidencia
    assert "supplier" in r.detalle["candidatos"][0]["campos_solo_de_nombre"]


def test_t05_un_proveedor_constante_tampoco_agrupa_por_proveedor():
    """Dos SKU de dos proveedores distintos no pueden salir con el mismo valor:
    un campo que no se mueve no esta transportando el proveedor."""
    r = _veredicto_oc((_candidato(muestra=_muestra(
        supplier="S-UNICO", sku=("SKU-1", "SKU-2"), order_qty=(120.0, 80.0),
        required_date=("2026-08-14", "2026-08-28"), reason=("bajo ROP", "quiebre"),
    )),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "OC agrupada por proveedor" in r.evidencia


def test_t05_un_entregable_sin_sonda_de_valores_no_acredita_nada():
    """``de_nombres`` = se conocen los campos y ningun valor. Hay que escribir la sonda."""
    cand = _candidato(muestra=_Muestra.de_nombres(
        ("supplier", "sku", "order_qty", "required_date", "reason")
    ))
    assert cand.faltantes == list(cand.requisitos)
    assert cand.solo_de_nombre == []  # no es que este vacio: es que nadie lo corrio


def test_t06_cumple_el_plan_cuantifica_el_exceso_y_ahora_recomienda():
    """Veredicto real: el plan de compra rotula lo que el MOQ y el multiplo de caja
    compraron de mas (238 u / 2.380 usd), cuanta cobertura agregan, y desde que
    ``PurchasePlanLine`` trae ``constraint_verdict`` tambien dice si conviene."""
    r = _resultado(puntuar(), "T06")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["mejor"] == "integrated_plan (purchase plan)"
    por_tool = {c["tool"]: c for c in r.detalle["candidatos"]}
    plan = por_tool["integrated_plan (purchase plan)"]
    assert plan["cuantifica_el_exceso"] is True
    assert plan["faltantes"] == []
    assert "238 u de mas" in plan["prueba_exceso"]
    assert "constraint_extra_units" in plan["prueba_exceso"]


def _candidato_ajuste(**cambios) -> _CandidatoAjuste:
    """Un ajuste que cumple TODO el criterio; los tests rompen una pieza por vez."""
    base = {
        "tool": "ajuste_ideal",
        "entregable": "jobs.oc_job.LineaAjustada",
        "restriccion": "MOQ 250",
        "campos": ("order_qty", "delta_costo_total_usd", "exceso_unidades",
                   "cobertura_agregada_meses", "conviene"),
        "ajusta": True,
        "prueba_ajuste": "la cantidad sube de 50 a 250 u",
        "prueba_exceso": "el redondeo compra 200 u de mas (2.000 usd)",
    }
    return _CandidatoAjuste(**{**base, **cambios})


def test_t06_cumple_con_exceso_cobertura_y_recomendacion():
    r = _veredicto_ajuste((_candidato_ajuste(),))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t06_redondear_sin_cuantificar_el_exceso_es_eliminatorio():
    """La fuente: \"'Redondear hacia arriba' sin cuantificar el exceso NO cuenta\"."""
    r = _veredicto_ajuste((
        _candidato_ajuste(campos=("order_qty", "cobertura_agregada_meses", "conviene")),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin cuantificar el exceso" in r.evidencia


def test_t06_es_parcial_cuando_solo_falta_la_cobertura():
    r = _veredicto_ajuste((
        _candidato_ajuste(campos=("order_qty", "exceso_unidades", "delta_costo_total_usd",
                                  "conviene")),
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "meses de cobertura que agrega" in r.evidencia


def test_t06_no_ajustar_a_la_restriccion_tampoco_acredita():
    """Un entregable que cuantifica un exceso que nunca aplico no ajusto nada."""
    r = _veredicto_ajuste((
        _candidato_ajuste(ajusta=False, prueba_ajuste="la cantidad no se mueve con MOQ 250"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no se mueve" in r.evidencia


def test_t06_gana_el_que_cuantifica_aunque_otro_tenga_mas_campos():
    sin_cuantificar = _candidato_ajuste(
        tool="mudo", campos=("order_qty", "cobertura_agregada_meses", "conviene")
    )
    cuantifica = _candidato_ajuste(tool="cuantifica", campos=("order_qty", "exceso_unidades"))
    r = _veredicto_ajuste((sin_cuantificar, cuantifica))
    assert r.detalle["mejor"] == "cuantifica"
    assert r.estado is Estado.NO_CUMPLE


# --- T14: acta de S&OP -------------------------------------------------------


def _candidato_acta(**cambios) -> _CandidatoActa:
    """Un acta que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "tool": "acta_ideal",
        "entregable": "jobs.sop_job.ActaSOP",
        "muestra": _muestra(
            familia=("bebidas", "snacks"),
            demanda_acordada=(5_200.0, 26_000.0),
            purchase_plan=(4_800.0, 25_100.0),
            brechas_abiertas=("ventas pidio +20% sin cobertura", "2 SKU sin segundo proveedor"),
            owner=("M. Rossi", "J. Paz"),
            due_date=("2026-08-31", "2026-09-30"),
        ),
        "registra_desacuerdo": True,
        "prueba_desacuerdo": "ventas pidio +20% y quedo asentado como no aprobado",
    }
    return _CandidatoActa(**{**base, **cambios})


def test_t14_cumple_el_acta_dejo_de_estar_partida_en_dos_entregables():
    """Veredicto real, y cambio el dia que se recupero #220.

    El acta traia el conflicto -- cuanto cede cada sombrero respecto de su ideal --
    y le faltaba el cierre. Ahora lleva la demanda acordada, el suministro que la
    soporta, las brechas sin resolver como ``UnresolvedGap``, y una decision con
    nombre y fecha.

    El plan integrado sigue sin poder ganar el desempate: ``registra_desacuerdo``
    es False para el, y esa es una limitacion del arnes que se deja anotada -- sus
    checks son incoherencias tecnicas del plan, no posiciones de areas. Cualquier
    trabajo sobre ``src/sop_engine/`` sigue valiendo cero en T14.
    """
    r = _resultado(puntuar(), "T14")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["mejor"] == "hat_settlement"
    por_tool = {c["tool"]: c for c in r.detalle["candidatos"]}
    assert por_tool["integrated_plan"]["registra_desacuerdo"] is False
    assert por_tool["hat_settlement"]["registra_desacuerdo"] is True
    assert por_tool["hat_settlement"]["faltantes"] == []


def test_t14_cumple_con_plan_brechas_decision_y_desacuerdo_registrado():
    r = _veredicto_acta((_candidato_acta(),))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t14_esconder_el_desacuerdo_es_eliminatorio():
    """La fuente: debe registrar el desacuerdo en vez de esconderlo."""
    r = _veredicto_acta((_candidato_acta(
        registra_desacuerdo=False, prueba_desacuerdo="solo queda el numero final"
    ),))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "registra el desacuerdo" in r.evidencia


def test_t14_una_decision_sin_fecha_no_cierra_la_reunion():
    r = _veredicto_acta((_candidato_acta(muestra=_muestra(
        familia=("bebidas", "snacks"), demanda_acordada=(5_200.0, 26_000.0),
        purchase_plan=(4_800.0, 25_100.0),
        brechas_abiertas=("ventas pidio +20%", "2 SKU sin segundo proveedor"),
        owner=("M. Rossi", "J. Paz"),
    )),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "una decision con nombre y fecha" in r.evidencia


def test_t14_un_responsable_y_una_fecha_vacios_no_cierran_la_reunion():
    """El ataque, en la unidad: cuatro campos con el nombre correcto y nada adentro."""
    r = _veredicto_acta((_candidato_acta(muestra=_muestra(
        familia=("bebidas", "snacks"), demanda_acordada=(5_200.0, 26_000.0),
        purchase_plan=(4_800.0, 25_100.0), brechas_abiertas=("", ""),
        owner=("", ""), due_date=("", ""),
    )),))
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    solo_nombre = r.detalle["candidatos"][0]["campos_solo_de_nombre"]
    assert {"owner", "due_date", "brechas_abiertas"} <= set(solo_nombre)


def test_t14_una_demanda_que_no_se_entera_de_que_la_demanda_cambio_no_es_el_numero_acordado():
    """Dos corridas con demanda muy distinta y un solo numero: no lo esta llevando."""
    r = _veredicto_acta((_candidato_acta(muestra=_muestra(
        familia=("bebidas", "snacks"), demanda_acordada=5_200.0,
        purchase_plan=(4_800.0, 25_100.0),
        brechas_abiertas=("ventas pidio +20%", "2 SKU sin segundo proveedor"),
        owner=("M. Rossi", "J. Paz"), due_date=("2026-08-31", "2026-09-30"),
    )),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "numero de demanda acordado" in r.evidencia


# --- T20: flujo de devoluciones ----------------------------------------------


def _candidato_devoluciones(**cambios) -> _CandidatoDevoluciones:
    """Un plan de devoluciones que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.devoluciones_job.PlanDevoluciones",
        "campos": ("product_id", "reason", "action", "monto_reclamable_proveedor"),
        "disposicion_por_motivo": {
            "falla de producto": "reclamar al proveedor",
            "error de despacho": "restock",
        },
        "tasa_sobre_ventas": True,
        "prueba_motivos": "la disposicion cambia segun el motivo",
    }
    return _CandidatoDevoluciones(**{**base, **cambios})


def test_t20_cumple_distingue_el_motivo_y_mide_la_tasa_sobre_ventas():
    """Veredicto real: la falla de producto ya no vuelve a stock (va a refurbish) y
    arrastra su base reclamable al proveedor, y desde que ``returns_job.run`` acepta
    ``units_sold`` la tasa se mide contra las ventas del periodo. El check exige el 1%
    real, no el 50% que daria la participacion sobre las devoluciones."""
    r = _resultado(puntuar(), "T20")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["distingue_motivos"] is True
    assert r.detalle["disposicion_por_motivo"] == {
        "falla de producto": "refurbish",
        "error de despacho": "restock",
    }
    assert r.detalle["faltantes"] == []


def test_t20_cumple_con_tasa_por_motivo_destino_y_reclamo():
    r = _veredicto_devoluciones(_candidato_devoluciones())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t20_no_distinguir_el_motivo_es_eliminatorio():
    """La fuente: la accion correctiva de una falla no es la de un error de despacho."""
    r = _veredicto_devoluciones(_candidato_devoluciones(disposicion_por_motivo={
        "falla de producto": "restock", "error de despacho": "restock",
    }))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "el motivo no toca la decision" in r.evidencia


def test_t20_una_participacion_sobre_devoluciones_no_es_una_tasa():
    """Sin ventas del periodo no hay denominador: el pareto no es una tasa."""
    r = _veredicto_devoluciones(_candidato_devoluciones(tasa_sobre_ventas=False))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "tasa de devolucion (%) por motivo" in r.evidencia


# --- T13: exposicion a disrupcion --------------------------------------------


def _candidato_exposicion(**cambios) -> _CandidatoExposicion:
    """Un ranking de exposicion que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.riesgo_job.LineaExposicion",
        "muestra": _muestra(
            supplier=("ACME", "BOREAL"),
            exposure_score=(0.81, 0.42),
            mitigation=("calificar segundo proveedor", "subir cobertura 4 semanas"),
            # Se mueve con la urgencia: el riesgo inminente vence antes.
            due_date=("2026-08-10", "2026-11-30"),
        ),
        "ranking_por_puntaje": True,
        "prueba_ranking": "EMV desc",
    }
    return _CandidatoExposicion(**{**base, **cambios})


def test_t13_cumple_la_accion_lleva_fecha_derivada_de_la_urgencia():
    """Veredicto real, y cambio el dia que se recupero #219.

    Antes faltaba el "cuando": ninguna capa tenia fecha. Ahora
    ``warning_window_days`` la deriva de la urgencia del propio riesgo
    (``velocity_days - detectability_days``) y ``RankedMitigation`` la lleva.

    La sonda del gate no se conforma con que el campo exista: corre el registro
    dos veces, con el mismo riesgo remoto y vuelto inminente, y exige que la
    fecha SE MUEVA. Un ``due_date`` declarado y vacio no pasa -- eso lo fija
    ``test_declarar_el_campo_vacio_no_mueve_la_tarea[T13]``.
    """
    r = _resultado(puntuar(), "T13")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["anclado_y_accionable"] is True
    assert r.detalle["faltantes"] == []


def test_t13_cumple_con_ranking_ancla_accion_y_fecha():
    r = _veredicto_exposicion(_candidato_exposicion())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t13_riesgos_genericos_sin_ancla_no_cuentan():
    """La fuente: 'Una lista de riesgos genericos sin SKU ni accion NO cuenta'."""
    r = _veredicto_exposicion(_candidato_exposicion(muestra=_muestra(
        name=("corte de ruta", "quiebre del proveedor unico"), category=("logistics", "supplier"),
        exposure_score=(0.81, 0.42), mitigation=("segundo transportista", "segundo proveedor"),
        due_date=("2026-08-10", "2026-11-30"),
    )))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin proveedor/SKU concreto" in r.evidencia


def test_t13_una_fecha_vacia_no_fecha_la_accion():
    """El ataque, en la unidad: ``due_date`` declarado y sin un cuando adentro."""
    r = _veredicto_exposicion(_candidato_exposicion(muestra=_muestra(
        supplier=("ACME", "BOREAL"), exposure_score=(0.81, 0.42),
        mitigation=("calificar segundo proveedor", "subir cobertura"), due_date=("", ""),
    )))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "accion fechada" in r.evidencia
    assert "due_date" in r.detalle["campos_solo_de_nombre"]


def test_t13_una_fecha_constante_no_se_mueve_con_la_urgencia():
    """Un riesgo remoto y el mismo riesgo inminente no pueden vencer el mismo dia:
    eso es un SLA estampado, no una accion comprometida."""
    r = _veredicto_exposicion(_candidato_exposicion(muestra=_muestra(
        supplier=("ACME", "BOREAL"), exposure_score=(0.81, 0.42),
        mitigation=("calificar segundo proveedor", "subir cobertura"), due_date="2026-12-31",
    )))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "accion fechada" in r.evidencia


def test_t13_un_orden_sin_puntaje_explicito_no_es_ranking_por_exposicion():
    r = _veredicto_exposicion(_candidato_exposicion(
        ranking_por_puntaje=False, prueba_ranking="sale en el orden del archivo de entrada",
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "criterio de puntaje explicito" in r.evidencia


# --- T12: markdown con unidades y margen -------------------------------------


def _candidato_markdown(**cambios) -> _CandidatoMarkdown:
    """Un plan de markdown que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.markdown_job.LineaMarkdown",
        "campos": ("product_id", "precio_propuesto", "descuento_pct", "units_to_clear",
                   "margen_bruto_usd", "cobertura_liberada_meses"),
        "metodos": {"SKU-EXC": "elasticity"},
        "precios_propuestos": {"SKU-EXC": 12.68},
        "prueba_degradacion": "sin historial degrada a remate",
    }
    return _CandidatoMarkdown(**{**base, **cambios})


def test_t12_cumple_los_dos_skus_proponen_precio_cada_uno_con_su_metodo():
    """Veredicto real hoy. Este test antes fijaba PARCIAL, con el argumento de que
    "una recuperacion de salvage no tiene precio unitario que proponer". Eso era
    falso: el remate SIEMPRE supo cuanto recuperaba por unidad
    (``salvage_recovery_pct * unit_cost``); lo que no hacia era escribirlo. Ahora el
    SKU muerto sale con precio anclado al costo y rotulado ``salvage_heuristic``, y
    el SKU con historial con precio resuelto sobre la curva: dos precios, dos
    metodos declarados, ningun numero inventado. Ver
    tests/test_liquidation_salvage_price.py para el invariante legal que sostiene la
    diferencia entre los dos."""
    r = _resultado(puntuar(), "T12")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["declara_el_supuesto"] is True
    assert r.detalle["metodos_declarados"]["SKU-EXC"] == "elasticity"
    assert r.detalle["metodos_declarados"]["SKU-MUERTO"] == "salvage_heuristic"
    # 0.30 (salvage_recovery_pct por defecto) * 12.5 (unit_cost del SKU muerto).
    assert r.detalle["precios_propuestos"]["SKU-MUERTO"] == pytest.approx(3.75)
    assert r.detalle["faltantes"] == []


def test_t12_cumple_con_precio_descuento_unidades_margen_y_cobertura():
    r = _veredicto_markdown(_candidato_markdown())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t12_sin_declarar_el_supuesto_de_demanda_es_eliminatorio():
    """La fuente: 'Debe declarar el supuesto de respuesta de demanda usado'."""
    r = _veredicto_markdown(_candidato_markdown(metodos={"SKU-EXC": ""}))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no declara con que supuesto" in r.evidencia


def test_t12_un_sku_sin_precio_propuesto_no_acredita_la_linea():
    """Un remate sin precio no es 'precio propuesto por SKU'."""
    r = _veredicto_markdown(_candidato_markdown(
        precios_propuestos={"SKU-EXC": 12.68, "SKU-MUERTO": None}
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "precio propuesto por SKU" in r.evidencia


# --- T10: servicio realizado al cliente --------------------------------------


def _candidato_servicio(**cambios) -> _CandidatoServicio:
    """Un entregable de servicio que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.servicio_job.LineaServicio",
        "muestra": _muestra(
            sku=("SKU-1", "SKU-2"),
            otif_salida=(0.60, 0.99),
            fill_rate_linea=(0.60, 0.99),
            dias_quiebre=(4.0, 1.0),
            venta_perdida_usd=(1_200.0, 340.0),
        ),
        # El numero reportado reproduce el servicio que de verdad se observo.
        "reporta_lo_medido": ("fill_rate_linea", "otif_salida"),
        # La beta teorica SIGUE EXISTIENDO en la libreria y ya no descalifica: ver
        # test_t10_que_la_libreria_sepa_el_beta_teorico_ya_no_descalifica.
        "fill_rate_sin_pedidos": 0.9959,
        "prueba_origen": "el fill rate se calcula sobre lineas de pedido despachadas",
    }
    return _CandidatoServicio(**{**base, **cambios})


def test_t10_es_parcial_ya_reporta_el_hecho_pero_no_los_dias_de_quiebre():
    """Veredicto real, despues de que el job pasara a reportar el servicio observado.

    Se le inyecta al job el servicio realmente observado (60% y 99%) y ahora un
    campo del entregable lo REPRODUCE: ``realized_service.fill_rate``, con la
    procedencia pegada al lado. Lo eliminatorio -- reportar el objetivo del modelo
    en lugar del realizado -- ya no aplica.

    Queda un solo faltante, y es un limite de la sonda, no del entregable: los
    dias a stock cero no se derivan de un beta escalar (un quiebre largo y muchos
    cortos dan el mismo numero), y ``observed_fill_rates`` es el unico canal por
    el que la comprobacion inyecta hechos del cliente. El camino queda cableado
    para el dia en que se le pasen lineas de pedido con fecha: ahi
    ``days_out_of_stock`` sale medido. El limite esta admitido en el propio
    docstring de la comprobacion.
    """
    r = _resultado(puntuar(), "T10")
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert r.detalle["mide_lo_realizado"] is True
    assert r.detalle["campos_que_reproducen_lo_medido"] == ["realized_service.fill_rate"]
    assert r.detalle["faltantes"] == ["dias con quiebre por SKU"]


def test_t10_cumple_con_servicio_medido_quiebres_y_venta_perdida():
    r = _veredicto_servicio(_candidato_servicio())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t10_que_la_libreria_sepa_el_beta_teorico_ya_no_descalifica():
    """Correccion del arnes, no ablandamiento.

    El gate viejo era ``fill_rate_sin_pedidos <= 0.0`` y ese numero salia de
    ``fill_rate_from_safety_stock``, una funcion PURA que no lee nada del
    entregable: T10 era imposible aunque se construyera el OTIF de salida
    perfecto, y la unica forma de pasarla era BORRAR ``src/fill_rate.py``. La
    fuente no dice eso: dice que reportar el objetivo EN LUGAR del realizado no
    cuenta. Un entregable que reporta lo medido cumple, tenga la libreria el
    beta teorico que tenga.
    """
    r = _veredicto_servicio(_candidato_servicio(fill_rate_sin_pedidos=0.9959))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.detalle["fill_rate_sin_pedidos"] > 0.99


def test_t10_reportar_el_objetivo_del_modelo_sigue_siendo_eliminatorio():
    """La fuente: reportar el nivel de servicio objetivo del modelo no cuenta.
    Un numero que se mueve con lo observado pero no lo iguala es el objetivo."""
    r = _veredicto_servicio(_candidato_servicio(
        muestra=_muestra(
            sku=("SKU-1", "SKU-2"), service_level=(0.985, 0.946),
            dias_quiebre=(4.0, 1.0), venta_perdida_usd=(1_200.0, 340.0),
        ),
        reporta_lo_medido=(),
        prueba_origen="el numero se mueve con lo observado pero no lo reproduce",
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "el del modelo, no el realizado" in r.evidencia


def test_t10_es_parcial_cuando_solo_falta_la_venta_perdida():
    r = _veredicto_servicio(_candidato_servicio(muestra=_muestra(
        sku=("SKU-1", "SKU-2"), otif_salida=(0.60, 0.99),
        fill_rate_linea=(0.60, 0.99), dias_quiebre=(4.0, 1.0),
    )))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "venta perdida estimada" in r.evidencia


def test_t10_una_venta_perdida_en_cero_no_es_una_venta_perdida_estimada():
    """Mismo criterio que ``recovery._unquantified``: lo que nadie cuantifico no
    puede pasar por un cero medido."""
    r = _veredicto_servicio(_candidato_servicio(muestra=_muestra(
        sku=("SKU-1", "SKU-2"), otif_salida=(0.60, 0.99),
        fill_rate_linea=(0.60, 0.99), dias_quiebre=(4.0, 1.0), venta_perdida_usd=0.0,
    )))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "venta perdida estimada" in r.evidencia


# --- T09: scorecard de proveedores -------------------------------------------


def _candidato_scorecard(**cambios) -> _CandidatoScorecard:
    """Un panel que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.panel_job.PanelProveedores",
        "campos": ("supplier", "otif", "in_full_rate", "ppm", "price_variance_pct",
                   "marcado_para_accion"),
        "metricas": {
            "ACME": {"otif": 0.0, "fill_rate": 0.5, "rechazo": 5000.0},
            "BETA": {"otif": 1.0, "fill_rate": 1.0, "rechazo": 0.0},
        },
        "ranking": ("BETA", "ACME"),
        "trazable_a_las_oc": True,
        "prueba_trazabilidad": "cada nota lista las OC que la generaron",
    }
    return _CandidatoScorecard(**{**base, **cambios})


def test_t09_cumple_es_trazable_marca_a_los_peores_y_compara_el_precio():
    """Veredicto real: las OC que generaron cada nota viajan con el scorecard, el fondo
    del ranking queda marcado para la revision, y desde que ``sourcing_job`` lee la
    columna de precio facturado el panel trae el desvio contra el precio de la OC."""
    r = _resultado(puntuar(), "T09")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["trazable_a_las_oc"] is True
    assert r.detalle["ranking"] == ["BETA", "ACME"]
    assert r.detalle["metricas"]["ACME"]["otif"] == 0.0
    assert r.detalle["faltantes"] == []


def test_t09_cumple_con_las_cuatro_metricas_ranking_y_trazabilidad():
    r = _veredicto_scorecard(_candidato_scorecard())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t09_sin_trazabilidad_es_eliminatorio_aunque_la_tabla_este_completa():
    """La fuente: 'Debe ser trazable a las OC concretas que generaron cada nota'."""
    r = _veredicto_scorecard(_candidato_scorecard(
        trazable_a_las_oc=False, prueba_trazabilidad="el id de OC no sobrevive al reporte"
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no son trazables" in r.evidencia


def test_t09_una_metrica_no_medida_en_todo_el_panel_no_acredita():
    """Un OTIF ``None`` es 'no lo medimos', no un 0%: no puede contar como medido."""
    r = _veredicto_scorecard(_candidato_scorecard(metricas={
        "ACME": {"otif": None, "fill_rate": 0.5, "rechazo": 5000.0},
        "BETA": {"otif": 1.0, "fill_rate": 1.0, "rechazo": 0.0},
    }))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "OTIF % por proveedor" in r.evidencia


# --- T08: lead time real por proveedor ---------------------------------------


def _candidato_lt(**cambios) -> _CandidatoLeadTime:
    """Un scorecard que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.lt_job.LeadTimePorProveedor",
        "campos": ("supplier", "avg_lead_time", "lead_time_p90", "gap_vs_lt_sistema",
                   "skus_a_recalcular_rop"),
        "lt_medido": {"ACME": 22.0, "BETA": 7.5},
        "lt_con_columna_declarada": {"ACME": 22.0, "BETA": 7.5},
    }
    return _CandidatoLeadTime(**{**base, **cambios})


def test_t08_cumple_el_lt_medido_gana_y_nombra_los_sku_a_recalcular():
    """Veredicto real: la columna de LT declarado no pisa la medicion (ACME sigue en
    22 d con y sin ella), el scorecard trae desvio, P90 y la brecha contra lo
    declarado, y desde que ``sourcing_job`` lee la columna de producto tambien
    nombra los SKU cuyo SS/ROP quedo dimensionado sobre el LT que las entregas
    desmienten."""
    r = _resultado(puntuar(), "T08")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["lt_medido_de_las_fechas"]["ACME"] == 22.0
    assert r.detalle["lt_con_columna_declarada"]["ACME"] == 22.0
    assert r.detalle["prevalece_lo_medido"] is True
    assert r.detalle["faltantes"] == []


def test_t08_cumple_con_desvio_gap_y_lista_de_recalculo():
    r = _veredicto_lead_time(_candidato_lt())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t08_reportar_el_lt_declarado_es_eliminatorio():
    """La fuente: 'Reportar solo el LT nominal que dice el proveedor NO cuenta'."""
    r = _veredicto_lead_time(_candidato_lt(
        lt_medido={"ACME": 7.0, "BETA": 7.0}, lt_con_columna_declarada={"ACME": 7.0, "BETA": 7.0}
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no mide el lead time" in r.evidencia


def test_t08_es_parcial_cuando_solo_falta_el_desvio():
    r = _veredicto_lead_time(_candidato_lt(
        campos=("supplier", "avg_lead_time", "gap_vs_lt_sistema", "skus_a_recalcular_rop")
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "desvio o P90 del LT" in r.evidencia


# --- T07: calendario de caja contra el tope ----------------------------------


def _candidato_caja(**cambios) -> _CandidatoCaja:
    """Un plan de caja que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.caja_job.LineaEgreso",
        "muestra": _muestra(
            semana_de_pago=("2026-W33", "2026-W35"),
            egreso_usd=(1_000.0, 860.0),
            tope_usd=1_000.0,
            deficit_usd=860.0,
            # Sin recorte no hay impacto; con recorte lo hay. Por eso se mueve.
            unidades_en_riesgo=(0.0, 145.0),
        ),
        "pedido_usd": 1860.0,
        "tope_usd": 1000.0,
        "prioriza": True,
        "prueba_recorte": "se difieren primero las lineas de menor venta en riesgo",
    }
    return _CandidatoCaja(**{**base, **cambios})


def test_t07_cumple_el_plan_de_compra_ya_dice_cuando_sale_la_plata():
    """Veredicto real, despues de que el plan de compra ganara el eje temporal.

    Lo que antes reprobaba el eliminatorio -- un total de 1.860 usd sin ninguna
    fecha de pago -- hoy sale con el plazo del proveedor por linea (declarado o
    supuesto, y rotulado como cual), la semana en que vence, y un recorte que
    elige que linea difiere en vez de raspar a todas la misma fraccion.
    """
    r = _resultado(puntuar(), "T07")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0
    assert r.detalle["faltantes"] == []
    assert r.detalle["prioriza_el_recorte"] is True
    assert r.detalle["deficit_usd"] == 860.0  # la sonda no cambio: 1.860 pedidos vs 1.000 de tope
    # El eje temporal viaja en la linea, no en un nombre suelto del reporte.
    assert {"payment_terms", "payment_due_week"} <= set(r.detalle["campos"])
    assert r.detalle["campos_solo_de_nombre"] == []


def test_t07_cumple_con_calendario_tope_prioridad_e_impacto():
    r = _veredicto_caja(_candidato_caja())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t07_sin_fecha_de_pago_es_eliminatorio_aunque_compare_contra_el_tope():
    """La fuente: 'Un total de compra sin fecha de pago NO cuenta'."""
    r = _veredicto_caja(_candidato_caja(muestra=_muestra(
        egreso_usd=(1_000.0, 860.0), tope_usd=1_000.0, unidades_en_riesgo=(0.0, 145.0),
    )))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin ninguna fecha de pago" in r.evidencia


def test_t07_una_fecha_de_pago_vacia_es_lo_mismo_que_no_tenerla():
    """El ataque, en la unidad: el total sigue sin fecha aunque el campo exista."""
    r = _veredicto_caja(_candidato_caja(muestra=_muestra(
        payment_date=("", ""), egreso_usd=(1_000.0, 860.0), tope_usd=1_000.0,
        unidades_en_riesgo=(0.0, 145.0),
    )))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin ninguna fecha de pago" in r.evidencia
    assert "payment_date" in r.detalle["campos_solo_de_nombre"]


def test_t07_un_impacto_en_quiebre_que_no_cambia_con_el_recorte_no_mide_el_recorte():
    """Con tope y sin tope no puede salir el mismo numero: no esta midiendo nada."""
    r = _veredicto_caja(_candidato_caja(muestra=_muestra(
        semana_de_pago=("2026-W33", "2026-W35"), egreso_usd=(1_000.0, 860.0),
        tope_usd=1_000.0, unidades_en_riesgo=0.0,
    )))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert "impacto en quiebre de cada recorte" in r.evidencia


def test_t07_recorte_parejo_no_es_lista_priorizada():
    """Raspar el buffer de todos por igual no es elegir que se difiere."""
    r = _veredicto_caja(_candidato_caja(
        prioriza=False, prueba_recorte="todos ceden la misma fraccion de su buffer"
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "lista ordenada de lineas a recortar" in r.evidencia


def test_t05_gana_el_que_netea_aunque_otro_tenga_mas_campos():
    """Un entregable mas completo que sobre-compra no puede tapar al que sí netea."""
    completo_sin_neteo = _candidato(
        tool="completo", descuenta_en_transito=False, prueba_en_transito="ignora las OC abiertas"
    )
    netea_incompleto = _candidato(tool="netea", muestra=_muestra(
        sku=("SKU-1", "SKU-2"), order_qty=(120.0, 80.0),
        reason=("bajo ROP", "quiebre"), required_date=("2026-08-14", "2026-08-28"),
    ))
    r = _veredicto_oc((completo_sin_neteo, netea_incompleto))
    assert r.detalle["mejor"] == "netea"
    assert r.estado is Estado.PARCIAL
