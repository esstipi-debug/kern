"""Sexta salida: exposición a disrupción, y la decisión de privacidad (kern#255).

El test que manda en este archivo es
``test_sin_consentimiento_no_sale_ni_una_llamada``: no basta con que el resultado
diga que no corrió — **el fetcher no tiene que haber sido invocado**. Un fetcher
que devuelve vacío no sería lo mismo: la URL con la razón social del proveedor ya
se habría armado, y cualquier log intermedio ya la habría visto.

El segundo que manda es ``test_solo_viaja_el_nombre_del_proveedor``: construye la
consulta sobre un archivo con SKU, cantidades, precios y fechas, y afirma que
nada de eso aparece en la URL. Es el test que impide que un refactor futuro
filtre un campo nuevo sin que nadie se entere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp import demo_disruption, demo_lead_time_scan, security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

#: Un export con MUCHO mas que el nombre del proveedor: SKU, cantidades, precios
#: y fechas. Todo eso tiene que quedarse adentro.
CSV_RICO = (
    "supplier,order_date,receipt_date,declared_lead_time_days,product_id,qty,unit_price\n"
    "Sellos y Empaques SpA,2026-01-01,2026-02-04,21,SE-ORING-220,480,17350\n"
    "Sellos y Empaques SpA,2026-02-01,2026-03-10,21,SE-ORING-221,120,9900\n"
    "Sellos y Empaques SpA,2026-03-01,2026-03-29,21,SE-ORING-222,75,4310\n"
    "Rodamientos del Sur Ltda,2026-01-05,2026-02-02,14,RS-BRG-330,900,28400\n"
    "Rodamientos del Sur Ltda,2026-02-05,2026-03-01,14,RS-BRG-331,60,7700\n"
    "Rodamientos del Sur Ltda,2026-03-05,2026-04-02,14,RS-BRG-332,30,5150\n"
)

#: Los valores del CSV que NO pueden aparecer en ninguna URL saliente.
NADA_DE_ESTO_VIAJA = (
    "SE-ORING", "RS-BRG",           # SKU
    "480", "900", "17350", "28400",  # cantidades y precios
    "2026-01-01", "2026-03-29",      # fechas
)


@pytest.fixture()
def aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "LEADS_FILE", tmp_path / "leads.jsonl")
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", tmp_path / "leads-reports")
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)


class _Espia:
    """Fetcher espia: anota cada URL y devuelve una respuesta vacia de GDELT."""

    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        return '{"articles": []}'


def _agrupado(csv_texto: str = CSV_RICO):
    import io

    import pandas as pd

    df = pd.read_csv(io.StringIO(csv_texto))
    grouped, _ = demo_lead_time_scan.to_deliveries(df)
    return grouped


# -- LA DECISION DE PRIVACIDAD -------------------------------------------------


def test_sin_consentimiento_no_sale_ni_una_llamada():
    """El test que manda. No que devuelva vacio: que NO SE INVOQUE."""
    espia = _Espia()
    scan = demo_disruption.run_disruption_scan(
        _agrupado(), consentimiento=False, fetcher=espia
    )

    assert espia.urls == [], "se armo una consulta saliente sin consentimiento"
    assert scan.corrio is False
    assert scan.motivo == demo_disruption.SIN_CONSENTIMIENTO
    assert scan.report is None


def test_solo_viaja_el_nombre_del_proveedor():
    """Con consentimiento SI se consulta -- y sale el nombre y nada mas.

    Sin este test, un refactor que agregue un campo al SupplierRow puede empezar
    a filtrar dato del cliente sin que nadie se entere.
    """
    espia = _Espia()
    demo_disruption.run_disruption_scan(_agrupado(), consentimiento=True, fetcher=espia)

    assert espia.urls, "con consentimiento tiene que haber consultas"
    todo = " ".join(espia.urls)
    assert "Sellos" in todo and "Rodamientos" in todo, "el nombre es lo que ancla la busqueda"
    for prohibido in NADA_DE_ESTO_VIAJA:
        assert prohibido not in todo, f"{prohibido!r} salio en una URL: no puede viajar"


def test_una_llamada_por_proveedor_y_no_una_por_recepcion():
    """Seis recepciones, dos proveedores: dos consultas. Si fuera una por fila,
    el volumen enviado triplicaria sin dar mas informacion."""
    espia = _Espia()
    demo_disruption.run_disruption_scan(_agrupado(), consentimiento=True, fetcher=espia)
    assert len(espia.urls) == 2


def test_el_endpoint_niega_por_defecto_cuando_el_campo_no_viene(aislado):
    """El default es False A PROPOSITO: un caller que se olvide del campo recibe
    la negativa, no el escaneo. El default tiene que fallar hacia no-enviar."""
    r = client.post("/api/demo-disruption", data={"email": "lead@test.com", "use_sample": "true"})

    assert r.status_code == 200, "una negativa no es un error"
    d = r.json()
    assert d["status"] == "omitido"
    assert d["motivo"] == demo_disruption.SIN_CONSENTIMIENTO


def test_la_negativa_le_dice_al_visitante_que_se_estaria_enviando(aislado):
    """El texto del consentimiento viaja con la negativa: quien la lea tiene que
    poder decidir con la informacion delante, no buscarla."""
    r = client.post("/api/demo-disruption", data={"email": "lead@test.com"})
    texto = r.json()["consent_text"]

    assert "GDELT" in texto
    assert "NOMBRE" in texto
    assert "ni SKU" in texto and "precios" in texto
    assert "el resto del análisis corre igual" in texto.lower() or "corre igual" in texto


def test_sin_consentimiento_el_endpoint_ni_siquiera_lee_el_archivo(aislado, monkeypatch):
    """La negativa se resuelve antes de parsear: sin consentimiento no hay nada
    que agrupar y por lo tanto nada que pueda salir por accidente."""
    def _explota(*a, **k):
        raise AssertionError("se leyo el archivo sin consentimiento")

    monkeypatch.setattr(appmod, "load_table", _explota)
    r = client.post("/api/demo-disruption", data={"email": "lead@test.com", "use_sample": "true"})
    assert r.json()["status"] == "omitido"


# -- COBERTURA: el tope nunca es silencioso ------------------------------------


def test_el_tope_nombra_cuantos_quedaron_sin_mirar():
    """Un lector que ve 2 proveedores y no sabe que habia 2 mas lee cobertura
    completa. El tope se declara o no sirve."""
    espia = _Espia()
    grouped = _agrupado()
    scan = demo_disruption.run_disruption_scan(
        grouped, consentimiento=True, fetcher=espia, tope=1
    )

    assert scan.n_consultados == 1
    assert scan.n_vistos == 2
    assert scan.n_omitidos == 1
    assert len(espia.urls) == 1

    informe = demo_disruption.render_mini_report(scan, ts="2026-08-19T00:00:00Z")
    assert "sin mirar" in informe
    assert "1 de 2" in informe


def test_sin_tope_alcanzado_no_se_habla_de_cobertura_parcial():
    espia = _Espia()
    scan = demo_disruption.run_disruption_scan(_agrupado(), consentimiento=True, fetcher=espia)
    assert scan.n_omitidos == 0
    assert "Cobertura parcial" not in demo_disruption.render_mini_report(
        scan, ts="2026-08-19T00:00:00Z"
    )


# -- HONESTIDAD DEL ENTREGABLE -------------------------------------------------


def test_el_informe_declara_que_es_un_proxy_de_tamizaje():
    """No se puede vender esto como medicion de riesgo. El modulo lo declara y el
    entregable tiene que repetirlo con esas palabras."""
    espia = _Espia()
    scan = demo_disruption.run_disruption_scan(_agrupado(), consentimiento=True, fetcher=espia)
    informe = demo_disruption.render_mini_report(scan, ts="2026-08-19T00:00:00Z")

    assert "PROXY DE TAMIZAJE" in informe
    assert "no es una probabilidad" in informe.lower() or "no una probabilidad" in informe


def test_sin_senal_no_se_afirma_que_no_hay_riesgo():
    """Ausencia de noticias no es ausencia de riesgo, y el informe lo dice."""
    espia = _Espia()  # siempre devuelve cero articulos
    scan = demo_disruption.run_disruption_scan(_agrupado(), consentimiento=True, fetcher=espia)
    informe = demo_disruption.render_mini_report(scan, ts="2026-08-19T00:00:00Z")

    assert "NO es una garantía" in informe


def test_una_consulta_caida_no_hunde_la_corrida():
    """Un proveedor inalcanzable queda con senal DESCONOCIDA, no con senal cero,
    y los demas se siguen consultando.

    Se levanta GdeltUnavailable y no un OSError pelado porque ese es el
    contrato: _urllib_fetch envuelve CUALQUIER excepcion de red en
    GdeltUnavailable y scan_suppliers atrapa GdeltError. La ruta real
    -- que pasa fetcher=None -- queda cubierta por esa envoltura. Un fetcher
    inyectado que levante otra cosa si hunde el escaneo; no se ensancha el
    except aca porque un except Exception en ese lugar taparia bugs.
    """
    from src.disruption import GdeltUnavailable

    llamadas: list[str] = []

    def _una_falla(url: str) -> str:
        llamadas.append(url)
        if "Sellos" in url:
            raise GdeltUnavailable("timeout")
        return '{"articles": []}'

    scan = demo_disruption.run_disruption_scan(
        _agrupado(), consentimiento=True, fetcher=_una_falla
    )

    assert len(llamadas) == 2, "una consulta caida corto el resto del escaneo"
    assert scan.corrio is True
    assert scan.report.n_failed == 1
    assert "señal desconocida" in demo_disruption.render_mini_report(
        scan, ts="2026-08-19T00:00:00Z"
    )


# -- REUSO: no se le pide nada nuevo al visitante ------------------------------


def test_los_proveedores_salen_del_archivo_que_ya_se_subio():
    """El insumo es el MISMO export de recepciones de la salida #1: ese archivo
    ya trae la columna de proveedor y `to_deliveries` ya la agrupo."""
    filas, n = demo_disruption.proveedores_de_recepciones(_agrupado())

    assert n == 2
    assert {f.supplier for f in filas} == {"Sellos y Empaques SpA", "Rodamientos del Sur Ltda"}
    # country y annual_spend quedan vacios: el archivo no los trae y deducirlos
    # seria adivinar.
    assert all(f.country == "" and f.annual_spend == 0.0 for f in filas)


def test_un_archivo_sin_proveedores_utilizables_lo_dice(aislado):
    scan = demo_disruption.run_disruption_scan({}, consentimiento=True, fetcher=_Espia())
    assert scan.corrio is False
    assert "ningún proveedor" in scan.motivo
