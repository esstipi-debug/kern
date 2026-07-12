# PLANTILLA — Anexo de Tratamiento de Datos (DPA-lite)

> ## ⚠️ BORRADOR — NO USAR CON UN CLIENTE PAGANDO SIN REVISIÓN LEGAL
>
> "Lite" no significa informal: significa dimensionado para una consultoría
> boutique (no una plataforma SaaS multi-tenant a gran escala), pero cada
> compromiso de este documento debe ser real y verificable contra el código
> — no aspiracional. Cada afirmación de este documento fue contrastada
> contra `SECURITY.md`, `webapp/security.py` y `src/writeback.py` al
> redactarlo; si el código cambia, este documento debe actualizarse en el
> mismo PR (o deja de ser preciso). Cada cláusula marcada
> `[REVISAR CON ABOGADO: ...]` necesita revisión legal antes de firmarse con
> un cliente que paga. Complementa a
> [service-agreement-template.md](service-agreement-template.md).

---

## 1 · Qué datos se procesan

Según el paquete contratado, Linchpin procesa datos operativos y
comerciales que el Cliente suministra, típicamente:

- Maestro de productos (SKU, nombre, código de barras, costo)
- Historial de ventas (fecha, producto, cantidad, precio/costo unitario)
- Stock a mano por SKU
- Datos financieros agregados (COGS, inventario promedio)
- Parámetros de negocio (nivel de servicio objetivo, costo de mantener
  inventario, plazos de reposición)
- Si el paquete incluye *writeback* Odoo: los mismos campos que lee el
  módulo de Odoo (ver la sección "What data is sent, and where" de
  `odoo_addon/linchpin_dry_run/static/description/index.html`) — SKU,
  fecha, cantidad, costo unitario, plazo de entrega del proveedor principal,
  y el nombre de la empresa del Cliente. **El stock a mano nunca se lee ni
  se envía a través de ese módulo específico** (dry-run).

**Estos son datos operativos y comerciales agregados a nivel de producto/
transacción — no están pensados para contener datos personales de
individuos** (empleados del Cliente, sus propios clientes finales, etc.).
Si el archivo que el Cliente suministra contiene datos personales
identificables (por ejemplo, nombre de cliente final en una línea de venta
minorista), es responsabilidad del Cliente anonimizarlos o excluirlos antes
de compartirlos — Linchpin no está diseñado para procesar datos personales
como parte de su flujo normal. Esto es una convención de diseño y una
instrucción interna para quien mantiene el código (ver la nota
correspondiente en `CLAUDE.md`, bajo "Gotchas"), **no un filtro técnico
automático que detecte y bloquee PII** — el motor no tiene lógica de
detección/redacción de datos personales; simplemente lee columnas
específicas por nombre (SKU, fecha, cantidad, costo, etc.), lo que de
hecho reduce el riesgo de leer una columna inesperada, pero no es una
garantía de que un dataset con PII mezclada en esas mismas columnas quede
protegido.

`[REVISAR CON ABOGADO: si el Cliente opera en una jurisdicción con
regulación de datos personales (GDPR, LGPD, leyes locales de protección de
datos), definir si esta cláusula de "responsabilidad del Cliente" es
suficiente o si Linchpin necesita compromisos adicionales — p. ej. actuar
formalmente como "encargado del tratamiento" en vez de solo declarar que no
procesa PII por diseño]`

## 2 · Finalidad del tratamiento

Los datos del Cliente se usan **exclusivamente** para producir los
entregables del paquete contratado. No se usan para ningún otro propósito
sin el consentimiento explícito del Cliente: no se venden, no se comparten
con terceros salvo los subencargados listados en la Sección 3, y no se
usan para entrenar modelos de terceros.

## 3 · Subencargados (terceros que procesan datos en nombre de Linchpin)

| Subencargado | Qué recibe | Cuándo |
|---|---|---|
| **Anthropic (API de Claude)** | Tres usos distintos, ninguno recibe jamás las filas crudas del CSV del Cliente: **(1)** un resumen ya calculado de los resultados del análisis (texto narrativo + título de herramienta + citas bibliográficas), para pulir la redacción o traducir al idioma del paquete (`scm_agent/llm.py::narrative_rewrite`); **(2)** si el enrutamiento automático por palabras clave no encuentra una única herramienta clara para el pedido, el **texto libre del brief que escribió el Cliente/operador** (no datos del CSV), para elegir qué herramienta correr (`scm_agent/intent.py`) — esto es parte del flujo normal, no un caso de error; **(3)** para la herramienta de diagnóstico de liderazgo (`leadership_chain`) específicamente, el **texto libre del brief**, para puntuar las dimensiones del modelo CHAIN (`scm_agent/tools.py`). | Solo si el operador configuró `ANTHROPIC_API_KEY`. Sin esa clave, ninguno de los tres usos se activa — el paquete se genera igual con plantillas de texto determinísticas, sin ningún llamado externo. |
| **Fly.io (hosting)** | La infraestructura donde corre la aplicación web y donde se almacenan temporalmente los entregables generados, si el operador la usa para alojar Linchpin. | Siempre que el despliegue use `linchpin.fly.dev` u otra infraestructura de Fly.io — no aplica a una instalación autoalojada por el Cliente/operador. |

