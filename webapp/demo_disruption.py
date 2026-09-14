"""Sexta salida del embudo: exposición a disrupción por proveedor (kern#255).

La salida #1 mide el plazo real contra el declarado y se gana el derecho a seguir
hablando. Ésta es su hermana: sale del **mismo archivo de recepciones**, porque
ese export ya trae la columna de proveedor y ``to_deliveries`` ya la agrupó. El
visitante no sube nada nuevo.

Para el vertical de repuestos industriales, donde el quiebre pesa más que todo lo
demás junto, dice algo que ninguna de las cinco salidas dice.

LA DECISIÓN DE PRIVACIDAD, QUE ES LA PARTE QUE NO SE PUEDE SALTAR
=================================================================
``src.disruption.build_query`` arma la consulta con la RAZÓN SOCIAL del proveedor
entrecomillada, y ``build_url`` la mete en el *query string* de un GET público
contra GDELT::

    "Sellos y Empaques SpA" (theme:NATURAL_DISASTER OR theme:STRIKE OR ...)

Una llamada por proveedor. La lista completa queda enumerable por quien vea esos
logs, y el orden y el timing la delatan aunque las consultas fueran anónimas
entre sí.

El argumento de venta de Kern es «sólo lectura, no escribo nada». Eso habla de
ESCRITURA: promete que no mutamos el sistema de registro del cliente. **No dice
nada sobre lo que sale.** Y acá sale con quién compra el cliente, que no es dato
público: es su cadena de abastecimiento, exactamente lo que un competidor querría.

Por eso el escaneo es OPT-IN, apagado por defecto, y sin consentimiento no se
emite NI UNA llamada saliente -- no una que devuelva vacío: ninguna. El resto del
diagnóstico corre igual, y la omisión se declara con su motivo.

Un opt-in pre-marcado no es un opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobs import disruption_scan_job
from src.disruption import Fetcher, SupplierRow

#: El texto que ve el visitante al lado de la casilla. Vive acá y no en la
#: plantilla para que el endpoint, el informe y el test hablen del mismo texto:
#: un consentimiento cuya redacción vive en dos lados se desincroniza y deja de
#: ser consentimiento.
CONSENT_TEXT = (
    "Para buscar noticias de disrupción, se envía el NOMBRE de cada proveedor a "
    "GDELT, un servicio público de noticias. No se envía ningún otro dato: ni "
    "SKU, ni cantidades, ni precios, ni fechas. Si preferís no hacerlo, el resto "
    "del análisis corre igual."
)

#: Motivo exacto cuando el visitante no dio consentimiento. Se declara; no se
#: omite en silencio.
SIN_CONSENTIMIENTO = (
    "no se buscaron noticias de disrupción: hacerlo enviaría el nombre de cada "
    "proveedor a un servicio externo, y no se marcó la casilla que lo autoriza"
)

#: Tope de proveedores por corrida. Una llamada HTTP por proveedor, secuencial:
#: sin tope, un archivo con 300 proveedores convierte «30 minutos» en otra cosa.
#: El tope NO es silencioso -- ver ``n_omitidos``.
MAX_PROVEEDORES = 25

#: La advertencia que el entregable repite con estas palabras. Sale del propio
#: ``src/disruption.py``: la señal de GDELT prioriza a quién investigar, NO es
#: una probabilidad anual de disrupción calibrada.
CAVEAT_TAMIZAJE = (
    "La señal de GDELT es un PROXY DE TAMIZAJE, no una probabilidad calibrada: "
    "sirve para priorizar a qué proveedor mirar primero, no para afirmar cuán "
    "probable es que falle."
)


@dataclass(frozen=True)
class DisruptionScan:
    """El resultado, o el motivo por el que no hubo resultado.

    ``corrio=False`` NO es un error: es el camino esperado cuando el visitante no
    dio consentimiento. Por eso ``motivo`` viaja siempre que ``corrio`` sea falso
    y el endpoint lo devuelve con 200, no con 4xx.
    """

    corrio: bool
    motivo: str = ""
    report: disruption_scan_job.DisruptionScanReport | None = None
    #: Proveedores que traía el archivo, antes de aplicar el tope.
    n_vistos: int = 0
    #: Proveedores efectivamente consultados.
    n_consultados: int = 0
    #: Los que quedaron fuera por el tope. Un tope silencioso se lee como
    #: cobertura completa, que es peor que no correr.
    n_omitidos: int = 0

    @property
    def hay_senal(self) -> bool:
        return bool(self.report and self.report.n_signalled)


def proveedores_de_recepciones(
    grouped: dict[str, list[dict]], *, tope: int = MAX_PROVEEDORES
) -> tuple[list[SupplierRow], int]:
    """Los proveedores del archivo YA agrupado por ``to_deliveries``.

    No se vuelve a parsear nada: la salida #1 agrupa las recepciones por nombre
    de proveedor, y esas claves son la lista. De ahí que esta salida no le pida
    al visitante ningún archivo nuevo.

    ``country`` queda vacío a propósito. El export de recepciones no lo trae y
    deducirlo del nombre sería adivinar; ``build_query`` lo trata como opcional y
    sin él sólo se pierde precisión geográfica en la consulta. ``annual_spend``
    queda en 0 por la misma razón: se usaría para ponderar el riesgo por plata en
    juego, y ese dato no está en este archivo.

    Devuelve también cuántos proveedores había ANTES del tope, para poder decir
    cuántos quedaron sin mirar.
    """
    nombres = sorted(grouped)
    return [SupplierRow(supplier=n) for n in nombres[:tope]], len(nombres)


def run_disruption_scan(
    grouped: dict[str, list[dict]],
    *,
    consentimiento: bool,
    fetcher: Fetcher | None = None,
    tope: int = MAX_PROVEEDORES,
    timespan: str = "3m",
    now=None,
) -> DisruptionScan:
    """El escaneo, o la negativa declarada.

    ``consentimiento=False`` retorna ANTES de construir siquiera las filas: no
    hay consulta, no hay URL armada, no hay nada que pueda salir por accidente.
    Un ``fetcher`` que devuelve vacío no sería lo mismo -- la URL con el nombre
    del proveedor ya se habría armado, y un log intermedio ya la habría visto.
    """
    if not consentimiento:
        return DisruptionScan(corrio=False, motivo=SIN_CONSENTIMIENTO)

    filas, n_vistos = proveedores_de_recepciones(grouped, tope=tope)
    if not filas:
        return DisruptionScan(
            corrio=False,
            motivo="el archivo no trajo ningún proveedor con recepciones utilizables",
            n_vistos=n_vistos,
        )

    report = disruption_scan_job.run(filas, fetcher=fetcher, timespan=timespan, now=now)
    return DisruptionScan(
        corrio=True,
        report=report,
        n_vistos=n_vistos,
        n_consultados=len(filas),
        n_omitidos=max(0, n_vistos - len(filas)),
    )


def render_mini_report(scan: DisruptionScan, *, ts: str) -> str:
    """El informe del operador. Dice lo que no se miró tanto como lo que sí."""
    lineas = ["# Exposición a disrupción por proveedor\n", f"_Generado {ts}_\n"]

    if not scan.corrio:
        lineas += [
            "**No se corrió.**\n",
            f"{scan.motivo}\n",
            "\nEl resto del diagnóstico no depende de esta salida.\n",
        ]
        return "".join(lineas)

    rep = scan.report
    assert rep is not None  # corrio=True siempre trae report
    lineas.append(f"{rep.summary}\n\n")

    if scan.n_omitidos:
        lineas.append(
            f"> **Cobertura parcial.** Se consultaron {scan.n_consultados} de "
            f"{scan.n_vistos} proveedores del archivo; **{scan.n_omitidos} quedaron "
            f"sin mirar** por el tope de {MAX_PROVEEDORES} por corrida.\n\n"
        )
    if rep.n_failed:
        lineas.append(
            f"> {rep.n_failed} consulta(s) fallaron y esos proveedores quedan con "
            "señal desconocida -- no con señal cero.\n\n"
        )

    if rep.n_signalled:
        lineas.append("| Proveedor | Artículos | Fuentes | Exposición |\n")
        lineas.append("|---|---:|---:|---:|\n")
        for sig in rep.signals:
            if sig.exposure_score <= 0 or sig.fetch_failed:
                continue
            lineas.append(
                f"| {sig.supplier} | {sig.article_count} | {sig.distinct_sources} "
                f"| {sig.exposure_score:.2f} |\n"
            )
        lineas.append("\n")
    else:
        lineas.append(
            "Ningún proveedor mostró noticias de disrupción en la ventana. "
            "Eso NO es una garantía: es ausencia de señal en una fuente de noticias.\n\n"
        )

    lineas.append(f"> {CAVEAT_TAMIZAJE}\n")
    return "".join(lineas)
