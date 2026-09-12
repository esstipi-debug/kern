# Catalogo ISO de operaciones y cadena

53 normas (paquete `iso-operaciones-cadena` 1.0.0). Kern las **carga** y declara,
ficha por ficha, cual puede sostener con datos de cadena. No certifica.

**El catalogo no fue escrito para este motor.** Su cabecera dice
`producto: "KERN IoP"` y sus seis modulos (SMARTPRO, MONITOR, ENERGY, huella,
CMMS, CEI) son instrumentacion de planta: sensores de maquina y medidores de
kWh. Kern no ingesta ninguna de esas magnitudes por conector ni por contrato de
columnas — ni kWh, ni tiempo de ciclo, ni scrap en cantidad, ni molde u
operario. Por eso `kern.cobertura` se reclasifico contra lo que este repo
realmente mide: las normas de piso quedaron `fuera-de-alcance` y las de cadena
que si tienen motor subieron a `nativo`. El trinquete que impide que eso vuelva
a torcerse vive en `tests/test_iso_catalog.py`.

Fuente: `catalogo.json`. Lector: `src/iso_catalog.py`. HTTP: `GET /api/iso`,
`POST /api/detectar`, `GET /api/iso/orden`. Pagina: `GET /iso` (catalogo
filtrable por modulo y dominio SCM + detector de planta + evidencia de orden).
Agente: tool `iso_ops` (`jobs/iso_ops_job.py`) — detectar, proponer tareas de
piso, evaluar evidencia de orden. Siempre OPTIONS o HANDOFF. No certifica.

## Claims permitidos

- Si: "KERN tiene evidencia para 8000-1 / 10004 / 2859-1 / 37500 / 22316 / 31000".
- Si: "14083 aplica; falta t·km".
- No: "la planta cumple ISO …".
- No: "KERN tiene evidencia de piso para 9001 / 50001 / 14067". Era el claim del
  paquete original y no se sostiene: sale de sensores que este motor no lee.
- No: pegar el texto oficial de un PDF ISO.

`norma.kern.limite` viaja siempre con la tarjeta. Un hueco (p. ej. 14083 sin
t·km del 3PL) se nombra; no se tapa con OEE.
