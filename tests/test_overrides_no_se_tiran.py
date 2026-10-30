"""Un hecho del cliente que el agente acepta y tira en silencio es peor que uno que rechaza.

El defecto que lo abrio, medido punta a punta antes de arreglarlo:
`jobs/inventory_optimization.run` acepta `lead_time_stds` -- la dispersion del plazo del
proveedor -- desde que existe la columna `lead_time_std` del CSV, y
`scm_agent/tools.py::_inventory_run` **nunca la reenviaba**. Un cliente que la declaraba
no recibia ni un error ni un aviso: recibia una politica dimensionada como si el plazo
no tuviera dispersion NINGUNA, con el entregable diciendo igual "95% service level".

Lo que cuesta no es cosmetico. El periodo de riesgo es
``sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)`` (Vandeput ec. 6.5), y el
termino que se perdia es el DOMINANTE cuando la demanda es grande frente a su propia
dispersion -- que es el caso normal de un SKU que vende todas las semanas. Medido sobre
la misma serie: llamando al job, `lead_time_stds={"A": 3.0}` mueve el buffer de 11,9 a
238,4 unidades; pasando por el orquestador quedaba en 11,9.

Se encontro midiendo el residuo de la brecha del marcador
(`documentation/KERN_RESIDUO_DE_LA_BRECHA.md`): un carril que le pasaba a Kern la
dispersion observada de sus propias recepciones salia IDENTICO al carril de control, lo
que no podia ser cierto.

**Y no era uno solo.** Al generalizar el guard a los 60 tools aparecieron once drops mas
que eran hechos del cliente de verdad, cada uno reproducido en las DOS direcciones
(que no llega por el agente, y que si llegara mueve un campo del reporte). El mas caro
es el mapeo explicito de columnas: `intake` lo acepta con la precedencia mas alta -- "mi
columna `bultos_despachados` ES la cantidad" -- y era la unica salida de un archivo que
la deteccion no entiende. Sin el, esa corrida salia `needs_data` y no habia forma de
contestarle desde afuera.

Tres clases de test, que son tres afirmaciones distintas:
- **estructural**: recorre por AST toda llamada a `jobs.*` dentro de `scm_agent/tools.py`
  y se pone rojo si aparece un parametro que el agente no reenvia ni declara por escrito
  por que no. Mismo patron que `tests/test_barrido_neteo.py`: la lista no se mantiene a
  mano, se deriva, y agregar un parametro obliga a decidir.
- **de comportamiento**: que el dato viaje de verdad y mueva el numero que tiene que
  mover, por el orquestador entero.
- **de la regla de la rama**: una dispersion declarada en CERO no es lo mismo que
  ninguna, y lo que las separa es `lead_time_std_source`.
"""

from __future__ import annotations

import ast
import inspect
import tempfile
import types
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pytest

from scm_agent import tools
from scm_agent.orchestrator import Orchestrator

#: Parametros que el agente NO expone a proposito, por NOMBRE, con su razon. Son los
#: genericos: aparecen en decenas de constructores de deck y siempre significan lo
#: mismo, asi que exigir una entrada por destino convertiria la lista en ruido y nadie
#: la leeria. Un deck nuevo hereda la razon; un parametro con nombre NUEVO no.
NO_EXPUESTOS_POR_NOMBRE = {
    "prepared": (
        "el resumen de `prepare` que el deck imprime arriba. Lo arma el propio tool "
        "con lo que preparo; no es un hecho del cliente ni algo que se pueda pedir"
    ),
    "lang": (
        "el idioma del deck. Es una opcion de renderizado, y hoy la eligen los tools "
        "que la tienen segun su publico; exponerla es trabajo de i18n, no de datos"
    ),
    "data_source": (
        "la etiqueta de procedencia que el deck imprime ('demand history (intake)'). "
        "La sabe el tool porque es quien leyo el archivo, no el que lo pide"
    ),
    "residual": (
        "texto de residual que el deck muestra. Lo produce la capa de OPTIONS "
        "(`nosilo_core.guided`), no el llamador"
    ),
}

