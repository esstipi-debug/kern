"""`plazo_real.xlsx`: el entregable de la salida #1 en el formato del comprador.

El embudo escribia solo markdown. Quien sube una planilla no lee markdown, y ese
es el momento que 05-GTM-KERN §5 identifica como el que decide si la conversacion
sigue. Estos tests fijan las propiedades que hacen que el archivo sirva y, sobre
todo, las que impiden que MIENTA -- que es el modo de falla caro cuando el
entregable viaja solo hasta el jefe del que lo descargo.

Los cinco grupos, en orden de cuanto duele que se rompan:

1. NO REVIENTA con los datos rotos que un ERP produce de verdad (declarado 0,
   declarado negativo, declarado ausente).
2. NO AFIRMA lo que no midio: 'no medido' nunca se pinta de un color que diga
   'tarde', y la reconciliacion de descartes cuadra contra las filas del archivo.
3. NO EJECUTA lo que trae el archivo del cliente: inyeccion de formula neutralizada.
4. EL GRAFICO EXISTE, es nativo, y sobrevive al autofiltro.
5. ES DETERMINISTA: dos corridas del mismo scan dan el mismo archivo.
"""
from __future__ import annotations

import re
import zipfile

import pytest

pytest.importorskip("openpyxl")
pytest.importorskip("pandas")

import pandas as pd  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from webapp.demo_lead_time_scan import run_lead_time_scan  # noqa: E402
from webapp.demo_workbook import NO_MEDIDO, write_lead_time_workbook  # noqa: E402


def _receipts(supplier: str, declared, n: int = 5, base: int = 0) -> list[dict]:
    """n recepciones que tardan ~34 dias, con el plazo declarado que se pida."""
    return [
        {
            "supplier": supplier,
            "order_date": f"2026-0{1 + i % 3}-0{1 + i % 9}",
            "receipt_date": f"2026-0{2 + i % 3}-1{i % 9}",
            "declared_lead_time_days": declared,
            "product_id": f"SKU-{base + i}",
            "po_id": f"PO-{base + i}",
        }
        for i in range(n)
    ]


@pytest.fixture()
def scan_sano():
    return run_lead_time_scan(pd.DataFrame(_receipts("ACME", 21, 6)))


@pytest.fixture()
def libro(tmp_path, scan_sano):
    path = write_lead_time_workbook(
        scan_sano, tmp_path / "plazo_real.xlsx", dataset_label="recepciones.csv", ts="2026-08-08T00:00:00Z"
    )
    return load_workbook(path), path


# --- 1. no revienta --------------------------------------------------------


@pytest.mark.parametrize(
    "declared,caso",
    [
        (0, "el default de un ERP sin configurar"),
        (-5, "un valor imposible que invertiria el signo"),
        (None, "el campo simplemente no venia"),
    ],
)
def test_un_plazo_declarado_roto_no_rompe_el_libro(tmp_path, declared, caso):
    """Dividir por el declarado sin mirarlo es un ZeroDivisionError esperando."""
    df = pd.DataFrame(_receipts("BUENO", 21, 6) + _receipts("ROTO", declared, 5, base=50))
    scan = run_lead_time_scan(df)
    path = write_lead_time_workbook(
        scan, tmp_path / "p.xlsx", dataset_label="d.csv", ts="T"
    )
    ws = load_workbook(path)["Plazo por proveedor"]
    fila = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "ROTO")
    assert ws.cell(fila, 9).value == NO_MEDIDO, f"{caso}: debe decir no medido, no un porcentaje"


def test_con_qa_fallado_no_hay_libro(tmp_path):
    """Sin brecha que mostrar el libro seria un resultado sin resultado adentro."""
    sin_declarado = pd.DataFrame(_receipts("ACME", None, 6))
    scan = run_lead_time_scan(sin_declarado)
    assert not scan.ok
    with pytest.raises(ValueError, match="QA fallado"):
        write_lead_time_workbook(scan, tmp_path / "p.xlsx", dataset_label="d", ts="T")


# --- 2. no afirma lo que no midio ------------------------------------------


def test_la_reconciliacion_de_descartes_cuadra_con_el_archivo(tmp_path):
    """Descartadas + usadas = filas del archivo. Si no suma, se lee como un bug.

    El proveedor corto es la cuarta causa: se leyo perfecto y aun asi no entro en
    ningun numero porque no junta MIN_DELIVERIES. Antes no se contaba en ningun lado.
    """
    filas = _receipts("ACME", 21, 6) + _receipts("CORTO", 21, 2, base=90)
    df = pd.DataFrame(filas)
    scan = run_lead_time_scan(df)
    path = write_lead_time_workbook(scan, tmp_path / "p.xlsx", dataset_label="d", ts="T")
    ws = load_workbook(path)["Recepciones descartadas"]

    fuera, usadas, archivo = ws["B9"].value, ws["B10"].value, ws["B11"].value
    assert fuera + usadas == archivo == len(filas)
    assert fuera >= 2, "las 2 recepciones del proveedor corto tienen que aparecer"


