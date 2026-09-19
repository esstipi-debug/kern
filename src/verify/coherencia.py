"""Invariantes que un lote de recomendaciones no puede violar, a cualquier escala.

**Por que existe este modulo.** La bateria (``src/bateria``) juzga si el entregable
trae el campo que el oficio exige, y lo hace sobre fixtures de dos SKU. La corrida
de portafolio (``deliverables/portfolio``) corre 34.498 SKU y sólo registra si el
job termino: su "status: ok" significa "no lanzo excepcion", nunca "la
recomendacion era coherente". Entre las dos no habia nada, y ese hueco es el que
esconde los errores que sólo aparecen con volumen -- rankings inestables,
agregados que no cuadran con el detalle, buffers negativos en la cola de la
distribucion.

Aqui no se juzga si la recomendacion es *buena*: eso exige un contrafactual y lo
mide ``src/simworld``. Se juzga si es *posible*. Un stock de seguridad negativo no
es una mala decision, es una imposibilidad aritmetica; una inversion agregada que
no cuadra con la suma del detalle no es discutible, es un bug. Esa distincion es
lo que hace que estas comprobaciones se puedan correr sobre 50.000 SKU sin un
solo dato etiquetado.

**Por que cada invariante lleva su derivacion escrita.** Un invariante que nadie
puede justificar acaba relajado la primera vez que falla. El comentario de cada
uno dice de que linea de ``jobs/inventory_optimization.py`` se deriva, para que
al fallar la pregunta sea "cual de las dos cambio" y no "bajemos la tolerancia".
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Las recomendaciones que el job emite para un SKU que no pudo analizar. No son
# un fallo: jobs/inventory_optimization.run() las aisla a proposito para que un
# SKU sin demanda o sin costo no mate el lote entero. Salen con todos los campos
# numericos en cero, asi que los invariantes de magnitud no les aplican -- pero
# los de conteo si, porque desaparecer en silencio si seria un fallo.
#: Las policy_kind que NO traen una politica dimensionada, y por eso no entran a las
#: invariantes por SKU de abajo. Tiene que coincidir con `jobs.qa.UNPRICED_POLICY_KINDS`
#: -- hasta el 2026-09-19 le faltaba `insufficient_history`, que quedaba dentro del
#: analisis con sus ceros y pasaba toda invariante trivialmente. `tests/` fija la
#: igualdad de los dos conjuntos para que no se separen otra vez.
POLITICAS_SALTADAS = frozenset({
    "skipped_zero_demand", "skipped_zero_cost", "insufficient_history",
})

# Tolerancia relativa de las igualdades de dinero. Los agregados son sumas de
# float sobre decenas de miles de terminos, asi que el error se acumula con N;
# 1e-6 relativo aguanta 50.000 SKU con margen de sobra y sigue siendo mil veces
# mas estricto que cualquier error de logica real (que mueve euros, no ulps).
REL_TOL = 1e-6
ABS_TOL = 1e-6


@dataclass(frozen=True)
class Violacion:
    """Una violacion concreta, con el dato que la prueba.

    ``evidencia`` lleva los numeros, no una descripcion: quien lea el informe
    tiene que poder reproducir el fallo sin volver a correr nada.
    """

    invariante: str
    ambito: str  # product_id, o "<lote>" cuando es un invariante agregado
    evidencia: str


@dataclass(frozen=True)
class Coherencia:
    """Veredicto sobre un lote completo."""

    n_recomendaciones: int
    n_saltadas: int
    violaciones: tuple[Violacion, ...] = ()
    invariantes_corridos: tuple[str, ...] = ()

    @property
    def coherente(self) -> bool:
        return not self.violaciones

    @property
    def por_invariante(self) -> dict[str, int]:
        """Cuantas violaciones aporto cada invariante, para priorizar."""
        conteo: dict[str, int] = {}
        for v in self.violaciones:
            conteo[v.invariante] = conteo.get(v.invariante, 0) + 1
        return conteo

    def resumen(self) -> str:
        if self.coherente:
            return (
                f"{self.n_recomendaciones} recomendaciones "
                f"({self.n_saltadas} saltadas) · "
                f"{len(self.invariantes_corridos)} invariantes · sin violaciones"
            )
        detalle = ", ".join(f"{k}={n}" for k, n in sorted(self.por_invariante.items()))
        return (
            f"{self.n_recomendaciones} recomendaciones "
            f"({self.n_saltadas} saltadas) · "
            f"{len(self.violaciones)} violaciones: {detalle}"
        )


_REGISTRO: dict[str, Callable[[object], list[Violacion]]] = {}


def invariante(nombre: str) -> Callable[
    [Callable[[object], list[Violacion]]], Callable[[object], list[Violacion]]
]:
    """Registra un invariante bajo un nombre estable.

    El nombre viaja al informe y a los mensajes de fallo, asi que renombrarlo
    rompe la comparacion historica a proposito: es una decision, no un descuido.
    """

    def deco(fn: Callable[[object], list[Violacion]]) -> Callable[[object], list[Violacion]]:
        if nombre in _REGISTRO:
            raise ValueError(f"invariante duplicado: {nombre}")
        _REGISTRO[nombre] = fn
        return fn

    return deco


def _analizadas(reporte: object) -> list[object]:
    """Las recomendaciones que el job realmente analizo.

    Acceso directo a ``.recommendations`` y ``.policy_kind`` -- nada de
    ``getattr(obj, campo, default)``: si un rename cambia el nombre del campo,
    esto tiene que romper fuerte y no emitir un veredicto silenciosamente
    equivocado sobre una lista vacia.
    """
    return [r for r in reporte.recommendations if r.policy_kind not in POLITICAS_SALTADAS]


# --------------------------------------------------------------------------
# Invariantes por SKU
# --------------------------------------------------------------------------


@invariante("buffer_no_negativo")
def _buffer_no_negativo(reporte: object) -> list[Violacion]:
    """Stock de seguridad y punto de reorden son cantidades fisicas.

    Un buffer negativo no es una politica agresiva: es unidades que no existen.
    """
    out = []
    for r in _analizadas(reporte):
        if r.safety_stock < 0:
            out.append(Violacion("buffer_no_negativo", r.product_id,
                                 f"safety_stock={r.safety_stock:.6g}"))
        if r.reorder_point < 0:
            out.append(Violacion("buffer_no_negativo", r.product_id,
                                 f"reorder_point={r.reorder_point:.6g}"))
    return out


@invariante("rop_cubre_el_buffer")
def _rop_cubre_el_buffer(reporte: object) -> list[Violacion]:
    """ROP >= SS.

    Se deriva de la linea que lo calcula: ``reorder = mu*L + ss``, con mu >= 0 y
    L >= 0. Un ROP por debajo del SS significa que el punto de pedido dispara
    *despues* de haberse comido el colchon, que es justo lo que el colchon
    existe para evitar.
    """
    out = []
    for r in _analizadas(reporte):
        if r.reorder_point + ABS_TOL < r.safety_stock:
            out.append(Violacion(
                "rop_cubre_el_buffer", r.product_id,
                f"reorder_point={r.reorder_point:.6g} < safety_stock={r.safety_stock:.6g}",
            ))
    return out


@invariante("servicio_es_probabilidad")
def _servicio_es_probabilidad(reporte: object) -> list[Violacion]:
    """El nivel de servicio de ciclo vive en (0, 1) abierto.

    En 0 o en 1 el factor z sale infinito y todo lo que dependa de el deja de
    ser un numero. El job valida el parametro de entrada; esto valida el valor
    *por SKU*, que puede haberlo movido ``tune_service_level``.
    """
    out = []
    for r in _analizadas(reporte):
        if not (0.0 < r.service_level < 1.0):
            out.append(Violacion("servicio_es_probabilidad", r.product_id,
                                 f"service_level={r.service_level!r}"))
    return out


@invariante("sin_nan_ni_infinito")
def _sin_nan_ni_infinito(reporte: object) -> list[Violacion]:
    """Ningun campo numerico emitido puede ser NaN ni infinito.

    Es el invariante que mas gana con la escala: un NaN nace de una division
    por casi-cero o de una serie degenerada, casos que en un fixture de dos SKU
    no ocurren nunca y en 50.000 ocurren siempre.
    """
    campos = ("forecast", "error_std", "bias", "mae", "reorder_point",
              "safety_stock", "z_factor", "service_level", "unit_cost",
              "lead_periods", "cycle_investment", "ss_investment", "investment")
    out = []
    for r in _analizadas(reporte):
        for c in campos:
            v = getattr(r, c)
            if not math.isfinite(v):
                out.append(Violacion("sin_nan_ni_infinito", r.product_id, f"{c}={v!r}"))
        # order_quantity y order_up_to son opcionales por politica, no por
        # accidente: (s,Q) no tiene order_up_to y (R,S) no tiene order_quantity.
        # La ausencia es un caso legitimo con evidencia propia; lo que no vale
        # es que el campo exista y no sea un numero.
        for c in ("order_quantity", "order_up_to"):
            v = getattr(r, c)
            if v is not None and not math.isfinite(v):
                out.append(Violacion("sin_nan_ni_infinito", r.product_id, f"{c}={v!r}"))
    return out


@invariante("la_politica_trae_su_parametro")
def _la_politica_trae_su_parametro(reporte: object) -> list[Violacion]:
    """Cada politica emite el parametro que la hace ejecutable.

    ``(s, Q)`` sin cantidad de pedido y ``(R, S)`` sin nivel objetivo son
    recomendaciones que el comprador no puede ejecutar. Es el mismo hueco que
    la bateria denuncia en otras tareas: el campo existe en el dataclass y sale
    vacio.
    """
    out = []
    for r in _analizadas(reporte):
        if r.policy_kind == "(s, Q)":
            if r.order_quantity is None or r.order_quantity <= 0:
                out.append(Violacion("la_politica_trae_su_parametro", r.product_id,
                                     f"(s, Q) con order_quantity={r.order_quantity!r}"))
        elif r.policy_kind == "(R, S)":
            if r.order_up_to is None:
                out.append(Violacion("la_politica_trae_su_parametro", r.product_id,
                                     "(R, S) sin order_up_to"))
            elif r.order_up_to + ABS_TOL < r.safety_stock:
                out.append(Violacion(
                    "la_politica_trae_su_parametro", r.product_id,
                    f"(R, S) con order_up_to={r.order_up_to:.6g} "
                    f"por debajo del safety_stock={r.safety_stock:.6g}",
                ))
        else:
            out.append(Violacion("la_politica_trae_su_parametro", r.product_id,
                                 f"policy_kind desconocida: {r.policy_kind!r}"))
    return out


@invariante("la_inversion_por_sku_cuadra")
def _la_inversion_por_sku_cuadra(reporte: object) -> list[Violacion]:
    """investment == cycle_investment + ss_investment, y ninguna es negativa.

    Es la descomposicion que el entregable publica. Si no cuadra, el resumen
    ejecutivo y el detalle cuentan cosas distintas al mismo cliente.
    """
    out = []
    for r in _analizadas(reporte):
        if r.cycle_investment < 0 or r.ss_investment < 0:
            out.append(Violacion(
                "la_inversion_por_sku_cuadra", r.product_id,
                f"cycle={r.cycle_investment:.6g} ss={r.ss_investment:.6g}",
            ))
            continue
        suma = r.cycle_investment + r.ss_investment
        if not math.isclose(r.investment, suma, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            out.append(Violacion(
                "la_inversion_por_sku_cuadra", r.product_id,
                f"investment={r.investment:.6g} != cycle+ss={suma:.6g}",
            ))
    return out


@invariante("el_costo_unitario_es_positivo")
def _el_costo_unitario_es_positivo(reporte: object) -> list[Violacion]:
    """Toda recomendacion analizada tiene costo unitario > 0.

    El job ya aisla los SKU sin costo usable en ``skipped_zero_cost`` justo para
    que el suelo de 1e-6 de ``compute_eoq`` no genere un pedido enorme de
    aspecto valido. Que una recomendacion *analizada* llegue aqui con costo <= 0
    significa que ese aislamiento se rompio.
    """
    return [
        Violacion("el_costo_unitario_es_positivo", r.product_id,
                  f"unit_cost={r.unit_cost:.6g}")
        for r in _analizadas(reporte)
        if r.unit_cost <= 0
    ]


# --------------------------------------------------------------------------
# Invariantes agregados -- los que sólo se pueden violar con volumen
# --------------------------------------------------------------------------


@invariante("nadie_desaparece")
def _nadie_desaparece(reporte: object) -> list[Violacion]:
    """El conteo publicado cuadra con la lista emitida.

    ``n_skus`` es lo que el resumen ejecutivo cita. Si no coincide con las filas
    del detalle, hay SKU que entraron y no salieron por ningun lado -- el modo
    de fallo mas caro a escala, porque no produce error y el cliente lo descubre
    cuando le falta un producto.
    """
    n = len(reporte.recommendations)
    if reporte.n_skus != n:
        return [Violacion("nadie_desaparece", "<lote>",
                          f"n_skus={reporte.n_skus} != len(recommendations)={n}")]
    return []


@invariante("el_agregado_cuadra_con_el_detalle")
def _el_agregado_cuadra_con_el_detalle(reporte: object) -> list[Violacion]:
    """requested_investment == suma de la inversion de las analizadas.

    El job construye el agregado desde ``items``, que excluye las saltadas; el
    detalle que el cliente lee son las recomendaciones. Que las dos vias den el
    mismo numero es lo unico que garantiza que el total del informe sea el total
    de sus filas.
    """
    suma = sum(r.investment for r in _analizadas(reporte))
    if not math.isclose(reporte.requested_investment, suma,
                        rel_tol=REL_TOL, abs_tol=ABS_TOL):
        return [Violacion(
            "el_agregado_cuadra_con_el_detalle", "<lote>",
            f"requested_investment={reporte.requested_investment:.10g} != "
            f"suma del detalle={suma:.10g} "
            f"(delta={reporte.requested_investment - suma:.6g})",
        )]
    return []


@invariante("el_recorte_respeta_el_suelo")
def _el_recorte_respeta_el_suelo(reporte: object) -> list[Violacion]:
    """El ajuste a presupuesto nunca baja del suelo de ciclo, ni sube del pedido.

    ``cycle_floor`` es el stock de ciclo, que no es recortable: recortarlo no
    ahorra caja, sólo desplaza el pedido. Y ``final <= requested`` porque el
    ajuste sólo puede quitar. Un ``final`` fuera de ese intervalo significa que
    la asignacion invento inversion o rompio el suelo economico.
    """
    out = []
    final, suelo, pedido = (reporte.final_investment, reporte.cycle_floor,
                            reporte.requested_investment)
    if final > pedido * (1 + REL_TOL) + ABS_TOL:
        out.append(Violacion("el_recorte_respeta_el_suelo", "<lote>",
                             f"final={final:.6g} > requested={pedido:.6g}"))
    if reporte.feasible and final + ABS_TOL < suelo * (1 - REL_TOL):
        out.append(Violacion("el_recorte_respeta_el_suelo", "<lote>",
                             f"final={final:.6g} < cycle_floor={suelo:.6g} con feasible=True"))
    if not (0.0 <= reporte.safety_stock_scale <= 1.0 + REL_TOL):
        out.append(Violacion("el_recorte_respeta_el_suelo", "<lote>",
                             f"safety_stock_scale={reporte.safety_stock_scale!r} fuera de [0, 1]"))
    return out


@invariante("el_presupuesto_se_respeta")
def _el_presupuesto_se_respeta(reporte: object) -> list[Violacion]:
    """Si el plan se declara factible bajo un tope, no lo pasa.

    Declararse factible y pasarse del tope es la unica forma que tiene este job
    de mentirle a finanzas, y es silenciosa: el numero sale, la bandera dice que
    si, y nadie resta.
    """
    if reporte.budget is None or not reporte.feasible:
        return []
    if reporte.final_investment > reporte.budget * (1 + REL_TOL) + ABS_TOL:
        return [Violacion(
            "el_presupuesto_se_respeta", "<lote>",
            f"final_investment={reporte.final_investment:.6g} > budget={reporte.budget:.6g} "
            "con feasible=True",
        )]
    return []


@invariante("los_conteos_derivados_cuadran")
def _los_conteos_derivados_cuadran(reporte: object) -> list[Violacion]:
    """n_at_risk y n_intermittent se pueden recontar desde el detalle.

    Son los dos numeros que deciden a que SKU mira primero un planner. Si el
    resumen los calcula sobre una lista y el detalle muestra otra, la atencion
    del cliente se dirige al sitio equivocado.
    """
    out = []
    riesgo = sum(1 for r in reporte.recommendations if r.status == "high_bias")
    interm = sum(1 for r in reporte.recommendations if r.intermittent)
    if reporte.n_at_risk != riesgo:
        out.append(Violacion("los_conteos_derivados_cuadran", "<lote>",
                             f"n_at_risk={reporte.n_at_risk} != recontado={riesgo}"))
    if reporte.n_intermittent != interm:
        out.append(Violacion("los_conteos_derivados_cuadran", "<lote>",
                             f"n_intermittent={reporte.n_intermittent} != recontado={interm}"))
    return out


@invariante("los_identificadores_son_unicos")
def _los_identificadores_son_unicos(reporte: object) -> list[Violacion]:
    """Un SKU aparece una vez.

    Un duplicado dobla su inversion en el agregado y hace que el comprador pida
    dos veces. Con dos SKU en el fixture es imposible de producir; con miles,
    lo produce cualquier agrupacion con una clave mal elegida.
    """
    vistos: dict[str, int] = {}
    for r in reporte.recommendations:
        vistos[r.product_id] = vistos.get(r.product_id, 0) + 1
    return [
        Violacion("los_identificadores_son_unicos", pid, f"aparece {n} veces")
        for pid, n in sorted(vistos.items()) if n > 1
    ]


def verificar(reporte: object, *, solo: Iterable[str] | None = None) -> Coherencia:
    """Corre todos los invariantes sobre un ``JobReport`` y devuelve el veredicto.

    ``solo`` restringe a un subconjunto por nombre -- util para aislar un fallo,
    nunca para silenciarlo: el informe publica siempre cuantos corrio.
    """
    nombres = tuple(_REGISTRO) if solo is None else tuple(solo)
    desconocidos = set(nombres) - set(_REGISTRO)
    if desconocidos:
        raise KeyError(f"invariantes desconocidos: {sorted(desconocidos)}")

    violaciones: list[Violacion] = []
    for nombre in nombres:
        violaciones.extend(_REGISTRO[nombre](reporte))

    saltadas = sum(1 for r in reporte.recommendations
                   if r.policy_kind in POLITICAS_SALTADAS)
    return Coherencia(
        n_recomendaciones=len(reporte.recommendations),
        n_saltadas=saltadas,
        violaciones=tuple(violaciones),
        invariantes_corridos=nombres,
    )


def nombres_de_invariantes() -> tuple[str, ...]:
    """Los invariantes registrados, en orden de declaracion."""
    return tuple(_REGISTRO)