#: Y los especificos, por `modulo.funcion.parametro`. Cada uno tiene su razon porque
#: cada uno la tiene DISTINTA -- y esa es la diferencia entre una decision y un olvido.
NO_EXPUESTOS_POR_DESTINO = {
    "jobs.inventory_optimization.run.min_periods": (
        "es la compuerta de historia minima. Bajarla no es un arreglo -- medido, la "
        "corrida sale `qa_failed` y la politica vuelve vacia igual "
        "(documentation/KERN_POR_QUE_BAJO_EL_PISO.md, E2a) -- y exponerla dejaria que "
        "quien la baja se saltee la negativa del motor desde afuera"
    ),
    "jobs.inventory_optimization.run.oos_origins": (
        "es el presupuesto de origenes del backtest del pronostico, una perilla de "
        "costo/precision del motor. No es un hecho del cliente ni cambia lo que se le "
        "promete"
    ),
    "jobs.forecast_job.run.aic_tolerance": (
        "es la compuerta de evidencia del modelo causal: `fit_causal_forecast` adopta "
        "las covariables solo si el AIC mejora al menos esto, y si no se niega a usarlas "
        "para no sobreajustar. No es un hecho del cliente, y exponerla dejaria que quien "
        "la baja -- o la pone negativa -- haga adoptar covariables que no pagan sus "
        "parametros: la negativa del motor salteada desde afuera, igual que "
        "`inventory_optimization.run.min_periods`. Y el deck imprime el umbral como "
        "literal ('<= -2.0' en `build_deck`): con la perilla expuesta, el entregable "
        "diria un umbral y gatearia con otro"
    ),
    "jobs.sop_job.run.confidence": (
        "la confianza declarada del plan S&OP. Una confianza que el llamador TIPEA no "
        "es una medicion, es un numero inventado con nombre de medicion -- exactamente "
        "lo que esta rama persigue. Que hoy este fija en 0,8 dentro del job es un "
        "problema aparte, y se arregla midiendola, no dejando que la pidan"
    ),
    "jobs.repricing_job.run.kb": (
        "la base de conocimiento que el tool inyecta para citar; es plomeria del "
        "agente, no un dato del cliente"
    ),
    "jobs.forecast_job.run.cycle_id": (
        "identidad del ciclo de memoria, que se deriva de `params['state_client_id']` "
        "-- ver el comentario en el call site. Dejarla pasar suelta permitiria escribir "
        "en la particion de otro cliente"
    ),
    "jobs.state_bridge.record_stock_snapshot.cycle_id": (
        "igual que arriba: el puente al Tower deriva la identidad, no la recibe"
    ),
    "jobs.state_bridge.record_outcomes.cycle_id": (
        "igual que arriba: el puente al Tower deriva la identidad, no la recibe"
    ),
    "jobs.state_bridge.stock_lines_from_inventory.periods_per_year": (
        "sale de `report.params['periods_per_year']` del propio reporte, que es el que "
        "de verdad se uso. Recibirlo aparte permitiria convertir la tasa diaria con un "
        "periodo distinto del que dimensiono la politica"
    ),
    # Las tres que siguen no se reenvian porque el hecho YA llega por otro camino. Esa
    # afirmacion la sostienen los tests de "Los que se declaran porque YA viajan por
    # otro camino", al final de este archivo: si el camino se corta, se ponen rojos
    # ellos, y no este dict.
    "jobs.forecast_job.run.covariates_by_sku": (
        "la historia de covariables YA viaja, dentro del payload: `forecast_job.prepare` "
        "la lee del MISMO archivo que la demanda -- mismo SKU, mismo orden por periodo -- "
        "y la cuelga del `ForecastPayload` que el orquestador le pasa entero a `run`. El "
        "kwarg es la puerta de quien llama al job desde Python con series sueltas. "
        "Reenviarlo desde `params` abriria una segunda historia, tipeada por el llamador, "
        "cuya alineacion con la serie nadie controla, y que le GANARIA a la del archivo: "
        "`run` prefiere el kwarg al payload"
    ),
    "jobs.forecast_job.run.future_covariates_by_sku": (
        "el valor futuro de cada covariable YA tiene canal: `params['future_covariates']` "
        "(global o por SKU), que `prepare_records` lee y cuelga del mismo payload. "
        "Reenviar el kwarg seria el mismo hecho del cliente por un segundo camino con "
        "otro nombre, y como `run` prefiere el kwarg al payload, la segunda copia "
        "pisaria a la primera sin que el entregable lo diga"
    ),
    "jobs.vehicle_routing_job.prepare.distance_matrix": (
        "la matriz YA viaja, por `params`: `_vehicle_routing_prepare` le pasa "
        "`request.params` entero a `prepare`, y `prepare_records` lee "
        "`params['distance_matrix']` (y sus alias `matrix`, `matrix_path` y "
        "`distance_matrix_path`). El kwarg es la puerta de quien llama desde Python y "
        "escribe en esa MISMA clave: reenviarlo mandaria la misma matriz dos veces"
    ),
}