def test_no_medido_no_se_pinta_de_rojo(tmp_path):
    """En Excel el texto ordena DESPUES que todo numero.

    Una regla `cellIs greaterThanOrEqual 0.1` da TRUE sobre la cadena 'no medido' y
    pinta de rojo justo las celdas sin dato -- afirmando lo contrario de lo que
    dice la leyenda. La regla tiene que preguntar ISNUMBER primero.
    """
    df = pd.DataFrame(_receipts("BUENO", 21, 6) + _receipts("SIN", None, 5, base=50))
    scan = run_lead_time_scan(df)
    path = write_lead_time_workbook(scan, tmp_path / "p.xlsx", dataset_label="d", ts="T")

    with zipfile.ZipFile(path) as z:
        hoja = next(
            z.read(n).decode() for n in z.namelist() if n.endswith("sheet2.xml")
        )
    reglas = re.findall(r'<cfRule type="(\w+)"', hoja)
    assert reglas, "la hoja debe traer formato condicional"
    assert set(reglas) == {"expression"}, f"cellIs pinta texto; se encontro {set(reglas)}"
    for formula in re.findall(r"<formula>([^<]*)</formula>", hoja):
        assert "ISNUMBER" in formula, f"la regla {formula!r} no comprueba el tipo primero"


def test_cada_hoja_declara_lo_que_no_midio(libro):
    wb, _ = libro
    metodo = "\n".join(
        str(c.value) for row in wb["Como leer esto"].iter_rows() for c in row if c.value
    )
    assert "no medido" in metodo
    assert "nunca significa cero" in metodo.lower()
    # Lo que este embudo estructuralmente no puede medir se nombra en vez de
    # aparecer como una columna llena de 'no medido', que se lee como defecto.
    assert "fecha comprometida" in metodo


# --- 3. no ejecuta lo que trae el archivo -----------------------------------


def test_inyeccion_de_formula_neutralizada(tmp_path):
    """El nombre del proveedor sale de un archivo subido por un anonimo."""
    payload = "=cmd|'/c calc'!A1"
    df = pd.DataFrame(_receipts("BUENO", 21, 6) + _receipts(payload, 30, 5, base=50))
    scan = run_lead_time_scan(df)
    path = write_lead_time_workbook(scan, tmp_path / "p.xlsx", dataset_label="d", ts="T")

    ws = load_workbook(path)["Plazo por proveedor"]
    celda = next(
        ws.cell(r, 1) for r in range(2, ws.max_row + 1) if "cmd" in str(ws.cell(r, 1).value)
    )
    assert str(celda.value).startswith("'"), "sin la comilla, Excel lo ejecuta"
    assert celda.data_type != "f", "no puede quedar como celda de formula"


# --- 4. el grafico -----------------------------------------------------------


def test_hay_graficos_nativos_y_no_imagenes(libro):
    """Nativo para que el comprador haga clic y vea el dato. Una PNG no se audita."""
    wb, _ = libro
    assert len(wb["Resumen"]._charts) == 1, "el grafico de la portada ES el entregable"
    con_grafico = sum(1 for ws in wb if ws._charts)
    assert con_grafico >= 3
    assert not any(getattr(ws, "_images", []) for ws in wb), "graficos, no capturas"


def test_el_grafico_no_se_vacia_con_el_autofiltro(libro):
    """openpyxl escribe plotVisOnly=1 por defecto.

    Con eso Excel deja de graficar las filas ocultas -- y el autofiltro de la hoja
    oculta filas enteras, incluidas las del bloque de datos que vive a la derecha.
    El cliente filtra una columna y el grafico se le borra.
    """
    _, path = libro
    with zipfile.ZipFile(path) as z:
        charts = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
        assert charts
        for name in charts:
            assert 'plotVisOnly val="0"' in z.read(name).decode(), name


def test_el_grafico_de_la_portada_entra_en_la_primera_pantalla(libro):
    """Es la razon por la que el archivo existe: no puede pedir scroll horizontal."""
    wb, _ = libro
    ancla = wb["Resumen"]._charts[0].anchor._from
    assert ancla.col <= 5, "anclado a la derecha del bloque de texto = fuera de pantalla"


def test_la_categoria_del_grafico_lleva_el_n(libro):
    """Un titular sostenido por 3 recepciones y otro por 180 no pueden verse igual.

    El n va DENTRO de la categoria para que sobreviva a la captura de pantalla.
    """
    wb, _ = libro
    ws = wb["Resumen"]
    etiquetas = [ws.cell(r, 14).value for r in range(3, ws.max_row + 1) if ws.cell(r, 14).value]
    assert etiquetas and all("(n=" in str(e) for e in etiquetas)


# --- 5. determinismo ---------------------------------------------------------


def test_dos_corridas_del_mismo_scan_dan_el_mismo_libro(tmp_path, scan_sano):
    """Ningun reloj adentro del escritor: `ts` entra por parametro."""
    kwargs = {"dataset_label": "d.csv", "ts": "2026-08-08T00:00:00Z"}
    a = write_lead_time_workbook(scan_sano, tmp_path / "a.xlsx", **kwargs)
    b = write_lead_time_workbook(scan_sano, tmp_path / "b.xlsx", **kwargs)

    def celdas(path):
        wb = load_workbook(path)
        return [(ws.title, c.coordinate, c.value) for ws in wb for row in ws.iter_rows() for c in row]

    assert celdas(a) == celdas(b)


def test_la_recepcion_se_puede_verificar_una_por_una(libro):
    """05-GTM-KERN §5 promete 'verificable en 5 min'. Un promedio no se verifica.

    La hoja de recepciones es lo que convierte el titular en algo que el comprador
    puede ir a buscar en su propio sistema.
    """
    wb, _ = libro
    ws = wb["Recepciones medidas"]
    cabeceras = [ws.cell(4, c).value for c in range(1, 7)]
    assert cabeceras == [
        "Proveedor", "Orden de compra", "SKU", "Fecha de la orden", "Fecha de recepcion", "Dias",
    ]
    assert ws.max_row > 4, "tiene que haber filas de recepcion, no solo el encabezado"
