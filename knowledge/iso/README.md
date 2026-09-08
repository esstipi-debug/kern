# Catalogo ISO de operaciones y cadena

53 normas (paquete `iso-operaciones-cadena` 1.0.0). Kern las **carga** y
cruza `norma.kern.modulos` con lo que ya mide. No certifica.

Fuente: `catalogo.json`. Lector: `src/iso_catalog.py`. HTTP: `GET /api/iso`,
`POST /api/detectar`, `GET /api/iso/orden`. Pagina: `GET /iso` (catalogo
filtrable por modulo y dominio SCM + detector de planta + evidencia de orden).
Agente: tool `iso_ops` (`jobs/iso_ops_job.py`) — detectar, proponer tareas de
piso, evaluar evidencia de orden. Siempre OPTIONS o HANDOFF. No certifica.

## Claims permitidos

- Si: "KERN tiene evidencia de piso para 9001 / 50001 / 14067 (tramo de proceso)".
- Si: "14083 aplica; falta t·km".
- No: "la planta cumple ISO …".
- No: pegar el texto oficial de un PDF ISO.

`norma.kern.limite` viaja siempre con la tarjeta. Un hueco (p. ej. 14083 sin
t·km del 3PL) se nombra; no se tapa con OEE.