def _params_por_destino() -> tuple[dict, dict]:
    """``(firma, pasados)`` por destino, leyendo el AST de `scm_agent/tools.py`.

    Se lee del AST y no de una lista escrita al lado, porque una lista escrita al lado
    es exactamente lo que se desincronizo.

    Dos cosas que el recorrido tiene que entender o acusa de mas:

    - **un destino se llama desde VARIOS sitios**, y alcanza con que UNO reenvie el
      parametro (`jobs.risk_job.run` recibe `severity_thresholds` en la rama que las
      tiene y no en la otra). Contar por call site daba cuatro falsos positivos.
    - **un kwarg puede viajar dentro de un `**{...}` condicional**, que es la forma
      correcta cuando el job no acepta `None` y hay que dejar que mande SU default.
      Las claves constantes de esos dicts cuentan como pasadas.
    """
    modulo = inspect.getfile(tools)
    arbol = ast.parse(Path(modulo).read_text(encoding="utf-8"))
    ns = vars(tools)

    def resolver(func):
        if isinstance(func, ast.Name):
            return ns.get(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = ns.get(func.value.id)
            return getattr(base, func.attr, None) if base is not None else None
        return None

    firma_de: dict[str, list[str]] = {}
    pasados: dict[str, set[str]] = defaultdict(set)
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        fn = resolver(nodo.func)
        if not isinstance(fn, types.FunctionType):
            continue
        if not (getattr(fn, "__module__", "") or "").startswith("jobs"):
            continue
        destino = f"{fn.__module__}.{fn.__name__}"
        firma = inspect.signature(fn)
        nombres = [p for p, v in firma.parameters.items()
                   if v.kind not in (inspect.Parameter.VAR_KEYWORD,
                                     inspect.Parameter.VAR_POSITIONAL)]
        firma_de[destino] = nombres
        for kw in nodo.keywords:
            if kw.arg:
                pasados[destino].add(kw.arg)
                continue
            # `**expr`: las claves constantes de cualquier dict literal ahi adentro.
            for hijo in ast.walk(kw.value):
                if isinstance(hijo, ast.Dict):
                    pasados[destino] |= {
                        k.value for k in hijo.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
        pasados[destino] |= set(nombres[:len(nodo.args)])
    return firma_de, pasados


def _sin_reenviar() -> dict[str, str]:
    """``{"modulo.funcion.parametro": parametro}`` de todo lo que no viaja."""
    firma_de, pasados = _params_por_destino()
    fuera = {}
    for destino, nombres in firma_de.items():
        for p in nombres:
            if p not in pasados[destino]:
                fuera[f"{destino}.{p}"] = p
    return fuera


def test_el_agente_reenvia_TODO_parametro_de_TODO_job_o_declara_por_que_no():
    """El guard estructural, sobre los 60 tools y no sobre uno.

    Nacio cubriendo solo `inventory_optimization`, donde se midio el defecto. Al
    generalizarlo aparecieron once drops mas que eran hechos del cliente de verdad --
    entre ellos el mapeo explicito de columnas, que dejaba un `needs_data` sin salida
    para el operador -- y por eso el guard no puede quedarse en un tool.

    Si aparece un parametro nuevo y nadie lo reenvia, este test lo nombra. Si de verdad
    no corresponde exponerlo, se agrega con su razon -- que es el punto: la decision
    queda escrita, no olvidada.
    """
    faltantes = sorted(
        ruta for ruta, nombre in _sin_reenviar().items()
        if nombre not in NO_EXPUESTOS_POR_NOMBRE and ruta not in NO_EXPUESTOS_POR_DESTINO
    )
    assert not faltantes, (
        f"`scm_agent/tools.py` no reenvia {faltantes}. Un hecho del cliente que el "
        "agente acepta y tira en silencio es peor que uno que rechaza: no hay error y "
        "el entregable promete igual. Reenvialo, o agregalo a NO_EXPUESTOS_POR_NOMBRE "
        "/ NO_EXPUESTOS_POR_DESTINO con la razon."
    )


def test_la_lista_de_exenciones_no_tiene_fantasmas():
    """Una razon escrita para algo que ya no existe es peor que ninguna: se lee como si
    alguien hubiera pensado el caso, y el caso no esta."""
    sin_reenviar = _sin_reenviar()
    vivos_por_nombre = set(sin_reenviar.values())
    fantasmas = sorted(set(NO_EXPUESTOS_POR_NOMBRE) - vivos_por_nombre)
    assert not fantasmas, f"NO_EXPUESTOS_POR_NOMBRE nombra parametros que ya se reenvian: {fantasmas}"

    fantasmas = sorted(set(NO_EXPUESTOS_POR_DESTINO) - set(sin_reenviar))
    assert not fantasmas, f"NO_EXPUESTOS_POR_DESTINO nombra destinos inexistentes: {fantasmas}"


def test_toda_exencion_lleva_una_razon_escrita():
    """Un conjunto pelado no puede llevar la razon, y sin razon la lista es el cajon
    donde va a parar lo que nadie quiso decidir -- el mismo argumento por el que
    `NO_EMISORES` del barrido de neteo es un dict."""
    for tabla in (NO_EXPUESTOS_POR_NOMBRE, NO_EXPUESTOS_POR_DESTINO):
        for clave, razon in tabla.items():
            assert isinstance(razon, str) and len(razon) > 40, (
                f"{clave} no tiene una razon escrita, tiene una etiqueta: {razon!r}"
            )


def _serie(tmp: Path) -> str:
    fechas = pd.date_range("2026-01-05", periods=24, freq="W")
    pd.DataFrame([
        {"date": f, "product_id": "A", "quantity": 40.0 + (i % 7) * 3,
         "unit_cost": 20.0, "lead_time_days": 7.0}
        for i, f in enumerate(fechas)
    ]).to_csv(tmp / "v.csv", index=False)
    return str(tmp / "v.csv")


def _por_el_agente(ruta: str, salida: Path, **overrides):
    res = Orchestrator(clients_root=None).run(
        "reorder point y stock de seguridad por SKU", data_path=ruta,
        job_type="inventory_optimization", out_dir=str(salida), overrides=overrides,
    )
    assert res.status == "ok", res.summary
    return res.report.recommendations[0]


def test_la_dispersion_del_plazo_LLEGA_al_motor_por_el_camino_del_agente():
    """El caso de cabecera. Sin esto, el buffer sale como si el proveedor no fallara nunca."""
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        ruta = _serie(raiz)

        base = _por_el_agente(ruta, raiz / "base")
        con = _por_el_agente(ruta, raiz / "con", lead_time_stds={"A": 3.0})

        assert base.lead_time_std == 0.0 and base.lead_time_std_source == "defaulted_zero"
        assert con.lead_time_std == 3.0, "la dispersion declarada no llego al registro"
        assert con.lead_time_std_source == "client_file"
        assert con.safety_stock > base.safety_stock * 5, (
            f"el buffer casi no se movio ({base.safety_stock:.3f} -> {con.safety_stock:.3f}); "
            "el termino sigma_L^2 * mu_d^2 del periodo de riesgo es el dominante aca, "
            "asi que si no se mueve es que el dato sigue sin llegar"
        )
        # Y el disparador se mueve con el, porque es `mu_x + buffer`.
        assert con.reorder_point > base.reorder_point


def test_una_dispersion_declarada_en_CERO_no_es_lo_mismo_que_ninguna():
    """La regla de toda esta rama, en este campo.

    Un proveedor que de verdad nunca falla tiene sigma_L = 0 MEDIDO, y eso es un hecho
    que el entregable puede citar. Que el numero coincida con el default no lo vuelve un
    default: lo que los separa es `lead_time_std_source`, y sin esa distincion el
    reporte no puede decir si el 95% descansa en una medicion o en una suposicion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        ruta = _serie(raiz)

        sin = _por_el_agente(ruta, raiz / "sin")
        cero = _por_el_agente(ruta, raiz / "cero", lead_time_stds={"A": 0.0})

        assert sin.lead_time_std == cero.lead_time_std == 0.0
        assert sin.safety_stock == pytest.approx(cero.safety_stock), (
            "el NUMERO tiene que ser el mismo: cero dispersion es cero dispersion"
        )
        assert sin.lead_time_std_source == "defaulted_zero"
        assert cero.lead_time_std_source == "client_file", (
            "una dispersion medida en cero no puede reportarse como un default; es la "
            "misma regla por la que `skipped_zero_demand` conserva sus ceros"
        )


# ---------------------------------------------------------------------------
# Los once drops que aparecieron al generalizar: cada uno, por el orquestador
# ---------------------------------------------------------------------------

#: tool -> (brief, overrides, campo del reporte que TIENE que moverse).
#: El campo esperado no es decoracion: sin el, un test que solo pida "algo cambio"
#: pasaria verde si el override moviera un timestamp.
LLEGAN_Y_MUEVEN = {
    "abc_xyz": ("classify the portfolio by ABC-XYZ",
                {"service_level_by_class": {"A": 0.99, "B": 0.95, "C": 0.90}},
                "classifications"),
    "returns": ("analyze product returns disposition",
                {"units_sold": {"P1": 1000.0, "P2": 500.0, "P3": 800.0}},
                "return_rate_by_reason"),
    "financial_kpis": ("compute inventory financial KPIs",
                       {"stockout_periods": 3.0, "total_periods": 52.0},
                       "stockout_rate"),
    "cycle_count": ("build a cycle count program",
                    {"counts_per_year": {"A": 24, "B": 6, "C": 2}},
                    "counts_per_year"),
    "risk": ("score and prioritize supply chain risks",
             {"as_of": "2025-06-30"}, "risk_report"),
    "ddmrp": ("size DDMRP buffers and planning signals",
              {"today": "2025-06-30"}, "parts"),
}


@pytest.mark.parametrize("tool", sorted(LLEGAN_Y_MUEVEN))
def test_el_override_llega_y_mueve_el_campo_que_tiene_que_mover(tool, tmp_path):
    """Las dos direcciones en un solo test, que es como se reproduce un drop.

    Que NO llegue se prueba por la diferencia contra la corrida base; que el parametro
    no sea inerte, por el campo nombrado. Un drop solo importa si las dos cosas son
    ciertas: un parametro que no llega pero tampoco cambia nada no vale una linea.
    """
    import dataclasses

    from examples.run_capability_smoke import _CSV, _fixture_path

    brief, overrides, campo = LLEGAN_Y_MUEVEN[tool]
    ruta = _fixture_path(tool, tmp_path)
    if ruta is None:
        ruta = tmp_path / f"{tool}.csv"
        ruta.write_text(_CSV[tool], encoding="utf-8")

    def corrida(etiqueta, ov):
        res = Orchestrator(clients_root=None).run(
            brief, data_path=str(ruta), job_type=tool,
            out_dir=str(tmp_path / tool / etiqueta), overrides=ov,
        )
        assert res.status == "ok", f"{tool} {etiqueta}: {res.status} {res.summary}"
        return dataclasses.asdict(res.report)

    base, con = corrida("base", {}), corrida("con", overrides)
    assert repr(base[campo]) != repr(con[campo]), (
        f"{tool}: el override {sorted(overrides)} no movio `{campo}`; o el agente lo "
        "sigue tirando, o el parametro es inerte y no habia que exponerlo"
    )


def test_el_operador_puede_NOMBRAR_su_columna_cuando_la_deteccion_no_la_encuentra(tmp_path):
    """El drop mas caro de los once, con su callejon sin salida.

    `intake.detect_columns` acepta `overrides` con la PRECEDENCIA MAS ALTA: un override
    que nombra una columna que existe en ESTE archivo le gana a los alias del rubro y a
    los genericos. Es una afirmacion sobre este archivo, no un vocabulario -- y por eso
    es distinto de `column_aliases`, que el agente si exponia.

    Sin el, un export cuya columna de cantidad la tabla de alias no conoce salia
    `needs_data` y NO HABIA forma de contestarle desde afuera: el operador veia "falta
    la columna cantidad" sobre un archivo que la tiene, con otro nombre.
    """
    fechas = pd.date_range("2026-01-05", periods=24, freq="W")
    ruta = tmp_path / "raro.csv"
    pd.DataFrame([
        {"fecha": f, "codigo": "A", "bultos_despachados": 40.0 + (i % 7) * 3,
         "costo_unit": 20.0}
        for i, f in enumerate(fechas)
    ]).to_csv(ruta, index=False)

    def corrida(ov):
        return Orchestrator(clients_root=None).run(
            "reorder point y stock de seguridad por SKU", data_path=str(ruta),
            job_type="inventory_optimization", out_dir=str(tmp_path / "out"), overrides=ov,
        )

    sin = corrida({})
    assert sin.status == "needs_data", (
        "si la deteccion ya resuelve este archivo sola, el test dejo de probar el "
        "callejon sin salida y hay que buscar una columna que de verdad no conozca"
    )

    con = corrida({"column_overrides": {"quantity": "bultos_despachados"}})
    assert con.status == "ok", f"el override de columnas no llego: {con.summary}"
    assert con.report.recommendations, "la corrida salio ok pero sin recomendaciones"


def test_una_fecha_ilegible_LEVANTA_en_vez_de_caer_al_reloj(tmp_path):
    """Un typo en `as_of` no puede convertirse en un reporte fechado hoy que dice estar
    fechado en otro lado. Fechar mal un analisis de envejecimiento es la clase de error
    que nadie encuentra leyendo el entregable."""
    with pytest.raises(ValueError, match="as_of"):
        tools._fecha_param({"as_of": "30-06-2025"}, "as_of")

    # Y las formas legitimas siguen andando, incluida la ausencia.
    from datetime import date, datetime

    assert tools._fecha_param({}, "as_of") is None
    assert tools._fecha_param({"as_of": ""}, "as_of") is None
    assert tools._fecha_param({"as_of": "2025-06-30"}, "as_of") == date(2025, 6, 30)
    assert tools._fecha_param({"as_of": date(2025, 6, 30)}, "as_of") == date(2025, 6, 30)
    assert tools._fecha_param({"as_of": datetime(2025, 6, 30, 11, 0)}, "as_of") == date(2025, 6, 30)


def test_un_costo_unitario_declarado_reemplaza_al_marcador_de_posicion(tmp_path):
    """`default_unit_cost` es el gemelo de `default_lead_days`, que el agente SI pasaba.

    El 1,0 por omision no es un costo: es un marcador de posicion, y como multiplica
    toda cifra de inversion, un archivo sin columna de costo publicaba un presupuesto
    que no significa nada. Declararlo no lo vuelve medido -- `IntakeQuality` lo sigue
    contando como sintetizado -- pero lo vuelve del orden de magnitud correcto.
    """
    fechas = pd.date_range("2026-01-05", periods=24, freq="W")
    ruta = tmp_path / "sin_costo.csv"
    pd.DataFrame([
        {"date": f, "product_id": "A", "quantity": 40.0 + (i % 7) * 3}
        for i, f in enumerate(fechas)
    ]).to_csv(ruta, index=False)

    def corrida(ov):
        res = Orchestrator(clients_root=None).run(
            "reorder point por SKU", data_path=str(ruta),
            job_type="inventory_optimization", out_dir=str(tmp_path / "out"), overrides=ov,
        )
        assert res.status == "ok", res.summary
        return res.report

    base = corrida({})
    con = corrida({"default_unit_cost": 18.0})

    assert base.recommendations[0].unit_cost == pytest.approx(1.0)
    assert con.recommendations[0].unit_cost == pytest.approx(18.0)

    # La inversion sube, y NO en proporcion al costo: el EOQ mete una raiz cuadrada.
    # `Q* ~ 1/sqrt(h)` y `h ~ unit_cost`, asi que el capital de CICLO (`Q/2 * c`) escala
    # con `sqrt(c)`, mientras el de SEGURIDAD (`ss * c`) si escala lineal -- el buffer
    # sale de la dispersion de la demanda, que el costo no toca. El total queda entre
    # las dos cotas, y afirmarlo asi es lo que impide "arreglar" este test poniendo un
    # factor lineal el dia que el numero no de.
    import math

    piso = base.requested_investment * math.sqrt(18.0)
    techo = base.requested_investment * 18.0
    assert piso < con.requested_investment < techo, (
        f"{con.requested_investment:.2f} quedo fuera de ({piso:.2f}, {techo:.2f})"
    )


def test_la_clave_de_idempotencia_del_restock_se_puede_declarar():
    """Que el llamador PUEDA declararla, que es lo que deja replanificar a mano.

    Este test nacio con una segunda afirmacion -- que sin override la clave era la
    constante `"channel-replenish-1"` -- puesta ahi para ponerse ROJA el dia que el
    default se arreglara. Se puso roja el 2026-09-20 y se vino a mirarla, que era el
    punto. El default ahora se DERIVA de lo que la reposicion hace, y su contrato vive
    entero en `tests/test_claves_de_idempotencia.py`.
    """
    from nosilo_core.types import JobRequest
    from scm_agent.tools import build_default_registry
    from src.connectors.odoo import demo_odoo
    from src.connectors.shopify import demo_shopify

    tool = build_default_registry().get("channel_replenishment")

    def clave(extra: dict) -> str | None:
        params = {"shopify_rpc": demo_shopify(), "odoo_rpc": demo_odoo(), **extra}
        prep = tool.prepare(JobRequest(brief="x", data_path=None, params=params), None)
        assert prep.status == "ok", prep.messages
        changeset = tool.run(prep.payload, params).report.changeset
        return getattr(changeset, "idempotency_key", None)

    assert clave({}).startswith("channel-replenish-")
    assert clave({}) != "channel-replenish-1", "volvio la constante"
    assert clave({"idempotency_key": "restock-2026-W38"}) == "restock-2026-W38"


# ---------------------------------------------------------------------------
# Los que se declaran porque YA viajan por otro camino: que ese camino ande
# ---------------------------------------------------------------------------
#
# `forecast_job.run.covariates_by_sku`, `forecast_job.run.future_covariates_by_sku`
# y `vehicle_routing_job.prepare.distance_matrix` estan en NO_EXPUESTOS_POR_DESTINO
# con una razon que es una afirmacion sobre la caneria: "el hecho ya llega, por el
# payload o por `params`". Si ese camino se cortara, el guard estructural seguiria
# verde -- la exencion lo tapa --, asi que la razon la sostienen estos tests y no la
# prosa.


def _demanda_con_promo(tmp: Path, *, con_columna: bool = True) -> str:
    """Doce periodos donde la promo suma 25 unidades sobre una base de ~10."""
    promo = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]
    filas = pd.DataFrame({
        "sku": ["P"] * 12, "period": range(12),
        "demand": [10.0 + 25.0 * p + (i % 3) * 0.4 for i, p in enumerate(promo)],
    })
    if con_columna:
        filas["promo"] = promo
    ruta = tmp / ("con_promo.csv" if con_columna else "sin_promo.csv")
    filas.to_csv(ruta, index=False)
    return str(ruta)


def _pronostico(ruta: str, salida: Path, **overrides):
    res = Orchestrator(clients_root=None).run(
        "forecast demand per SKU", data_path=ruta, job_type="forecast",
        out_dir=str(salida), overrides=overrides,
    )
    assert res.status == "ok", res.summary
    return res.report.skus[0]


def test_la_historia_de_covariables_viaja_DENTRO_del_payload_por_el_camino_del_agente(tmp_path):
    """La razon de `forecast_job.run.covariates_by_sku`, puesta a prueba.

    La promo vive como COLUMNA del mismo archivo que la demanda, y viaja como
    atributo del `ForecastPayload` que `prepare` arma. Un `dict(payload)` en el
    camino alcanzaria para perderla, y entonces `delta_aic` saldria vacio, igual que
    en un archivo sin la columna.
    """
    sin = _pronostico(_demanda_con_promo(tmp_path, con_columna=False), tmp_path / "sin")
    con = _pronostico(_demanda_con_promo(tmp_path), tmp_path / "con")

    assert sin.delta_aic is None and not sin.causal_accepted
    assert con.delta_aic is not None and con.delta_aic <= -2.0, (
        "la historia de la promo no llego al modelo causal por el camino del agente"
    )
    assert con.causal_accepted and con.method == "causal_multivariate_aic"


def test_el_valor_futuro_de_la_covariable_viaja_por_params_y_mueve_el_pronostico(tmp_path):
    """La razon de `forecast_job.run.future_covariates_by_sku`, puesta a prueba.

    "El proximo periodo hay promo" es un hecho del cliente, y su canal es
    `params['future_covariates']`. Las dos formas que `prepare_records` acepta --
    global y por SKU -- tienen que llegar y mover el punto: si no llegaran, las dos
    corridas tomarian el default (sin promo) y saldrian iguales.
    """
    ruta = _demanda_con_promo(tmp_path)
    sin_promo = _pronostico(ruta, tmp_path / "0", future_covariates={"promo": 0.0})
    global_ = _pronostico(ruta, tmp_path / "g", future_covariates={"promo": 1.0})
    por_sku = _pronostico(ruta, tmp_path / "s", future_covariates={"P": {"promo": 1.0}})

    assert global_.forecast == pytest.approx(por_sku.forecast)
    assert global_.forecast - sin_promo.forecast == pytest.approx(25.0, abs=2.0), (
        f"{sin_promo.forecast:.2f} -> {global_.forecast:.2f}: la promo declarada no "
        "movio el pronostico lo que la historia dice que mueve"
    )


def test_la_matriz_de_distancias_viaja_por_params_por_el_camino_del_agente(tmp_path):
    """La razon de `vehicle_routing_job.prepare.distance_matrix`, puesta a prueba.

    La forma es la que puede llegar por `/api/jobs`: JSON no tiene claves-tupla, asi
    que la matriz viaja anidada (`{origen: {destino: distancia}}`). Si se perdiera en
    el camino, el ruteo volveria a la linea recta y solo el residual del deck lo
    diria.
    """
    ruta = tmp_path / "paradas.csv"
    pd.DataFrame({
        "stop_id": ["A", "B", "C"], "x": [3.0, 6.0, 9.0], "y": [4.0, 8.0, 12.0],
        "demand": [1.0, 1.0, 1.0],
    }).to_csv(ruta, index=False)

    def corrida(etiqueta: str, **ov):
        res = Orchestrator(clients_root=None).run(
            "route the delivery vehicles", data_path=str(ruta), job_type="vehicle_routing",
            out_dir=str(tmp_path / etiqueta), overrides={"capacity": 3, **ov},
        )
        assert res.status == "ok", res.summary
        return res.report

    recta = corrida("recta")
    vial = corrida("vial", distance_matrix={
        "depot": {"A": 10.0, "B": 20.0, "C": 30.0},
        "A": {"B": 5.0, "C": 12.0},
        "B": {"C": 5.0},
    })

    assert recta.distance_source == "euclidean"
    assert vial.distance_source == "matrix", "la matriz declarada no llego al ruteo"
    assert vial.recommended_distance != pytest.approx(recta.recommended_distance)