`[REVISAR CON ABOGADO: verificar los términos de tratamiento de datos de
Anthropic y de Fly.io vigentes al momento de la firma, y si hace falta
listar un subencargado adicional según cómo esté desplegada esta instancia
particular de Linchpin]`

## 4 · Transmisión y almacenamiento

- La transmisión hacia el servicio de Linchpin ocurre sobre HTTPS. La
  aplicación en sí **no gestiona certificados TLS ni terminación TLS** —
  eso lo hace el proxy/infraestructura de despliegue (p. ej. la capa de
  borde de Fly.io); confirmar con el operador cómo está configurado el
  despliegue específico antes de afirmar "cifrado en tránsito" como una
  garantía absoluta.
- **No hay ninguna afirmación de cifrado en reposo** en este documento
  porque no está implementado ni verificado en el código — no prometer lo
  que no se puede confirmar.
- El acceso al panel web (`POST /api/jobs`, etc.) puede protegerse con una
  clave (`LINCHPIN_API_KEY`) y con límite de tasa
  (`LINCHPIN_RATE_LIMIT`) — **ambos son opcionales y están apagados por
  defecto** salvo que el operador los configure explícitamente (ver
  `SECURITY.md`). `[REVISAR CON ABOGADO: si el Cliente exige estos
  controles activos como condición del contrato, dejarlo explícito acá y
  verificar que estén configurados antes de firmar, no asumirlo]`.
- El canal de *writeback* Odoo descrito en la Sección 1 **no** usa
  `LINCHPIN_API_KEY` — usa un mecanismo separado, con clave propia por
  cliente (`webapp/mcp_auth.py` + `src/mcp_keys.py`), que **está siempre
  activo, sin opción de apagarlo**: cualquier pedido sin una clave válida
  recibe un rechazo automático. No confundir un control (opcional) con el
  otro (obligatorio) si el Cliente pregunta específicamente por la
  seguridad de la integración Odoo.

## 5 · Retención y borrado

La retención real depende de por qué canal entró el dato — no hay un único
mecanismo, y ninguno de los tres es un borrado automático por antigüedad
salvo el primero:

- **Entregables de un job vía el panel web** (`POST /api/jobs`): se
  purgan automáticamente a la hora de generados (TTL fijo de 3600
  segundos, no configurable — ver `webapp/app.py`).
- **Entregables de una corrida por línea de comandos**
  (`examples/run_agent.py`, `examples/run_package.py`, carpeta
  `deliverables/` por defecto): **no tienen ningún borrado automático** —
  quedan en disco hasta que el operador los borre a mano.
- **Mini-reportes del funnel de demo** (si está en uso, dirección
  configurable vía `LINCHPIN_LEAD_REPORTS_DIR` en producción): contienen
  el email real del lead que completó la demo — **es un dato personal**.
  Este directorio está **deliberadamente excluido** de cualquier borrado
  por antigüedad (es el artefacto durable que el operador revisa después);
  el único límite es un tope duro de cantidad total de directorios
  (`MAX_LEAD_DIRS`), que solo empieza a purgar los más viejos una vez
  superado ese tope — no es un mecanismo de retención por tiempo.

`[REVISAR CON ABOGADO: definir un plazo de retención concreto para cada uno
de los tres casos de arriba y comprometer un proceso de borrado a pedido
del Cliente — hoy es una capacidad operativa manual en los tres casos, no
un compromiso contractual con un plazo definido, y el caso de los leads en
particular retiene PII (el email) sin ningún borrado automático por tiempo.
Si el Cliente pide que se borren sus datos, el operador debe poder hacerlo
a mano y confirmarlo por escrito]`

## 6 · Derechos del Cliente sobre sus datos

El Cliente puede solicitar en cualquier momento: (a) una copia de los datos
que Linchpin tiene almacenados sobre él; (b) la eliminación de esos datos,
sujeto a cualquier obligación legal de retención que aplique. El operador
se compromete a responder a estas solicitudes `[REVISAR CON ABOGADO: definir
un plazo concreto de respuesta — sugerido, alineado con SECURITY.md's plazo
de reconocimiento de vulnerabilidades: no más de X días hábiles]`.

## 7 · Notificación de incidentes

Si Linchpin detecta un incidente de seguridad que afecte los datos del
Cliente, se compromete a notificarlo **sin demora injustificada** una vez
confirmado. `[REVISAR CON ABOGADO: definir un plazo concreto — muchas
regulaciones de protección de datos exigen notificación dentro de 72 horas
de tomar conocimiento; confirmar si aplica y comprometerse a un número
específico, no dejarlo abierto]`.

## 8 · Vigencia

Este anexo tiene la misma vigencia que el
[Acuerdo de Servicios](service-agreement-template.md) al que complementa y
termina junto con él, salvo las obligaciones de confidencialidad y borrado
de datos, que sobreviven su terminación según lo que se defina en la
Sección 6 de este documento.
