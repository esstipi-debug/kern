"""Las comprobaciones: una por tarea de software de la bateria.

Cada una corre la capa ``jobs/`` sobre un dataset real y verifica el
``criterio_de_aprobado`` **campo por campo**, no "a ojo". La evidencia que
devuelve tiene que alcanzar para defender el veredicto sin volver a correr nada.

Importar este paquete registra las comprobaciones en el scorer: cada modulo
hermano se importa abajo por su side-effect de ``@comprobacion``. Antes era un
unico modulo de 2031 lineas; se partio por familia de tarea sin tocar la logica,
y este ``__init__`` conserva las dos formas de uso que ya existian --
``import src.bateria.comprobaciones`` para registrar, y
``from src.bateria.comprobaciones import tNN_...`` para nombrar una.
"""

from __future__ import annotations

from src.bateria.comprobaciones._comunes import (  # noqa: F401
    _alguno_contiene,
    _campos_del_registro,
    _campos_planos,
    _cerrar_veredicto,
)
from src.bateria.comprobaciones.abastecimiento import (  # noqa: F401
    _CandidatoAjuste,
    _CandidatoCaja,
    _CandidatoOC,
    _veredicto_ajuste,
    _veredicto_caja,
    _veredicto_oc,
    t05_propuesta_de_oc_por_proveedor,
    t06_ajuste_a_las_condiciones_del_proveedor,
    t07_calendario_de_caja_contra_el_tope,
)
from src.bateria.comprobaciones.comercial import (  # noqa: F401
    _CandidatoActa,
    _CandidatoDevoluciones,
    _CandidatoExposicion,
    _CandidatoMarkdown,
    _veredicto_acta,
    _veredicto_devoluciones,
    _veredicto_exposicion,
    _veredicto_markdown,
    t12_markdown_con_unidades_y_margen,
    t13_exposicion_a_disrupcion,
    t14_acta_de_sop,
    t20_flujo_de_devoluciones,
)
from src.bateria.comprobaciones.politica import (  # noqa: F401
    t03_segmentacion_con_servicio_diferenciado,
    t04_politica_con_ss_sensible_al_lead_time,
    t11_excedente_y_obsoleto,
)
from src.bateria.comprobaciones.pronostico import (  # noqa: F401
    t01_pronostico_con_banda,
    t02_sesgo_y_precision,
)
from src.bateria.comprobaciones.proveedores import (  # noqa: F401
    _CandidatoLeadTime,
    _CandidatoScorecard,
    _CandidatoServicio,
    _veredicto_lead_time,
    _veredicto_scorecard,
    _veredicto_servicio,
    t08_lead_time_real_por_proveedor,
    t09_scorecard_de_proveedores,
    t10_servicio_realizado_al_cliente,
)

__all__ = [
    "_CandidatoActa",
    "_CandidatoAjuste",
    "_CandidatoCaja",
    "_CandidatoDevoluciones",
    "_CandidatoExposicion",
    "_CandidatoLeadTime",
    "_CandidatoMarkdown",
    "_CandidatoOC",
    "_CandidatoScorecard",
    "_CandidatoServicio",
    "_alguno_contiene",
    "_campos_del_registro",
    "_campos_planos",
    "_cerrar_veredicto",
    "_veredicto_acta",
    "_veredicto_ajuste",
    "_veredicto_caja",
    "_veredicto_devoluciones",
    "_veredicto_exposicion",
    "_veredicto_lead_time",
    "_veredicto_markdown",
    "_veredicto_oc",
    "_veredicto_scorecard",
    "_veredicto_servicio",
    "t01_pronostico_con_banda",
    "t02_sesgo_y_precision",
    "t03_segmentacion_con_servicio_diferenciado",
    "t04_politica_con_ss_sensible_al_lead_time",
    "t05_propuesta_de_oc_por_proveedor",
    "t06_ajuste_a_las_condiciones_del_proveedor",
    "t07_calendario_de_caja_contra_el_tope",
    "t08_lead_time_real_por_proveedor",
    "t09_scorecard_de_proveedores",
    "t10_servicio_realizado_al_cliente",
    "t11_excedente_y_obsoleto",
    "t12_markdown_con_unidades_y_margen",
    "t13_exposicion_a_disrupcion",
    "t14_acta_de_sop",
    "t20_flujo_de_devoluciones",
]
