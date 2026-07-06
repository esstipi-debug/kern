# Paquetes comerciales — one-pagers de venta

Los tres paquetes de entrada de la escalera comercial (fuente de verdad de
precio/alcance: [MONETIZATION_BRIEF.md](../MONETIZATION_BRIEF.md), sección
"Estructura de empaquetado comercial"). Cada one-pager está escrito para
**enviarse tal cual a un prospecto**:

| Paquete | Precio | Cadencia | One-pager |
|---|---|---|---|
| **Diagnóstico de Arranque** | USD 1.500–2.500 único | Sprint de 2 semanas | [diagnostico-arranque.md](diagnostico-arranque.md) |
| **Starter — Fundamentos de Inventario** | USD 2.000/mes | Mensual, alcance fijo | [starter-fundamentos.md](starter-fundamentos.md) |
| **Growth — Operación Completa de SC** | USD 4.000/mes | Mensual + QBR trimestral | [growth-operacion.md](growth-operacion.md) |

Las otras 4 secciones de la estructura (Scale, Retainer Ejecutivo y los 2
proyectos puntuales) aún no tienen paquete ejecutable ni one-pager — están
definidas solo en el brief.

## Para el operador

Cada paquete es **ejecutable de punta a punta** — no es una lista de tools, es un
runner (`scm_agent/packages.py` + `scm_agent/package_specs.py`) que corre todas
las herramientas del paquete en un solo flujo y emite un deck consolidado más el
entregable completo de cada herramienta:

```bash
# checklist de intake para pedirle al cliente
python examples/run_package.py --package starter --checklist

# correr sobre la carpeta de intake del cliente
python examples/run_package.py --package diagnostico --intake intake/acme --client "ACME"

# demo completa con datos sinteticos (sin archivos de cliente)
python examples/run_package.py --package growth --demo
```

La garantía "QA falla => no hay entregable" se preserva a nivel de **paquete**:
si un solo análisis ejecutado falla su QA, no se escribe ningún archivo. El
procedimiento operativo completo está en el runbook
[RB-9](../operator/04_runbooks.md#rb-9--correr-un-paquete-comercial).
