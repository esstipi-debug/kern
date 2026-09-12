"""Procedencia de libro para un entregable: libro, pagina del PDF y un fragmento corto.

QUE ES ESTO Y QUE NO ES. Existe un indice de lectura aparte (44 libros de supply chain
partidos en 6.929 pasajes con su pagina, repo `kern-lectura`) servido por un MCP stdio.
Un pasaje de ese indice **no es una cita gateada de Kern**: no tiene `node_id`, y
`filter_citations` lo rechazaria con razon. Por eso este modulo va RIO ABAJO del gate,
como seccion de procedencia aparte -- nada de lo que devuelve entra al pool de
candidatas ni al campo `citations`. El grafo L3 sigue siendo la unica fuente de citas.

POR QUE EL TOPE DE LONGITUD VIVE DE ESTE LADO. Los PDF del indice son copias de libgen,
y lo que Kern renderiza es lo que llega al cliente. Kern hoy no tiene ningun tope de
longitud sobre lo que renderiza, asi que el tope lo pone este codigo y no el otro repo:
:data:`MAX_EXCERPT_CHARS`. Y lo pone en el TIPO, no solo en el parser: :class:`Reading`
valida en `__post_init__`, porque el render (``src/deliverable.py``) confia en lo que se
construyo aca y un tope que solo existe en el camino del parser no es una invariante,
es una costumbre.

EL CABLE, MEDIDO A MANO, NO ASUMIDO. La tool contesta un STRING de markdown envuelto en
``structuredContent = {"result": <str>}``, no filas -- por eso `decode_tool_result` de
``mcp_source`` no sirve aca (levanta `McpToolError` sobre un str) y lo unico que se
reusa de ese modulo es :func:`sync_tool_caller`. Peor: ``isError`` es False tambien
cuando no hubo resultados; el servidor devuelve el string ``"(sin pasajes)"`` como exito
y sus fallos como ``"error: <mensaje interno>"``. Traducir esos strings a estado es la
mitad del trabajo: un adaptador ingenuo le embarcaria al cliente ``"error: storage ...
esta corrupto"`` como si fuera un pasaje de libro.

EL PISO DE CALIDAD ES UN CINTURON, NO UNA DUPLICACION. Una corrida real contra el indice
devolvio 3 pasajes y los 3 eran MOBILIARIO: la barra lateral de un blog, un extracto con
el pie de imprenta del maquetador adentro, y un indice onomastico del final de un libro.
El repo del indice ya limpia lo suyo; esto lo vuelve a hacer de este lado porque lo que
Kern renderiza llega al cliente con el nombre de un libro al lado, y esa ultima milla no
puede depender de que otro proceso, en otro repo y en otra maquina, este limpio hoy. Dos
piezas: :func:`_sanear` SACA el mobiliario que viene incrustado en texto bueno (el pie de
imprenta, :data:`_PIE_DE_IMPRENTA_RE`), y :func:`es_inutilizable_como_cita` DESCARTA el
pasaje cuyo contenido ES mobiliario. Que descartar no cueste un pasaje bueno es lo que
paga :data:`HOLGURA_PEDIDO`: se pide de mas en la misma unica llamada.

DISPONIBLE ES LA EXCEPCION, NO LA REGLA. El indice pide ~1,6 GB de RSS y la VM de
produccion tiene 512 MB (``fly.toml``, ``[[vm]] memory = "512mb"``), asi que en prod
esto nunca esta disponible -- y eso NO es un error: se contesta ``available=False`` con
el motivo en una linea. Ningun fallo de lectura se propaga al llamador: un entregable no
se cae porque falto una lectura.
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .mcp_source import sync_tool_caller

#: Tope de copyright del lado de Kern. Deliberadamente MAS CORTO que el del otro repo
#: (600 en `kern_lectura.format.format_chunks`): alla el consumidor es un agente que
#: lee para decidir, aca el consumidor es el entregable que ve el cliente. Los PDF son
#: copias de libgen: lo que sale tiene que ser cita con pagina y fragmento corto, nunca
#: un pasaje largo textual, y el unico lugar donde ese limite se puede hacer cumplir es
#: este (Kern no tiene ningun tope de longitud sobre lo que renderiza).
MAX_EXCERPT_CHARS = 320

#: Tope del slug de la fuente. Misma razon que el de arriba, otra puerta: el slug sale
#: de la cabecera que manda el servidor y nadie lo recorta, asi que sin un maximo
#: ``1. **<900 caracteres>** PDF p.5`` publica 900 caracteres de texto ajeno como si
#: fueran el nombre de un libro. El slug mas largo del indice mide 50.
MAX_SOURCE_CHARS = 120

#: Los dos caracteres que :func:`_sanear` FABRICA cuando no pudo recuperar el original:
#: el ``?`` de ``encode("ascii", "replace")`` y el ``-`` al que colapsa toda la familia
#: de rayas de :data:`_REEMPLAZOS`. Un PDF con el glyph-mapping roto no llega con
#: caracteres raros, llega con ESTOS DOS -- por eso el piso de legibilidad los cuenta a
#: ellos y no "todo lo que no es una letra": ver :data:`MAX_RELLENO_EXCERPT`.
_RELLENO_FABRICADO = frozenset("-?")

#: Piso de legibilidad del extracto: que fraccion de sus caracteres no-blancos puede ser
#: relleno fabricado antes de que el extracto se TIRE (libro y pagina quedan: este modulo
#: ya declara que eso solo es procedencia completa, y el fragmento es el extra).
#:
#: POR QUE EXISTE. El indice tiene al menos un libro con el glyph-mapping destruido, y lo
#: que sale de ahi pasa todas las otras puertas -- slug valido, etiqueta que matchea
#: :data:`_ETIQUETA_RE`, una sola linea, bajo el tope -- y se publica TEXTUAL entre
#: comillas bajo un libro y una pagina REALES: ``"y- - - -up-lk -h-l-m-k-gbl -f-j is one
#: of the most used libraries in -ython (it was created by -es -c-inney in 2008)"``. Una
#: cita que el cliente no puede verificar es peor que no citar, y esta es la peor clase:
#: el propio saneo convierte texto irrecuperable en algo con forma de prosa.
#:
#: EL NUMERO ESTA MEDIDO, NO ESTIMADO: 400 pasajes distintos del indice vivo (15 libros,
#: 22 consultas). Y refuta la metrica obvia -- "proporcion de letras vs relleno" NO
#: separa: el pasaje roto de arriba tiene 82,7% de letras y un listado de codigo
#: perfectamente legible tiene 61,4%. Contando SOLO el relleno fabricado, en cambio:
#:
#:   * los 375 pasajes de los otros 14 libros: p50 = 0,004  p90 = 0,012  p95 = 0,019;
#:   * los 25 del libro roto: min = 0,028, mediana = 0,067, max = 0,206.
#:
#: Con 0,04 se descarta el 92% del libro roto (los tres casos de la muestra puntuan
#: 0,112, 0,117 y 0,136) y el 2,1% del resto -- y de esos 8, 5 son volcados de figuras y
#: formulas ya ilegibles ("suma i?M fi(xi)-R?suma i?M gi(xi)", una tabla de reporte con
#: rayas de cuadricula), no prosa. El corte queda 3,3x arriba del p90 sano y por encima
#: del listado de codigo mas denso que se midio (0,030).
#:
#: HACIA DONDE SE FALLA, A PROPOSITO: tirar de mas cuesta un fragmento y deja libro +
#: pagina; tirar de menos publica basura atribuida a una pagina real. Lo que este umbral
#: NO arregla es el pasaje medio roto (un encabezado destruido pegado a un cuerpo
#: legible, 0,028-0,038): un promedio sobre todo el extracto no lo ve.
#:
#: Y POR QUE ESTO NO VA EN ``Reading.__post_init__``, donde SI vive el tope de longitud:
#: el tope es una invariante del valor y su respuesta correcta es negarse a existir. Esto
#: es un umbral heuristico y su respuesta correcta es DEGRADAR -- tirar el fragmento y
#: dejar la entrada en pie. Un tipo que levanta no puede degradar por su productor: lo
#: obligaria a medir primero con este mismo helper para no comerse la excepcion, o sea la
#: regla viviria igual del lado del parser, mas una trampa.
MAX_RELLENO_EXCERPT = 0.04

#: Lo que el emisor pone cuando NO sabe de que libro salio el pasaje
#: (``format_chunks``: ``c.get("file_path", "?")``). Una entrada asi trae pagina pero
#: no trae libro: es procedencia que el cliente no puede verificar, asi que se descarta
#: igual que una etiqueta invalida.
FUENTE_DESCONOCIDA = "?"

#: Variable de entorno con la ruta ABSOLUTA al interprete del repo de lectura, p. ej.
#: ``C:\\Users\\...\\kern-lectura\\.venv\\Scripts\\python.exe``. Sin ella no se intenta
#: levantar nada: es el caso normal en produccion.
ENV_INTERPRETE = "KERN_LECTURA_PYTHON"

#: Donde escucha el indice cuando corre como app propia, p.ej.
#: ``http://kern-lectura.flycast:8080/mcp``. Gana sobre :data:`ENV_INTERPRETE`.
#:
#: POR QUE HAY DOS CAMINOS. Medido: una consulta al indice pica en 1.595 MB de RSS y
#: Kern en reposo usa 377 MB. Meterlos en la misma VM obliga a pagar 4 GB las 24 horas
#: por un pico que solo ocurre mientras corre un entregable, asi que en produccion el
#: indice vive en su PROPIA app de Fly, que se enciende al primer pedido y se apaga
#: sola. En tu maquina no hay nada que encender: ahi sigue siendo un subproceso.
ENV_URL = "KERN_LECTURA_URL"

#: Como arranca el servidor de lectura: ``<interprete> -m kern_lectura.mcp_server``.
SERVIDOR_MODULO = "kern_lectura.mcp_server"

#: La unica tool que se consume. `query_books` devuelve ademas entidades y relaciones
#: del grafo de ese repo, que compiten con el grafo L3 de Kern; no es lo que se quiere.
TOOL_SEARCH = "search_passages"

#: Los dos strings que el servidor devuelve como EXITO y no son pasajes.
SIN_PASAJES = "(sin pasajes)"
PREFIJO_ERROR = "error:"

_ELIPSIS = "..."

#: Un corte por palabra solo vale si no deja el extracto mucho mas corto que el tope;
#: un pasaje sin espacios (formulas, tablas) se corta duro antes que devolver migajas.
_MIN_FRACCION_PALABRA = 0.6

#: Cuantos eslabones de la cadena de causas entran a la nota. Tres alcanzan para decir
#: que fallo sin convertir la nota en un traceback: la nota es una linea para un humano.
_MAX_CAUSAS = 3

#: Cuanto del stderr del subproceso entra a la nota. El interprete equivocado escribe
#: ahi una sola linea util ("No module named 'kern_lectura'"); un traceback entero no
#: entra en una nota, y el resto del stderr no es de nadie.
_MAX_STDERR_CHARS = 300

#: Centinelas de pagina del otro repo (``kern_lectura.content.PAGE_MARK``). El servidor
#: ya los saca, pero si alguno sobrevive se va ENTERO: dejar "p.83" suelto en el medio
#: de una frase es peor que no tener marca, porque parece parte del texto del libro.
_CENTINELA_RE = re.compile("\u27e6p\\.\\d+\u27e7")

#: Pie de imprenta del maquetador, tal como lo imprime InDesign al pie de cada pagina:
#: ``<archivo>.indd   <pagina> <dd/mm/aa>   <h:mm> AM|PM``, p. ej.
#: ``jac66107_ch22_590-621.indd 596 12/16/16 02:08 PM``. Se SACA del extracto, igual que
#: los centinelas de pagina, en vez de usarse como veredicto sobre el pasaje.
#:
#: POR QUE SE SACA Y NO DESCARTA, MEDIDO. La primera version de este piso lo trataba como
#: motivo de descarte. Contra el indice vivo (2026-09-12, dos consultas distintas sobre
#: ``jacobs-chase-operations-supply-chain-management``), 10 de 11 pasajes de ese libro
#: traen el pie completo: esta impreso en casi todas sus paginas, asi que descartar por
#: el pie borraria un manual entero del corpus -- incluida la solucion de programacion de
#: centros de trabajo de la p.625, que es justamente uno de los pasajes que hay que
#: conservar. El pie no dice NADA sobre si el pasaje sirve; solo ensucia el extracto.
#:
#: LAS CUATRO PARTES VAN JUNTAS a proposito: sobre el corpus entero, exigirlas todas da
#: cero falsos positivos, mientras que un ``.indd`` suelto o una fecha suelta pueden ser
#: texto del libro. Y se saca ANTES de :func:`_cortar`, o el pie se comeria presupuesto
#: del tope de copyright y el cliente leeria menos libro por la misma cantidad de letras.
_PIE_DE_IMPRENTA_RE = re.compile(
    r"\S+\.indd\s+\d{1,5}\s+\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?"
)

#: Un numero de pagina de un indice: entero corto, o rango ("113-115"), y NUNCA un anio.
#:
#: EL ANIO QUEDA AFUERA POR MEDICION, no por prolijidad: "(Coyle, 1977)" tiene
#: exactamente la forma "termino, pagina". Medido: el parrafo de
#: ``operations-supply-chain-management-arxiv`` PDF p.89, que cita tres veces autor-anio
#: entre parentesis, puntua 3 marcas sin la exclusion y 0 con ella -- o sea que sin esto
#: un parrafo academico con cuatro citas entre parentesis llega al umbral solo. El costo
#: es la pagina 1500-2099 de un libro, que en este corpus no existe: el indice mas alto
#: que se vio apunta a la 903.
_PAGINA_DE_INDICE = r"(?!(?:1[5-9]|20)\d{2}(?!\d))\d{1,4}(?:\s*-\s*\d{1,4})?"

#: LA COMA DE UN INDICE LLEVA ESPACIO DETRAS, y esa exigencia no es cosmetica: es lo
#: unico que separa una pagina de indice de un SEPARADOR DE MILES. Sin ella, "$25,240,000
#: Total sales 24,324,000 Total credit sales 18,785,000" es una lista de paginas
#: perfecta ("25, 240, 000"), y eso NO es hipotetico: es
#: ``jacobs-chase-operations-supply-chain-management`` PDF p.50 -- el enunciado de un
#: ejercicio de finanzas, contenido real del libro --, que puntuaba 6 marcas y se
#: descartaba entero. Medido sobre 521 pasajes distintos del indice vivo (2026-09-12, 74
#: consultas): exigir el espacio cambia el veredicto de ESE pasaje y de ningun otro, y
#: deja las 16 paginas de indice reales con exactamente el mismo puntaje que antes.
_COMA_DE_INDICE = r"\s*,\s+"

#: MARCA 1 de indice: una ENTRADA, o sea un termino y su pagina ("Abbott, 510",
#: "Vendor-managed inventory (VMI), 21"). El caracter de antes de la coma tiene que ser
#: una letra o un parentesis de cierre, porque en "Period 1, 2, 3, 4" y en
#: "95, 105, 70, 10" lo que viene antes de la coma es un NUMERO.
#:
#: ESTA MARCA, SOLA, NO DISTINGUE UN INDICE DE UNA ENUMERACION EN PROSA, y conviene
#: decirlo aca en vez de descubrirlo con un pasaje perdido: "ocean, 35 days; air, 5 days;
#: rail, 12 days; truck, 3 days" tiene la misma forma exacta que "Abbott, 510 Accenture,
#: 22". La diferencia no es local -- no esta en ningun caracter alrededor de la coma --,
#: asi que quien decide no es esta marca sino :func:`_marcas_de_indice`, que la cuenta
#: por clausula y contra un umbral con hueco medido a los dos lados.
_ENTRADA_DE_INDICE_RE = re.compile(rf"[A-Za-z)]{_COMA_DE_INDICE}{_PAGINA_DE_INDICE}")

#: MARCA 2 de indice: la LISTA de paginas de un mismo termino, tres o mas separadas por
#: coma ("5, 6, 23, 24, 113-115, 159"). Hace falta ademas de la marca 1 porque hay
#: indices que ponen muchas paginas por termino y pocos terminos por pasaje (Liu PDF
#: p.383): ahi las entradas son pocas y lo que abunda son las listas.
_LISTA_DE_PAGINAS_RE = re.compile(
    rf"(?<!\d){_PAGINA_DE_INDICE}(?:{_COMA_DE_INDICE}{_PAGINA_DE_INDICE}){{2,}}"
)

#: Donde TERMINA una clausula en prosa y no puede seguir una entrada de indice.
#:
#: POR QUE EL PUNTO Y COMA Y NO EL PUNTO. Un indice SI tiene puntos ("Disney World. See
#: Walt Disney Parks and Resorts", "A. T. Kearney, 673"), asi que cortar por punto
#: partiria indices reales al medio. Punto y coma no tiene ninguno: medido sobre las 16
#: paginas de indice que hay en los 521 pasajes, CERO traen uno, mientras que 25 de los
#: 521 pasajes del corpus si -- o sea que la condicion no es vacua, simplemente no se
#: cumple nunca dentro de un indice. Una enumeracion en prosa, en cambio, es justo donde
#: vive: "material, 12 dollars; labor, 8 dollars; overhead, 4 dollars; freight, 2
#: dollars" son cuatro marcas de indice repartidas en cuatro clausulas de una sola frase.
_SEPARADOR_DE_CLAUSULA_RE = re.compile(r";")

#: Cuantas marcas convierten un extracto en el indice del final de un libro.
#:
#: POR QUE SE CUENTAN MARCAS Y NO SE BUSCA UNA FIRMA. La PRIMERA version de este piso
#: buscaba UNA firma -- un termino seguido de cuatro o mas paginas con coma -- y estaba
#: calibrada contra un solo pasaje. Medido contra el indice vivo, esa firma fallaba por
#: los dos lados: no reconocia el estilo DOMINANTE del corpus ("termino, pagina termino,
#: pagina", una pagina por termino, que es como indexan Heizer, Chopra, Ivanov, Vollmann,
#: Fleisher y Jacobs), y en cambio marcaba contenido real, porque su "termino" admitia
#: numeros adentro y entonces "Period 1, 2, 3, 4" era un termino con su lista de paginas.
#: Lo que de verdad distingue a un indice no es una firma sino la REPETICION: una entrada
#: suelta o una lista suelta aparecen en prosa normal, veinte no.
#:
#: LA OBVIA SIGUE REFUTADA. Densidad de numeros sueltos por palabra NO sirve: los tres
#: extractos con mas numeros por palabra del corpus son contenido valioso (el MPS de
#: capacidad de Vollmann, la solucion de centros de trabajo de Jacobs & Chase y una serie
#: de ordenes de cliente) y un umbral por densidad los tiraria a los tres. Lo que
#: discrimina es la FORMA de la coma, no cuantos numeros hay.
#:
#: LA MARCA SOLA NO ALCANZA, Y ESTE ES EL ERROR QUE COSTO LA RONDA 2. Contar marcas sin
#: mirar nada mas TIRA PROSA DE LIBRO: cuatro pares "<palabra>, <numero>" en cualquier
#: parte del extracto llegaban al umbral y el pasaje se descartaba ENTERO, en silencio.
#: Corrido contra el codigo de la ronda 1, se iban estos seis, todos contenido legitimo:
#:
#:   * "ocean, 35 days; air, 5 days; rail, 12 days; truck, 3 days" dentro de un parrafo
#:     de 312 caracteres sobre seleccion de modo de transporte -- los otros 250
#:     caracteres de prosa buena no lo rescataban, porque la cuenta era absoluta;
#:   * "material, 12 dollars; labor, 8 dollars; overhead, 4 dollars; freight, 2 dollars";
#:   * "8 pallets, 2 cases, 3 totes, 5 drums, 7 bags of raw material";
#:   * "3 shifts, 12 lines, 48 workstations, 96 operators, 200 changeovers per week";
#:   * "8 CUMPLE, 2 PARCIAL, 3 NO_CUMPLE, 2 SIN_DATO, 5 FUERA_DE_ALCANCE";
#:   * "X12 856 (ASN), 214 (estado), 990 (tender), 850 (PO), 810 (factura)".
#:
#: Y NO ES UN PROBLEMA DE REGEX: "Abbott, 510 Accenture, 22" y "8 pallets, 2 cases" son
#: la MISMA cadena de simbolos. Lo que sigue a la coma es un numero en los dos casos, y
#: lo que sigue al numero es una palabra en los dos casos. No hay ningun caracter
#: alrededor de la coma que los separe, asi que ninguna marca mas afilada arregla esto:
#: la unica evidencia disponible es GLOBAL -- cuantas hay, y si conviven con prosa.
#: (Se midieron dos candidatas globales mas y las dos se descartaron: el orden alfabetico
#: de los terminos, que es la propiedad que DEFINE un indice, no separa -- los indices
#: reales van de 0,50 a 0,89 de pares ordenados y la prosa llega a 0,67, porque un indice
#: con subentradas reinicia el abecedario y una enumeracion corta se ordena de casualidad
#: --, y la dispersion de las marcas a lo largo del extracto tampoco, por lo mismo.)
#:
#: LAS DOS CONDICIONES QUE SI SE AGREGARON, cada una medida aparte: el espacio obligatorio
#: despues de la coma (:data:`_COMA_DE_INDICE`, que separa una pagina de un separador de
#: miles) y el conteo POR CLAUSULA (:data:`_SEPARADOR_DE_CLAUSULA_RE`, que es lo que
#: desarma las dos primeras de la lista de arriba: un indice no tiene punto y coma, una
#: enumeracion en prosa casi siempre si).
#:
#: EL CINCO ESTA MEDIDO Y CAE EN UN HUECO VACIO DE DOS ENTEROS. 521 pasajes distintos del
#: indice vivo (74 consultas, 2026-09-12; 34 buscando back-matter y 40 de contenido
#: normal), con las dos condiciones de arriba ya puestas:
#:
#:   * 16 pasajes puntuan de 5 a 12, y los 16 son paginas de indice de verdad, repartidas
#:     en cinco libros: Fleisher x7, Vollmann x4, Jacobs x2, Heizer x2, Ivanov x1;
#:   * los otros 505 puntuan 0, 1 o 2;
#:   * NADIE puntua 3 ni 4.
#:
#: El 3 lo tiene un solo pasaje conocido, y es contenido: la tabla de evaluacion de
#: consultoria de Jacobs & Chase PDF p.718-719 ("Customer focus 1, 2, 20 Safety,
#: cleanliness, and order 3, 4, 5, 20"). El 4 lo tienen las seis prosas de arriba. O sea
#: que el umbral viejo estaba apoyado EXACTAMENTE sobre el puntaje de la prosa que tiraba;
#: el nuevo se apoya en aire por los dos lados, a un entero de la tabla y a un entero del
#: indice mas pobre que se midio.
#:
#: LO QUE ESTE PISO SIGUE SIN VER, dicho antes de que sorprenda:
#:
#:   * el indice que NO pone coma entre el termino y su primera pagina -- "reorder points
#:     (ROP) 104, 106" (Christopher PDF p.288-289), "Rolex 28-9, 40, 68" (Nagle PDF
#:     p.349) -- puntua 0 a 2 y pasa entero. Se deja pasar MEDIDO: la marca que lo
#:     atraparia ("termino <pagina>, <pagina>", sin la coma) recupera 2 de las 8 paginas
#:     que se escapan y al mismo tiempo empuja la tabla de Jacobs & Chase de 3 marcas a 6,
#:     o sea DESCARTA contenido verificado. Perder un indice le cuesta al cliente un
#:     pasaje; perder la tabla le cuesta el pasaje que SI queria;
#:   * y, por el otro lado, una enumeracion en prosa de CINCO items o mas metida en una
#:     sola clausula sigue cayendo. No esta arreglado, esta corrido: cinco items sin un
#:     punto y coma en 320 caracteres es mas raro que cuatro, y ninguno de los 521 pasajes
#:     medidos lo tiene, pero la forma existe y este piso no la distingue de un indice.
#:
#: HACIA DONDE FALLA: un descarte cuesta el pasaje ENTERO (libro y pagina incluidos, ver
#: :func:`es_inutilizable_como_cita`), no solo el fragmento -- por eso el umbral se
#: eligio con hueco y no pegado al maximo del contenido, y por eso existe
#: :data:`HOLGURA_PEDIDO`, que repone lo descartado en la misma unica llamada.
MIN_MARCAS_DE_INDICE = 5

#: Donde ARRANCA la navegacion de un sitio web. El corpus no es solo PDF de libros: trae
#: paginas raspadas (guias ISO, normas de industria), y un chunk que toca el cromo de la
#: pagina trae la barra lateral entera. El hallazgo real fue exactamente eso: uno de los
#: tres pasajes de una corrida era la barra lateral de un blog.
#:
#: SE BUSCA EN TODO EL EXTRACTO, no al principio: en el caso real
#: (``sop-isotracker-iso-9001-document-review`` PDF p.2) el extracto ARRANCA con la cola
#: de un parrafo de verdad ("advice on how often to review documents for ISO 9001.") y la
#: navegacion empieza recien en el medio. Una regla que solo mire el comienzo no lo ve.
#:
#: QUE LO MEDIRIA: buscar cada marcador sobre los 6.929 extractos y mirar cuantos de los
#: marcados son prosa de libro. Todos son frases de cromo de varias palabras; el unico
#: con riesgo real en un corpus de calidad y cumplimiento es "privacy policy", y la
#: consulta que mas directamente lo busca en prosa ("the organization shall maintain a
#: privacy policy...") no devolvio ni un pasaje de libro que lo contenga.
#:
#: LOS DOS ULTIMOS SALEN DE UNA CORRIDA REAL, no de la lista original: con solo los seis
#: primeros, ``read_passages(["iso 9001 document review", "document control", "quality
#: manual"], k=3)`` contra el indice vivo seguia devolviendo dos pasajes que son el
#: encabezado de una pagina web ("Skip to main content Skip to footer info@... Call: 0333
#: ... Search About Us Services"). Un enlace de salto es una ayuda de accesibilidad del
#: HTML: no existe en la prosa de ningun libro, asi que su riesgo de falso positivo es
#: nulo y su cobertura es toda la cabecera de una pagina raspada, que es donde el cromo
#: empieza de verdad.
NAVEGACION_DE_SITIO = (
    "recent posts",
    "related posts",
    "share this post",
    "subscribe to our",
    "privacy policy",
    "cookie policy",
    "skip to main content",
    "skip to content",
)

#: Un token cuenta como palabra si trae al menos una letra. Un numero suelto, un simbolo
#: o una raya de cuadricula no son palabras, y son justo lo que sobra en el mobiliario.
_TIENE_LETRA_RE = re.compile(r"[A-Za-z]")

#: Cuantas palabras necesita un extracto para poder ser una cita.
#:
#: DE DONDE SALE EL TRES. Lo que se publica es el extracto ENTRE COMILLAS con el libro y
#: la pagina al lado, o sea una AFIRMACION atribuida. La afirmacion mas corta posible
#: necesita sujeto, verbo y complemento: tres palabras. Con dos o menos lo que queda
#: entre comillas no es una frase sino un rotulo, y los rotulos que de verdad sobreviven
#: al saneo en este corpus puntuan todos uno o cero: "FIGURE 10.18", "480 PART 3",
#: "Chapter 7 133", "206 21 2". El pie de imprenta, ya sacado, puntua dos.
#:
#: HACIA DONDE FALLA, A PROPOSITO: bajo. Tres palabras es un piso de forma, no de
#: contenido -- no pretende juzgar si la frase dice algo, solo que sea una frase. Subirlo
#: empezaria a comerse citas cortas legitimas ("Corto y entero." puntua tres).
MIN_PALABRAS_CITABLES = 3

#: Cuantos pasajes de mas se le piden al servidor para que descartar no cueste un slot.
#:
#: POR QUE EXISTE. Sin holgura, el piso de calidad convierte un pasaje de mobiliario en un
#: pasaje MENOS: la corrida real que motivo todo esto pidio 3 y los 3 eran mobiliario, asi
#: que el cliente se habria quedado con cero en vez de con tres.
#:
#: POR QUE ESTE NUMERO. Es el tamanio de UN pedido del orchestrator
#: (``scm_agent/orchestrator.py``, ``_LECTURAS_POR_DECK`` = 3): la holgura repone un
#: pedido entero, que es exactamente lo que esa corrida perdio.
#:
#: POR QUE NO MAS. Bajar en el ranking del vector no es gratis y esta medido en
#: ``scm_agent/packages.py``: cuanto mas se estira una consulta sin tema, mas back-matter
#: aparece. Duplicar ``k`` para "asegurarse" traeria justo la cola del ranking, que es
#: donde vive el mobiliario. La holgura compra margen contra un par de piezas de
#: mobiliario y NO intenta rescatar una consulta entera que apunto mal: ese caso tiene que
#: volver corto, y corto es una respuesta honesta.
HOLGURA_PEDIDO = 3

#: Entrada del markdown: ``N. **slug** ETIQUETA``.
_ENTRADA_RE = re.compile(r"^\s*\d+\.\s+\*\*(?P<source>.*?)\*\*(?P<label>.*)$")

#: Las UNICAS etiquetas que el servidor produce (``kern_lectura.format``): "PDF p.83",
#: "PDF p.397-399" o "sin pagina". Se valida en vez de tomarse tal cual porque la
#: etiqueta es resto-de-linea: sin este filtro ``1. **slug** esto es texto del libro``
#: sale como procedencia FABRICADA -- una pagina que el cliente no puede verificar --
#: y ademas es la unica parte de la entrada que no pasa por ningun tope de longitud.
_ETIQUETA_RE = re.compile(r"^(?:PDF p\.\d{1,6}(?:-\d{1,6})?|sin pagina)$")

#: No-ASCII que llega en los extractos, con su equivalente legible. Va ANTES de la
#: normalizacion Unicode porque ninguno de estos se descompone solo: NFKD deja la raya
#: y las comillas curvas intactas, y convierte el micro en mu griega sin hacerla ASCII.
#: La matematica se escribe con su nombre en vez de perderse como "?": un extracto que
#: dice "sigma = 4" se sigue entendiendo, uno que dice "? = 4" no.
_REEMPLAZOS = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ",
    "\u2022": "-", "\u00b7": "-", "\u2043": "-",
    "\u00b5": "mu", "\u03bc": "mu", "\u03c3": "sigma", "\u03a3": "Sigma",
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b4": "delta",
    "\u0394": "Delta", "\u03b5": "epsilon", "\u03b8": "theta", "\u03bb": "lambda",
    "\u03c0": "pi", "\u03c1": "rho", "\u03c4": "tau", "\u03c6": "phi",
    "\u03c7": "chi", "\u03c9": "omega", "\u03a9": "Omega",
    "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u00d7": "x", "\u00f7": "/",
    "\u221a": "raiz", "\u2211": "suma", "\u221e": "infinito", "\u00b1": "+/-",
}


def _es_una_sola_linea(valor: str) -> bool:
    """True si el texto entra en UNA sola linea.

    Se compara contra ``splitlines`` en vez de buscar un caracter concreto porque ese
    metodo ya conoce TODOS los cortes que Python -- y los renderers de markdown --
    tratan como fin de linea (LF, CR, CRLF, tabulador vertical, salto de pagina, NEL,
    separador de linea Unicode), incluido el que viene pegado al final: un texto
    terminado en salto se parte igual, y ``splitlines`` lo delata porque devuelve la
    lista ya sin ese salto.
    """
    return not valor or valor.splitlines() == [valor]


@dataclass(frozen=True)
class Reading:
    """Un pasaje de libro como PROCEDENCIA, no como cita gateada.

    ``page_label`` se conserva TEXTUAL ("PDF p.83-84", "sin pagina") y nunca se
    convierte en "p.83" pelado: es la pagina FISICA del archivo PDF y el folio impreso
    del libro difiere por obra (Vandeput: 26 paginas de corrimiento). Una cita que el
    cliente falsea al abrir el libro impreso es peor que no citar.

    POR QUE VALIDA EN VEZ DE CONFIAR. Este tipo es publico y lo renderiza
    ``src/deliverable.py``, que es la ultima milla antes de que texto de un PDF ajeno
    llegue al cliente, y que NO aplica ningun tope propio -- a proposito: un segundo
    recorte del lado del render seria una segunda verdad sobre cuanto se puede copiar.
    Entonces la unica verdad, :data:`MAX_EXCERPT_CHARS`, se hace cumplir aca, en el
    borde donde el valor se construye. Hoy el unico productor es :func:`parse_readings`,
    que ya corta y sanea, asi que esto no cambia ninguna conducta viva: fija la
    invariante para el proximo productor. Sin esto,
    ``Reading("libro", "PDF p.1", "x" * 5000)`` publica 5.000 caracteres entre comillas.

    Las tres reglas son las que :func:`parse_readings` ya garantiza, y cada una tapa una
    forma distinta de mentir:

    * **una sola linea** en los tres campos -- un salto parte la vinneta del markdown y
      deja prosa suelta que ya no cuelga de ningun libro ni de ninguna pagina, con la
      comilla de cierre colgando en otro renglon;
    * **libro y pagina no vacios** -- media procedencia no se inventa (el extracto SI es
      opcional: libro + pagina ya es procedencia completa, el fragmento es el extra);
    * **los dos topes de copyright** -- el del extracto y el del slug, que son los que
      evitan publicar texto ajeno largo como cita o como nombre de libro.

    Levanta ``ValueError``. No rompe el "nunca levanta" del modulo: lo que se construye
    del cable pasa por :func:`parse_readings`, que corta, sanea y descarta antes de
    llegar hasta aca.
    """

    source: str
    page_label: str
    excerpt: str

    def __post_init__(self) -> None:
        for campo, valor in (("source", self.source), ("page_label", self.page_label),
                             ("excerpt", self.excerpt)):
            if not isinstance(valor, str):
                raise ValueError(f"Reading.{campo} tiene que ser str, llego {type(valor).__name__}")
            if not _es_una_sola_linea(valor):
                raise ValueError(
                    f"Reading.{campo} trae un salto de linea: una entrada de procedencia "
                    "es UNA linea, o el texto se desprende del libro que lo respalda"
                )
        if not self.source.strip() or not self.page_label.strip():
            raise ValueError("Reading sin libro o sin pagina: media procedencia no se cita")
        if len(self.excerpt) > MAX_EXCERPT_CHARS:
            raise ValueError(
                f"Reading.excerpt mide {len(self.excerpt)} y el tope de copyright es "
                f"{MAX_EXCERPT_CHARS}: hay que cortarlo antes de construirlo (ver `_cortar`)"
            )
        if len(self.source) > MAX_SOURCE_CHARS:
            raise ValueError(
                f"Reading.source mide {len(self.source)} y el tope es {MAX_SOURCE_CHARS}: "
                f"eso no es el nombre de un libro, es texto ajeno publicado como si lo fuera"
            )


@dataclass(frozen=True)
class ReadingLookup:
    """El resultado de una consulta al indice, disponible o no.

    Cuando ``available`` es False, ``readings`` va vacia y ``note`` dice POR QUE en una
    linea que le sirve a un humano. No hay un tercer estado ni una excepcion: el
    llamador siempre recibe esto.
    """

    available: bool
    readings: tuple[Reading, ...]
    note: str


class _RespuestaIlegible(RuntimeError):
    """El servidor contesto algo que este adaptador no sabe leer."""


# --- saneo y corte -------------------------------------------------------------------


def _sanear(texto: str) -> str:
    """Texto imprimible en una consola cp1252, sin centinelas y en una sola linea.

    Cinco pasos, en este orden: sacar los centinelas; sacar el pie de imprenta del
    maquetador (:data:`_PIE_DE_IMPRENTA_RE`, mobiliario del libro que el otro repo ya
    limpia y este vuelve a limpiar por su cuenta); reemplazar lo que no se descompone
    solo (comillas, rayas, griegas); normalizar (ligaduras fl/fi/ff y acentos caen aca);
    y como ULTIMO recurso ``ascii``/``replace``, que degrada a "?" en vez de romper. Al
    final se colapsa el espacio en blanco, lo que ademas junta un extracto que venia
    partido en varias lineas.

    El mobiliario se saca ACA, antes de :func:`_cortar`, para que no se coma presupuesto
    del tope de copyright: un pie de imprenta son ~45 de los 320 caracteres que el
    cliente tiene derecho a leer del libro.
    """
    t = _CENTINELA_RE.sub(" ", texto)
    t = _PIE_DE_IMPRENTA_RE.sub(" ", t)
    for malo, bueno in _REEMPLAZOS.items():
        if malo in t:
            t = t.replace(malo, bueno)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.encode("ascii", "replace").decode("ascii")
    return " ".join(t.split())


def _cortar(texto: str) -> str:
    """El extracto hasta :data:`MAX_EXCERPT_CHARS`, sin partir una palabra si se puede.

    Se aplica DESPUES del saneo a proposito: el reemplazo puede ALARGAR el texto (un
    solo caracter sigma pasa a cinco), asi que un corte previo dejaria el tope mintiendo.
    """
    if len(texto) <= MAX_EXCERPT_CHARS:
        return texto
    presupuesto = MAX_EXCERPT_CHARS - len(_ELIPSIS)
    cabeza = texto[:presupuesto]
    corte = cabeza.rfind(" ")
    if corte >= presupuesto * _MIN_FRACCION_PALABRA:
        cabeza = cabeza[:corte]
    return cabeza.rstrip(" ,;:.") + _ELIPSIS


def _es_ilegible(texto: str) -> bool:
    """True si el extracto es mas relleno fabricado que texto, y no se puede publicar.

    Se mide sobre el extracto YA saneado y cortado, que es exactamente lo que veria el
    cliente: medirlo sobre el crudo seria medir un texto que nadie va a leer. El corte
    ademas ayuda, porque el encabezado destruido de un PDF roto suele estar al principio.

    Un extracto vacio no es ilegible, es ausente: la entrada sigue siendo procedencia
    valida con libro y pagina, y no hay nada que tirar.
    """
    sin_blanco = [c for c in texto if not c.isspace()]
    if not sin_blanco:
        return False
    relleno = sum(1 for c in sin_blanco if c in _RELLENO_FABRICADO)
    return relleno / len(sin_blanco) > MAX_RELLENO_EXCERPT


def _palabras_citables(texto: str) -> int:
    """Cuantos tokens del extracto son palabras (traen al menos una letra)."""
    return sum(1 for token in texto.split() if _TIENE_LETRA_RE.search(token))


def _marcas_en_una_clausula(texto: str) -> int:
    """Las marcas de indice de UN tramo, sin cruzar un punto y coma.

    Las dos marcas se cuentan JUNTAS y por separado del texto (no en una sola pasada
    alternada) porque son evidencia del mismo hecho vista de dos formas: los indices de
    una pagina por termino puntuan casi todo en :data:`_ENTRADA_DE_INDICE_RE`, y los de
    muchas paginas por termino casi todo en :data:`_LISTA_DE_PAGINAS_RE`. Sumarlas es lo
    que hace que un solo umbral (:data:`MIN_MARCAS_DE_INDICE`) sirva para los dos
    estilos; separarlas obligaria a dos umbrales, cada uno calibrado con la mitad de los
    datos -- que es exactamente como se rompio la primera version de este piso.
    """
    return len(_ENTRADA_DE_INDICE_RE.findall(texto)) + len(_LISTA_DE_PAGINAS_RE.findall(texto))


def _marcas_de_indice(texto: str) -> int:
    """El puntaje de indice del extracto: las marcas de la clausula que mas junta.

    POR QUE EL MAXIMO POR CLAUSULA Y NO EL TOTAL. Un indice es un bloque continuo de
    entradas: no tiene punto y coma adentro (0 de las 16 paginas de indice medidas), asi
    que para el todo el extracto es UNA clausula y el maximo es el total de siempre. Una
    enumeracion en prosa es lo contrario: las marcas estan repartidas una por clausula
    ("material, 12 dollars; labor, 8 dollars; overhead, 4 dollars"), y ahi el maximo es 1
    donde el total era 4. Sumar de un lado y del otro de un punto y coma es sumar
    evidencia de dos frases distintas como si fuera de una sola lista.
    """
    return max(
        (_marcas_en_una_clausula(tramo) for tramo in _SEPARADOR_DE_CLAUSULA_RE.split(texto)),
        default=0,
    )


def es_inutilizable_como_cita(excerpt: str) -> bool:
    """True si el extracto es MOBILIARIO y no contenido: no se puede citar, ni con pagina.

    EL PISO DE CALIDAD, Y EN QUE SE DIFERENCIA DEL DE LEGIBILIDAD. El otro piso
    (:func:`_es_ilegible`, :data:`MAX_RELLENO_EXCERPT`) contesta "este fragmento, se
    puede LEER?", y cuando la respuesta es no degrada: la pagina SI tiene texto de verdad
    -- lo que fallo es el glyph-mapping del PDF --, asi que el libro y la pagina quedan en
    pie y el cliente todavia tiene una referencia que puede abrir. Este contesta otra
    cosa: "este pasaje, ES contenido del libro, o es el mobiliario alrededor del
    contenido?". Cuando es mobiliario no hay nada que salvar -- mandar "Liu, PDF p.383"
    es mandar al cliente al indice alfabetico del final, y "isotracker, PDF p.2" a la
    barra lateral de un blog --, asi que quien llama DESCARTA la entrada entera. Por eso
    corre DESPUES del piso de legibilidad en :func:`parse_readings`: un fragmento que ya
    quedo vacio por ilegible es una ausencia, no mobiliario, y no se puede descartar por
    tener cero palabras algo que nunca ofrecio ninguna.

    TRES RAZONES, cada una con su patron medido arriba:

    1. :data:`MIN_MARCAS_DE_INDICE` sobre :func:`_marcas_de_indice` -- el indice del
       final del libro, reconocido por REPETICION de sus dos formas (una entrada
       "termino, pagina" y una lista "pagina, pagina, pagina") DENTRO DE UNA MISMA
       CLAUSULA, y no por una firma unica ni por una cuenta suelta: las dos versiones
       anteriores de este piso fallaron, la primera por buscar una firma que solo tenia
       un libro y la segunda por contar marcas sueltas, que es como se descartaba
       "ocean, 35 days; air, 5 days; rail, 12 days; truck, 3 days";
    2. :data:`NAVEGACION_DE_SITIO` -- el cromo de una pagina web raspada;
    3. :data:`MIN_PALABRAS_CITABLES` -- lo que queda despues de todo eso (incluido el pie
       de imprenta, que :func:`_sanear` ya saco) no alcanza para ser una frase.

    UN EXTRACTO VACIO NO ES INUTILIZABLE, ES AUSENTE -- misma regla que el otro piso. Un
    extracto vacio no afirma nada y deja una referencia abrible; uno de dos palabras sale
    ENTRE COMILLAS afirmando una cita que no dice nada.

    EL ARGUMENTO DEL OTRO LADO, PARA QUE ESTE ESCRITO. Se podria conservar libro + pagina
    con el extracto vacio, que es lo que hace el piso de legibilidad y que el render ya
    soporta. A favor: un descarte es SILENCIOSO -- no deja nota ni rastro --, asi que
    cuando el piso se equivoca (y se equivoco: ver la regresion de la ronda 1 en
    :data:`MIN_MARCAS_DE_INDICE`) el cliente pierde un pasaje bueno entero y nadie se
    entera. Conservar la procedencia acotaria ese dano a perder el fragmento.
    Se descarta igual, y por una razon que el otro piso no tiene: alla el defecto esta en
    el ARCHIVO (la pagina tiene texto de verdad, el PDF lo destruyo) y la referencia sigue
    sirviendo; aca el defecto esta en la PAGINA MISMA, que es el mobiliario. "Liu, PDF
    p.383" no es una referencia degradada, es una referencia EQUIVOCADA: manda al cliente
    al indice alfabetico del final a buscar una teoria que ahi no esta. Publicar media
    procedencia que apunta a ningun lado le cuesta al cliente mas que no publicar nada, y
    ademas ocupa el slot igual, que es justo lo que este piso existe para evitar.

    POR QUE ESTO VIVE DE ESTE LADO. El repo del indice ya limpia el mobiliario antes de
    servirlo. Esto no es duplicacion sino defensa en profundidad: lo que Kern renderiza
    llega al cliente con el nombre de un libro al lado, y esa ultima milla no puede
    depender de que otro proceso, en otro repo y en otra maquina, este limpio hoy.
    """
    texto = (excerpt or "").strip()
    if not texto:
        return False
    if _marcas_de_indice(texto) >= MIN_MARCAS_DE_INDICE:
        return True
    bajo = texto.lower()
    if any(marcador in bajo for marcador in NAVEGACION_DE_SITIO):
        return True
    return _palabras_citables(texto) < MIN_PALABRAS_CITABLES


def _es_linea_de_error(texto: str) -> bool:
    """El servidor reporta sus fallos como texto plano con este prefijo, no como isError."""
    return texto.lstrip().lower().startswith(PREFIJO_ERROR)


# --- parser del markdown -------------------------------------------------------------


def parse_readings(crudo: str) -> tuple[Reading, ...]:
    """Las entradas ``N. **slug** ETIQUETA`` + extracto indentado que trae el markdown.

    Tolerante con lo que FALTA, cerrado con lo que SOBRA. El extracto puede no estar
    (libro + pagina ya es procedencia completa; el fragmento es el extra), venir partido
    en varias lineas o quedarse en el camino por ilegible (:data:`MAX_RELLENO_EXCERPT`),
    y una entrada rota se descarta sola sin llevarse el lote.
    Pero una linea que no es una entrada NI un extracto indentado se tira: pegarla al
    cuerpo de la entrada anterior -- que es lo que hacia la primera version -- le
    atribuye a un libro y a una pagina texto que el servidor no saco de ningun libro
    (un pie de pagina interno, o directamente ``error: <mensaje interno>``).

    Por eso el cuerpo exige indentacion, que es lo que usa el formato real (tres
    espacios), y por eso el lote se CORTA en la primera linea que empieza con
    ``error:``, indentada o no: preferimos perder la cola de un extracto antes que
    embarcarle al cliente el mensaje interno del servidor como si fuera un libro.
    """
    entradas: list[tuple[str, str, list[str]]] = []
    for linea in crudo.splitlines():
        pelada = linea.strip()
        if _es_linea_de_error(pelada):
            break
        m = _ENTRADA_RE.match(linea)
        if m:
            entradas.append((m.group("source"), m.group("label"), []))
        elif pelada and entradas and linea[:1].isspace():
            entradas[-1][2].append(pelada)
        # Cualquier otra linea es texto suelto del servidor: se descarta. NUNCA se pega
        # al cuerpo de la entrada anterior.

    lecturas = []
    for source, label, cuerpo in entradas:
        slug = _sanear(source)
        etiqueta = _sanear(label)
        sin_libro = not slug or slug == FUENTE_DESCONOCIDA or len(slug) > MAX_SOURCE_CHARS
        if sin_libro or not _ETIQUETA_RE.match(etiqueta):
            continue  # media cita no se inventa: sin slug creible o sin pagina creible, no hay entrada
        extracto = _cortar(_sanear(" ".join(cuerpo)))
        if _es_ilegible(extracto):
            # El libro y la pagina SI se conservan: son procedencia completa por si
            # solos. Lo que se tira es el fragmento, que es el extra -- y publicarlo
            # seria ponerle comillas a algo que el cliente no puede verificar.
            extracto = ""
        if es_inutilizable_como_cita(extracto):
            # MOBILIARIO, no contenido: no ocupa un slot ni como libro + pagina. La
            # diferencia con el piso de arriba es cual es el defecto -- alla la pagina
            # tiene texto de verdad y el PDF lo destruyo (la referencia sigue sirviendo),
            # aca la pagina ES el mobiliario (el indice del final, la barra lateral del
            # sitio) y mandarla como referencia es mandar al cliente a una pagina que no
            # habla del tema. Que descartar no cueste un pasaje bueno es lo que resuelve
            # :data:`HOLGURA_PEDIDO` en `read_with_caller`, pidiendo de mas.
            continue
        lecturas.append(Reading(source=slug, page_label=etiqueta, excerpt=extracto))
    return tuple(lecturas)


# --- del cable a un estado -----------------------------------------------------------


def _no_disponible(motivo: str) -> ReadingLookup:
    return ReadingLookup(available=False, readings=(), note=motivo)


def _causas(exc: BaseException) -> list[BaseException]:
    """Del envoltorio a la hoja: las excepciones que de verdad explican el fallo.

    Existe por el modo de falla REAL de este conector. Cuando el interprete de
    :data:`ENV_INTERPRETE` es valido pero no tiene el modulo del servidor -- la
    variable mal seteada, que es el unico modo de falla que existe fuera de produccion
    -- lo que sube no es la causa sino un ``ExceptionGroup`` de anyio cuyo texto es
    "unhandled errors in a TaskGroup (1 sub-exception)". Eso no le dice NADA al humano
    que tiene que ir a arreglar el indice, que es la unica razon por la que la nota
    existe. Los grupos son plomeria y no se renderizan: se baja por ``exceptions[0]``
    hasta una hoja, y de ahi por ``__cause__`` (el ``raise ... from`` explicito).

    No se sigue ``__context__``: eso encadena la excepcion que estaba en vuelo cuando
    se levanto esta, que muchas veces no tiene nada que ver, y la nota pasaria a
    nombrar un culpable equivocado.
    """
    cadena: list[BaseException] = []
    vistos: set[int] = set()
    actual: BaseException | None = exc
    while actual is not None and id(actual) not in vistos and len(cadena) < _MAX_CAUSAS:
        vistos.add(id(actual))
        sub = tuple(getattr(actual, "exceptions", ()) or ())
        if sub:
            actual = sub[0]
            continue
        cadena.append(actual)
        actual = actual.__cause__
    return cadena or [exc]


def _una_linea(exc: BaseException) -> str:
    """La cadena de causas en una sola linea, sin repetir el mismo eslabon dos veces."""
    partes: list[str] = []
    for e in _causas(exc):
        parte = f"{type(e).__name__}: {e}"
        if parte not in partes:
            partes.append(parte)
    return _sanear(" <- ".join(partes))


def _ultimo_grito(errlog: Any) -> str:
    """La ultima linea que el subproceso del servidor escribio por stderr.

    Es el otro eslabon que faltaba. La causa de verdad del modo de falla real
    (``No module named 'kern_lectura'``) no viaja por la excepcion -- del lado del
    cliente solo llega "Connection closed" -- sino por el stderr del subproceso, que
    por defecto va a parar a la consola de Kern y se pierde. Se captura y se pega a la
    nota. Nunca levanta: esto corre dentro del manejo de un fallo.
    """
    try:
        errlog.seek(0)
        lineas = [ln.strip() for ln in errlog.read().splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001 - un stderr ilegible no puede tapar el fallo real
        return ""
    return _sanear(lineas[-1])[:_MAX_STDERR_CHARS] if lineas else ""


def _bloques_de_texto(result: Any) -> str | None:
    textos = [t for t in (getattr(b, "text", None) for b in getattr(result, "content", []) or []) if t]
    return "\n".join(textos) if textos else None


def _texto_del_resultado(result: Any) -> str:
    """El string markdown que devolvio la tool, venga por donde venga.

    Acepta un ``str`` ya decodificado para que un llamador con otro transporte (o un
    test) no tenga que fabricar un ``CallToolResult``.
    """
    if isinstance(result, str):
        return result
    if getattr(result, "isError", False):
        raise _RespuestaIlegible(f"el servidor reporto un error: {_bloques_de_texto(result) or result}")
    estructurado = getattr(result, "structuredContent", None)
    if isinstance(estructurado, dict) and isinstance(estructurado.get("result"), str):
        return estructurado["result"]
    texto = _bloques_de_texto(result)
    if texto is not None:
        return texto
    raise _RespuestaIlegible(f"la respuesta no trae texto: {type(result).__name__}")


def lookup_from_text(crudo: str, *, tope: int | None = None) -> ReadingLookup:
    """Traduce el string del servidor a un estado. Los dos strings que NO son pasajes
    (``"(sin pasajes)"`` y ``"error: ..."``) salen como no disponible con ese mismo
    texto de motivo: jamas se convierten en un `Reading`.

    ``tope`` es cuantos pasajes se queda quien llamo. Existe porque al servidor se le
    piden ``k + HOLGURA_PEDIDO`` (ver :func:`read_with_caller`) y lo que sobrevive al
    piso de calidad puede ser mas que ``k``; recortar ACA y no afuera mantiene UNA sola
    verdad sobre cuantos pasajes hay, porque es el mismo lugar que escribe la nota.
    """
    texto = (crudo or "").strip()
    if not texto:
        return _no_disponible("el servidor de lectura contesto vacio")
    if texto == SIN_PASAJES or _es_linea_de_error(texto):
        return _no_disponible(_sanear(texto))
    lecturas = parse_readings(crudo)
    if tope is not None:
        lecturas = lecturas[:max(tope, 0)]
    if not lecturas:
        return _no_disponible("el servidor contesto algo sin forma de pasaje; no se cita lo que no se pudo leer")
    return ReadingLookup(
        available=True,
        readings=lecturas,
        note=f"{len(lecturas)} pasaje(s) del indice de lectura (procedencia, no cita del grafo)",
    )


def read_with_caller(
    call_tool: Callable[[str, dict], Any],
    keywords: list[str],
    *,
    k: int = 3,
    sources: list[str] | None = None,
) -> ReadingLookup:
    """La consulta, con el transporte en manos del llamador.

    Separado de :func:`read_passages` para que el transporte sea reemplazable (un test
    con un servidor en memoria, o un llamador que ya tiene una sesion abierta) sin
    tocar la traduccion del cable, que es donde estan las trampas.

    SE PIDE DE MAS, EN UNA SOLA LLAMADA. Al servidor se le piden ``k +
    HOLGURA_PEDIDO`` pasajes y se devuelven los primeros ``k`` que pasan el piso de
    calidad (:func:`es_inutilizable_como_cita`). Sin esa holgura, cada pieza de
    mobiliario que el piso descarta seria un pasaje MENOS para el cliente -- la corrida
    real que motivo el piso pidio 3 y los 3 eran mobiliario.

    La holgura viaja en la MISMA unica llamada, no en una segunda: cada consulta levanta
    y baja el servidor de lectura entero (16,8 s con el indice frio, ~1,6 GB de RSS), asi
    que reponer lo descartado con otra llamada costaria mas que todo lo que el piso
    ahorra. Pedir mas filas de un ranking que el servidor ya calculo es gratis.

    EL RESULTADO PUEDE TRAER MENOS DE ``k``, Y ESO NO ES UN ERROR: el servidor devuelve
    lo que tiene (un ``sources`` acotado, un corpus chico, una consulta rara), y el piso
    puede descartar mas que la holgura. Dos pasajes son dos pasajes; la alternativa
    -- rellenar con mobiliario para llegar a ``k`` -- es exactamente el bug que se cierra.
    """
    consulta = " ".join(p for p in (str(x).strip() for x in keywords) if p)
    if not consulta:
        return _no_disponible("no se pidio ninguna palabra clave: no se consulto el indice de lectura")
    argumentos: dict[str, Any] = {"query": consulta, "k": k + HOLGURA_PEDIDO}
    if sources is not None:
        argumentos["sources"] = sources
    try:
        crudo = _texto_del_resultado(call_tool(TOOL_SEARCH, argumentos))
    except Exception as exc:  # ningun fallo de lectura tira el entregable
        return _no_disponible(f"no se pudo leer el indice: {_una_linea(exc)}")
    return lookup_from_text(crudo, tope=k)


@contextmanager
def _http_caller(url: str, timeout_s: float):
    """Abre la sesion contra el indice servido por HTTP y devuelve un ``call_tool``.

    Mismo patron y mismo portal que :func:`_stdio_caller` -- de hecho ``sync_tool_caller``
    ya estaba escrito para los dos casos -- y los imports siguen siendo perezosos por el
    gotcha de arranque en produccion: nada de ``mcp`` a nivel de modulo en la cadena de boot.

    No hay ``errlog`` aca: el servidor es un proceso ajeno y remoto, sus gritos van a sus
    propios logs (``fly logs -a kern-lectura``), no a un descriptor nuestro.
    """
    import httpx
    from anyio.from_thread import start_blocking_portal
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    # El timeout se pone en el cliente HTTP, no en la llamada: el API nuevo no lo toma.
    # `connect` va holgado a proposito -- la app del indice se APAGA cuando nadie la usa,
    # asi que el primer pedido despues de un rato paga el arranque de la maquina antes de
    # que haya con quien hablar.
    with start_blocking_portal() as portal:
        http = create_mcp_http_client(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 15.0))
        )
        cliente = streamable_http_client(url, http_client=http)
        with portal.wrap_async_context_manager(cliente) as (leer, escribir, _):
            sesion_cm = ClientSession(leer, escribir, read_timeout_seconds=timedelta(seconds=timeout_s))
            with portal.wrap_async_context_manager(sesion_cm) as sesion:
                portal.call(sesion.initialize)
                yield sync_tool_caller(sesion, portal)


@contextmanager
def _stdio_caller(interprete: str, timeout_s: float, errlog: Any):
    """Abre el servidor de lectura por stdio y devuelve un ``call_tool`` sincronico.

    Mismo patron que ``mcp_source``: un portal bloqueante de anyio sirve una sesion
    async desde codigo sincronico, y la sesion queda viva mientras dure el ``with`` en
    vez de un loop por llamada. Los imports son perezosos por el gotcha de arranque en
    produccion (nada de `mcp` a nivel de modulo en la cadena de boot).

    ``errlog`` es un archivo real (hace falta un descriptor: va como ``stderr`` del
    subproceso). Redirigirlo no es cosmetica -- por defecto ``stdio_client`` manda el
    stderr del servidor a ``sys.stderr``, donde ademas de perderse para la nota puede
    romper una consola cp1252 -- y es el unico lugar donde aparece por que no arranco.
    """
    from anyio.from_thread import start_blocking_portal
    from mcp import ClientSession, StdioServerParameters, stdio_client

    parametros = StdioServerParameters(command=interprete, args=["-m", SERVIDOR_MODULO])
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(stdio_client(parametros, errlog=errlog)) as (leer, escribir):
            sesion_cm = ClientSession(leer, escribir, read_timeout_seconds=timedelta(seconds=timeout_s))
            with portal.wrap_async_context_manager(sesion_cm) as sesion:
                portal.call(sesion.initialize)
                yield sync_tool_caller(sesion, portal)


def read_passages(
    keywords: list[str],
    k: int = 3,
    sources: list[str] | None = None,
    timeout_s: float = 30.0,
) -> ReadingLookup:
    """Pasajes de libro para acompaniar un entregable. Nunca levanta.

    Sin :data:`ENV_INTERPRETE` -- el caso normal en produccion -- contesta
    ``available=False`` sin intentar levantar nada.
    """
    url = (os.environ.get(ENV_URL) or "").strip().strip('"')
    if url:
        # El indice corre aparte: no hay subproceso que levantar ni stderr que capturar.
        try:
            with _http_caller(url, timeout_s) as call_tool:
                return read_with_caller(call_tool, keywords, k=k, sources=sources)
        except Exception as exc:  # apagado, arrancando, o la red se corto: es un estado
            return _no_disponible(
                f"no se pudo hablar con el indice en {url}: {_una_linea(exc)}"
            )

    interprete = (os.environ.get(ENV_INTERPRETE) or "").strip().strip('"')
    if not interprete:
        return _no_disponible(
            f"ni {ENV_URL} ni {ENV_INTERPRETE} estan seteadas: Kern corre sin indice de "
            f"lectura. La primera apunta al indice servido como app propia; la segunda lo "
            f"levanta como subproceso local"
        )
    if not Path(interprete).is_file():
        return _no_disponible(
            f"{ENV_INTERPRETE} apunta a {interprete}, que no existe: no se levanto ningun servidor de lectura"
        )
    # El try de afuera cubre la captura del stderr del servidor, que es una comodidad y no
    # una precondicion: un TMP no escribible no puede tirar abajo un entregable. El de
    # adentro necesita a `errlog` vivo para contar lo que grito el servidor antes de morir.
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errlog:
            try:
                with _stdio_caller(interprete, timeout_s, errlog) as call_tool:
                    return read_with_caller(call_tool, keywords, k=k, sources=sources)
            except Exception as exc:  # no arranca, se colgo, o la libreria levanto: es un estado
                grito = _ultimo_grito(errlog)
                dijo = f"; el servidor dijo: {grito}" if grito else ""
                return _no_disponible(
                    f"no se pudo hablar con el servidor de lectura: {_una_linea(exc)}{dijo}"
                )
    except Exception as exc:
        return _no_disponible(
            f"no se pudo preparar la lectura: {_una_linea(exc)}"
        )
