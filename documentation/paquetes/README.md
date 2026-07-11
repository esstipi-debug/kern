# Paquetes comerciales — one-pagers de venta

Las 8 secciones de la escalera comercial (fuente de verdad de precio/alcance:
[MONETIZATION_BRIEF.md](../MONETIZATION_BRIEF.md), sección "Estructura de
empaquetado comercial"), todas ya ejecutables de punta a punta. Cada one-pager
está escrito para **enviarse tal cual a un prospecto**:

| # | Paquete | Precio | Cadencia | One-pager |
|---|---|---|---|---|
| 1 | **Diagnóstico de Arranque** | USD 1.500–2.500 único | Sprint de 2 semanas | [diagnostico-arranque.md](diagnostico-arranque.md) |
| 2 | **Starter — Fundamentos de Inventario** | USD 2.000/mes | Mensual, alcance fijo | [starter-fundamentos.md](starter-fundamentos.md) |
| 3 | **Growth — Operación Completa de SC** | USD 4.000/mes | Mensual + QBR trimestral | [growth-operacion.md](growth-operacion.md) |
| 4 | **Scale — Red, S&OP y Mando Ejecutivo** | USD 7.500/mes | Quincenal + S&OP mensual | [scale-red-sop.md](scale-red-sop.md) |
| 5 | **Retainer Ejecutivo Fraccional** | USD 9.000–12.000/mes | Mensual + semanal + SLA | [retainer-ejecutivo.md](retainer-ejecutivo.md) |
| 6 | **Proyecto de Red, Almacén y Operación** | USD 8.000–18.000 único | Proyecto, 4–8 semanas | [proyecto-red-almacen.md](proyecto-red-almacen.md) |
| 7 | **Proyecto de Sourcing y Costo de Importación** | USD 5.000–10.000 único | Proyecto, recurrible trimestral/anual | [proyecto-sourcing.md](proyecto-sourcing.md) |
| 8 | **Sprint de Liquidación** | 10–20% del cash recuperado (piso USD 1.500) | Sprint de 2–3 semanas | [sprint-liquidacion.md](sprint-liquidacion.md) |

Nota: Scale (4) y Retainer Ejecutivo (5) corren el **mismo** catálogo completo
de 35 herramientas — el brief es explícito en que la diferencia es gobierno
(cadencia, escalamiento con SLA), no capacidad analítica.

Nota: el Sprint de Liquidación (8) es la única sección con **precio
contingente** — cobra un % del cash efectivamente recuperado, nunca un monto
fijo por adelantado (calculadora en `src/contingent_fee.py`); ver `--measure`
más abajo para el anexo de cierre real-vs-estimado.

Nota: [partner-odoo.md](partner-odoo.md) no es un 9no paquete de la escalera
— es el programa de partners (integradores Odoo / consultoras), con dos
modelos (rev-share 20% o white-label a tarifa fija) y su propio
`ClientProfile.branding` para que el deck salga bajo la marca del partner en
vez de la de Linchpin (ver `src/deliverable.py`'s `Branding`).

## Para el operador

Cada paquete es **ejecutable de punta a punta** — no es una lista de tools, es un
runner (`scm_agent/packages.py` + `scm_agent/package_specs.py`) que corre todas
las herramientas del paquete en un solo flujo y emite un deck consolidado más el
entregable completo de cada herramienta:

```bash
# checklist de intake para pedirle al cliente
python examples/run_package.py --package scale --checklist

# correr sobre la carpeta de intake del cliente
python examples/run_package.py --package diagnostico --intake intake/acme --client "ACME"

# demo completa con datos sinteticos (sin archivos de cliente)
python examples/run_package.py --package scale --demo

# Sprint de Liquidacion: al cerrar el sprint, medir el recupero real contra
# la estimacion y emitir el anexo de cierre (product_id, quantity, price)
python examples/run_package.py --package liquidacion --intake intake/acme \
    --client "ACME" --measure ventas_post_liquidacion.csv
```

La garantía "QA falla => no hay entregable" se preserva a nivel de **paquete**:
si un solo análisis ejecutado falla su QA, no se escribe ningún archivo. El
procedimiento operativo completo está en el runbook
[RB-9](../operator/04_runbooks.md#rb-9--correr-un-paquete-comercial).

Dos capacidades del catálogo completo (Scale/Retainer) tienen un intake
distinto al resto: `warehouse_layout` (diseño de bodega) no toma un CSV — es
parametrico, se define en una sesión con el cliente (dimensiones de sitio,
edificio, racks, andenes) — y `leadership_chain` (diagnóstico de liderazgo) se
releva **vos** con el cliente vía una autoevaluación breve, nunca se le manda
un CSV en blanco.
