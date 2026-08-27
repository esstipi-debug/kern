"""Construye el corpus de cabeceras sucias que mide `jobs.intake.detect_columns`.

    python scripts/build_intake_headers_corpus.py

Escribe `tests/fixtures/intake_headers_labeled.csv`, que la bateria
`tests/test_intake_headers_bateria.py` lee para sacar dos numeros: cuanta cabecera
detecta la tabla de alias, y cuanta columna que no es ningun campo canonico mapea
igual.

## Que fabrica esto, y que no

Fabrica el INSUMO -- cabeceras -- con su etiqueta correcta al lado. No fabrica
ningun resultado: la puntuacion sale de correr `detect_columns` de verdad contra
cada export. `scripts/build_bateria_datasets.py` lo dice para el suyo y vale
igual aqui: un marcador que sube con datos inventados es peor que uno bajo.

## Por que no lee la tabla de alias

Derivar el corpus de `jobs.intake.ALIASES` daria ~100 % siempre, midiendo la
tabla contra si misma. Es el mismo defecto que `webapp/demo_lead_time_scan.py`
rechaza cuando se niega a leer una columna `lead_time_days` como observacion: la
promesa comparada consigo misma devuelve brecha cero sobre el defecto exacto que
se venia a medir. Este modulo no importa `jobs`, y
`tests/test_intake_headers_corpus.py` lo verifica sobre el AST.

## Procedencia del vocabulario -- leer antes de citar un numero

Las formas de superficie de `VOCABULARIO` son una RECONSTRUCCION de convenciones
de export de ERP y planillas en LatAm (Defontana, Softland, Contpaqi, Siigo,
Tango, Bsale, Nubox y exportaciones a Excel de sistemas propios). **No estan
extraidas de exports reales de cliente**, que es lo unico que dejaria hablar del
mercado. Lo que la bateria mide es, literal, *cobertura contra un corpus
declarado* -- nunca *cobertura de los exports que llegan*.

Cerrar esa distancia pide muestras reales, anonimizadas y con permiso, y ese es
otro trabajo. Mientras no exista, cualquier cifra que salga de aqui viaja con
esta frase pegada. El fixture de `tests/fixtures/matching_labeled.csv` lleva el
mismo aviso por la misma razon.

## Por que el corpus se commitea

Congelado y en git, el marcador es reproducible y el `git diff` muestra cuando
cambio el CORPUS y no solo la tabla. Sin eso, una subida del numero es
indistinguible de haber ablandado la prueba.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

SALIDA = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "intake_headers_labeled.csv"

CABECERA_CSV = ("case_id", "forma", "personalidad", "encabezado", "campo_canonico")


@dataclass(frozen=True)
class Forma:
    """Un formulario de entrada: lo que `detect_columns` recibe en `required`/`optional`."""

    requeridos: tuple[str, ...]
    opcionales: tuple[str, ...]


#: Los dos juegos que `jobs.intake` ya distingue. Se nombran aqui a mano, sin
#: importarlos, para no reintroducir por la puerta de atras la dependencia que
#: este modulo evita: si el corpus siguiera a la tabla, dejaria de medirla.
CAMPOS_POR_FORMA: dict[str, Forma] = {
    "demanda": Forma(
        requeridos=("date", "product_id", "quantity"),
        opcionales=("unit_cost", "lead_time_days"),
    ),
    "recepciones": Forma(
        requeridos=("supplier", "order_date", "receipt_date"),
        opcionales=("lead_time_days", "product_id", "po_id"),
    ),
}


@dataclass(frozen=True)
class Personalidad:
    """Un ERP exporta siempre con las mismas manias, y ahi esta el realismo.

    Un export no mezcla `Fecha Orden` con `fecha_orden` en el mismo archivo: el
    sistema que lo escribe tiene UNA convencion y la aplica entera. Cruzar cada
    eje contra cada otro daria miles de filas irreales; cruzar personalidades
    contra formas da un corpus del tamano que se puede leer a mano.
    """

    id: str
    idioma: str  # es | pt | en
    acentos: bool
    separador: str  # " " | "_" | "" | "-"
    caso: str  # titulo | mayus | minus
    ruido: str  # ninguno | prefijo_tabla | sufijo_indice | espacio_final
    nota: str


PERSONALIDADES: tuple[Personalidad, ...] = (
    Personalidad("p01", "es", True, " ", "titulo", "ninguno", "planilla escrita a mano, acentos puestos"),
    Personalidad("p02", "es", False, "_", "minus", "ninguno", "volcado de vista SQL, ASCII"),
    Personalidad("p03", "es", True, " ", "mayus", "ninguno", "export de ERP con cabecera en mayusculas"),
    Personalidad("p04", "es", False, "", "titulo", "ninguno", "PascalCase de un sistema propio"),
    Personalidad("p05", "es", True, "_", "minus", "sufijo_indice", "export repetido, columnas numeradas"),
    Personalidad("p06", "es", False, "_", "minus", "prefijo_tabla", "vista con alias de tabla adelante"),
    Personalidad("p07", "es", True, " ", "titulo", "espacio_final", "copiado de Excel, espacios sobrantes"),
    Personalidad("p08", "es", False, "-", "minus", "ninguno", "CSV de integracion, guiones"),
    Personalidad("p09", "pt", True, " ", "titulo", "ninguno", "ERP brasileno, acentos puestos"),
    Personalidad("p10", "pt", False, "_", "minus", "ninguno", "volcado ASCII de sistema brasileno"),
    Personalidad("p11", "en", False, "_", "minus", "ninguno", "export en ingles, snake_case"),
    Personalidad("p12", "en", False, " ", "titulo", "ninguno", "export en ingles, Title Case"),
    Personalidad("p13", "es", True, " ", "titulo", "prefijo_tabla", "el caso duro: acentos y alias de tabla"),
    Personalidad("p14", "pt", True, "_", "minus", "sufijo_indice", "brasileno con columnas numeradas"),
)


#: campo canonico -> idioma -> formas de superficie, en minusculas, sin acentos y
#: separadas por espacio. Los acentos y el separador los pone la personalidad.
#:
#: Escrito mirando exports, NO la tabla de alias. Que una forma coincida con un
#: alias existente es esperable -- son el mismo idioma describiendo la misma cosa
#: -- pero ninguna se eligio por estar ahi, y varias no estan a proposito.
VOCABULARIO: dict[str, dict[str, tuple[str, ...]]] = {
    "date": {
        "es": ("fecha", "fecha documento", "fecha emision", "fecha venta", "fec emision"),
        "pt": ("data", "data emissao", "data venda"),
        "en": ("date", "doc date", "invoice date"),
    },
    "product_id": {
        "es": ("codigo", "codigo producto", "cod prod", "codigo interno", "id producto", "cod art"),
        "pt": ("produto", "codigo produto", "cod produto"),
        "en": ("product id", "item code", "stock code"),
    },
    "quantity": {
        "es": ("cantidad", "cant", "ctd", "unidades", "cant vendida", "unid"),
        "pt": ("quantidade", "qtde", "qtd"),
        "en": ("quantity", "qty", "units sold"),
    },
    "unit_cost": {
        "es": ("precio unitario", "costo unitario", "valor unitario", "p unitario", "vlr unitario"),
        "pt": ("preco unitario", "custo unitario", "preco"),
        "en": ("unit price", "unit cost"),
    },
    "lead_time_days": {
        "es": ("plazo entrega", "plazo dias", "tiempo entrega", "dias de entrega", "lt dias"),
        "pt": ("prazo entrega", "prazo dias"),
        "en": ("lead time days", "lead time"),
    },
    "supplier": {
        "es": ("proveedor", "nombre proveedor", "razon social proveedor", "prov", "razon social"),
        "pt": ("fornecedor", "nome fornecedor"),
        "en": ("supplier", "vendor name"),
    },
    "order_date": {
        "es": ("fecha orden", "fecha pedido", "fecha oc", "emision oc", "fecha compra"),
        "pt": ("data pedido", "data emissao pedido"),
        "en": ("order date", "po date"),
    },
    "receipt_date": {
        "es": ("fecha recepcion", "fecha recibido", "fecha ingreso", "fecha guia", "fecha llegada"),
        "pt": ("data recebimento", "data entrega"),
        "en": ("receipt date", "received date"),
    },
    "po_id": {
        "es": ("numero oc", "nro oc", "orden compra", "folio", "documento compra"),
        "pt": ("numero pedido", "num pedido"),
        "en": ("po number", "purchase order"),
    },
}


#: Columnas que un export real trae y que NO son ninguno de los campos canonicos
#: de la forma. Existen para el otro lado del marcador: una tabla que mapea todo
#: a algo saca 100 % de cobertura y es inservible.
#:
#: `total`, `monto total` y `subtotal` estan a proposito. Son el caso con
#: consecuencia: `revenue` es opcional en la forma de demanda y solo existe para
#: recuperar un costo unitario real dividiendo por la cantidad. Si un total de
#: LINEA o de documento entra por ahi, el costo que sale no es el del cliente.
DISTRACTORES: dict[str, tuple[str, ...]] = {
    "es": (
        "total", "subtotal", "monto total", "iva", "neto", "impuesto",
        "bodega", "almacen", "sucursal", "centro costo",
        "vendedor", "usuario", "rut cliente", "nombre cliente",
        "estado", "observaciones", "nota interna",
        "moneda", "tipo cambio", "condicion pago",
    ),
    "pt": (
        "total", "subtotal", "valor total", "icms", "situacao",
        "deposito", "filial", "vendedor", "cliente", "observacao",
        "moeda", "condicao pagamento",
    ),
    "en": (
        "total", "subtotal", "line total", "tax", "status",
        "warehouse", "branch", "salesperson", "customer", "notes",
        "currency", "payment terms",
    ),
}


#: Palabra sin acento -> con acento. Se aplica token a token cuando la
#: personalidad los pone. Sin esto el eje del acento no existiria, y el plegado
#: NFKD de `_norm` -- el arreglo que motivo todo esto -- quedaria sin medir.
ACENTOS: dict[str, str] = {
    # es
    "codigo": "código", "articulo": "artículo", "descripcion": "descripción",
    "recepcion": "recepción", "emision": "emisión", "numero": "número",
    "razon": "razón", "dias": "días", "guia": "guía", "credito": "crédito",
    "telefono": "teléfono", "direccion": "dirección", "categoria": "categoría",
    "minimo": "mínimo", "maximo": "máximo", "condicion": "condición",
    # pt
    "emissao": "emissão", "descricao": "descrição", "preco": "preço",
    "situacao": "situação", "observacao": "observação", "condicao": "condição",
    "deposito": "depósito", "numero_pt": "número",
}

#: Tres por export: con dos, los idiomas con pocas personalidades no alcanzaban
#: a recorrer su lista de distractores entera.
DISTRACTORES_POR_EXPORT = 3

_RUIDO_PREFIJO = "F_"
_RUIDO_SUFIJO = "_1"


def _acentuar(token: str) -> str:
    return ACENTOS.get(token, token)


def _aplicar_caso(tokens: list[str], caso: str) -> list[str]:
    if caso == "mayus":
        return [t.upper() for t in tokens]
    if caso == "titulo":
        return [t[:1].upper() + t[1:] for t in tokens]
    return [t.lower() for t in tokens]


def _aplicar_ruido(encabezado: str, ruido: str) -> str:
    """El ruido de sistema, que es donde vive la mitad de las fallas reales.

    `espacio_final` es inofensivo a proposito: `_norm` lo limpia, y una prueba
    que solo trajera basura letal no diria si el normalizador hace su trabajo.
    `prefijo_tabla` y `sufijo_indice` SI rompen la clave, y por eso estan.
    """
    if ruido == "prefijo_tabla":
        return _RUIDO_PREFIJO + encabezado
    if ruido == "sufijo_indice":
        return encabezado + _RUIDO_SUFIJO
    if ruido == "espacio_final":
        return encabezado.replace(" ", "  ") + " "
    return encabezado


def componer(base: str, p: Personalidad, *, con_ruido: bool) -> str:
    """Forma de superficie -> la cabecera tal como saldria de ese sistema."""
    tokens = base.split(" ")
    if p.acentos:
        tokens = [_acentuar(t) for t in tokens]
    tokens = _aplicar_caso(tokens, p.caso)
    encabezado = p.separador.join(tokens)
    return _aplicar_ruido(encabezado, p.ruido) if con_ruido else encabezado


@dataclass(frozen=True)
class Columna:
    case_id: str
    forma: str
    personalidad: str
    encabezado: str
    campo_canonico: str  # nombre canonico, o "ninguno" para un distractor


def _base_para(campo: str, idioma: str, desplazamiento: int) -> str:
    """Elige la forma de superficie sin azar: rotacion sobre la lista.

    El desplazamiento es el indice de la personalidad mas el de la columna, asi
    que dos personalidades distintas no eligen la misma forma para todo, y la
    eleccion se puede reproducir leyendo el codigo.
    """
    formas = VOCABULARIO[campo].get(idioma) or VOCABULARIO[campo]["en"]
    return formas[desplazamiento % len(formas)]


def _es_ruidosa(indice_columna: int, indice_personalidad: int) -> bool:
    """El ruido no cae en todas las columnas.

    Un export real mezcla columnas limpias con columnas que arrastran el nombre
    del campo en la base. Si cayera en todas, las personalidades ruidosas
    sacarian cero y el marcador seria un promedio de dos poblaciones distintas
    en vez de una medida.
    """
    return (indice_columna + indice_personalidad) % 3 == 0


def construir_corpus() -> tuple[Columna, ...]:
    """Cada personalidad x cada forma = un export sintetico."""
    filas: list[Columna] = []
    # Un contador CONTINUO por idioma, no un modulo sobre el indice de la
    # personalidad. Con el modulo, los idiomas con pocas personalidades solo
    # alcanzaban una ventana de su lista -- `line total` no salia nunca, y es
    # justo un distractor con consecuencia (`line_total` esta en los alias de
    # `revenue`). Caminando la lista de corrido, todos se usan al menos una vez,
    # y `test_todos_los_distractores_se_usan` lo exige.
    tomados: dict[str, int] = dict.fromkeys(DISTRACTORES, 0)
    for i, p in enumerate(PERSONALIDADES):
        for nombre_forma, forma in CAMPOS_POR_FORMA.items():
            case_id = f"{nombre_forma}-{p.id}"
            campos = (*forma.requeridos, *forma.opcionales)
            for j, campo in enumerate(campos):
                base = _base_para(campo, p.idioma, i + j)
                filas.append(
                    Columna(
                        case_id=case_id,
                        forma=nombre_forma,
                        personalidad=p.id,
                        encabezado=componer(base, p, con_ruido=_es_ruidosa(j, i)),
                        campo_canonico=campo,
                    )
                )
            distractores = DISTRACTORES.get(p.idioma, DISTRACTORES["en"])
            for _ in range(DISTRACTORES_POR_EXPORT):
                base = distractores[tomados[p.idioma] % len(distractores)]
                tomados[p.idioma] += 1
                filas.append(
                    Columna(
                        case_id=case_id,
                        forma=nombre_forma,
                        personalidad=p.id,
                        encabezado=componer(base, p, con_ruido=False),
                        campo_canonico="ninguno",
                    )
                )
    return tuple(filas)


def filas_csv(corpus: tuple[Columna, ...]) -> list[tuple[str, ...]]:
    return [
        (c.case_id, c.forma, c.personalidad, c.encabezado, c.campo_canonico)
        for c in corpus
    ]


def escribir(destino: Path = SALIDA) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    filas = filas_csv(construir_corpus())
    # newline="" + \n explicito: sin esto Windows escribe \r\n y el fixture
    # cambia de bytes al regenerarlo en otra maquina, que es justo el ruido de
    # diff que congelarlo venia a evitar.
    with destino.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CABECERA_CSV)
        w.writerows(filas)
    return len(filas)


def main() -> None:
    n = escribir()
    unicos = len({f[3] for f in filas_csv(construir_corpus())})
    print(f"{SALIDA}: {n} columnas, {unicos} cabeceras distintas")
    print(
        "Recordatorio: reconstruccion de convenciones de export, no muestras "
        "reales de cliente. Ver el docstring antes de citar un numero."
    )


if __name__ == "__main__":
    main()
