# Playbook de Adquisición — Cliente #1 del Diagnóstico de Arranque

> Objetivo: 1 cliente pago del Diagnóstico de Arranque (USD 1.500–2.500) en 30
> días. Complementa [MONETIZATION_BRIEF.md](MONETIZATION_BRIEF.md) (qué vender)
> con el cómo conseguir al primer comprador — el cuello de botella que el
> propio brief identifica ("el riesgo es la adquisición, no la disposición a
> pagar"). No requiere ningún cambio de código: todo corre con
> `documentation/paquetes/diagnostico-arranque.md` y el `excess_obsolete`
> existente.

## 0. Aritmética del funnel

Para un operador solo, con listas pequeñas y dirigidas (no volumen):

```
~40 contactos hiper-dirigidos (listas <=50, ~5 min de research c/u)
  + secuencia de 4 toques (Día 1 / 3 / 10 / 17) + LinkedIn en paralelo
  ->  ~4-8 respuestas  ->  ~3-5 escaneos gratis entregados
  ->  ~2-3 llamadas de resultados  ->  1 cierre
```

Listas de ≤50 hechas a mano promedian ~5,8% de respuesta (vs. ~2,1% en envíos
de 1.000+); la personalización profunda suma ~+52%; parear email + LinkedIn
suma +30-50% adicional. La ventaja de ser un operador solo es precisión, no
volumen.

## 1. El ICP (a quién SÍ)

No "cualquiera con inventario" — busca **señales de dolor visibles**:

| Segmento | Perfil | Señal de que YA tiene el problema |
|---|---|---|
| **DTC/Shopify (EN)** | Marca de producto físico, USD 1-10M, 200-5.000 SKUs, sin planificador de inventario dedicado | Se queja en público de "overstock", "dead stock" o "cash tied up in inventory" |
| **Pyme LatAm/España (ES)** | Distribuidor/retailer/**importador**, USD 1-10M, en Odoo o Excel | Capital atado en stock importado; compra "a ojo" |

El importador es el target más fuerte: su dolor (capital atado) es
exactamente lo que `excess_obsolete` cuantifica.

## 2. El embudo de oferta

```
ESCANEO GRATIS (imán)     ->  DIAGNOSTICO ($1.5-2.5k)   ->  STARTER/GROWTH ($2-4k/mes)
1 archivo (stock.csv)         4 analisis, 2 semanas          ejecucion recurrente
48h -> "$X atrapados"          el plan priorizado
```

**El escaneo:** el cliente manda `stock.csv`
(`product_id, on_hand, daily_demand, unit_cost, days_since_last_sale`); se
corre solo `src/excess_obsolete.py` (ya existe, sin trabajo nuevo) y se
devuelve en <48h: *"tienes $X atrapados en stock muerto/excedido — top 10
SKUs, y el primero que liberaría cash"*. Elimina toda fricción para un
desconocido: sin costo, sin compromiso, con una cifra que duele.

**Ya es ejecutable hoy** (`examples/run_free_scan.py`, reutiliza
`jobs/excess_obsolete_job.py` tal cual — cero lógica nueva):

```bash
# plantilla lista para adjuntar al prospecto
documentation/templates/stock_template.csv

# probar sin datos de cliente
PYTHONPATH=. python examples/run_free_scan.py --demo

# correr sobre el archivo real del prospecto y guardar el mensaje para pegar
PYTHONPATH=. python examples/run_free_scan.py --data prospecto_stock.csv \
    --client "Nombre del prospecto" --out escaneo_prospecto.txt
```

El tracker de prospectos (`documentation/templates/prospect_tracker.csv`)
tiene las columnas de la sección 8 listas para abrir en Excel/Sheets y
empezar a registrar contactos desde el día 1.

## 3. Dónde buscar a los primeros 20 (sourcing pack)

**EN — Upwork** (search strings, logueado, ordenar por nuevo + fixed-price +
client spend >$1k): `inventory management` · `demand planning` ·
`demand forecasting` · `reduce excess inventory` · `dead stock` ·
`overstock` · `inventory optimization` · `Shopify inventory`.

**EN — comunidades** (unirse y escuchar quejas de overstock/cashflow, no
spammear): r/ecommerce, r/shopify, r/smallbusiness · **eCommerceFuel**
(foro privado $7M+ owners — el techo de tu ICP) · **Limited Supply Slack** ·
directorio thehiveindex.com/topics/ecommerce (84 comunidades). Narrativa
prestada: el "cash flow trap" de DTC (Portless).

**Limite honesto (verificado jul 2026):** Upwork bloquea el fetch de posts
individuales a herramientas automatizadas y Reddit/IndieHackers/Slack no
son navegables por busqueda web (devuelve articulos de blog, no hilos de
comunidad) — este tramo requiere sesion logueada humana, no se puede
armar la lista de 20 en automatico como el segmento ES-Odoo. Lo que SI
quedo verificado y listo para usar hoy:

- **Volumen confirmado en vivo:** ~306 jobs abiertos en
  [upwork.com/freelance-jobs/inventory-management](https://www.upwork.com/freelance-jobs/inventory-management/)
  y ~320 en
  [upwork.com/freelance-jobs/demand-planning](https://www.upwork.com/freelance-jobs/demand-planning/)
  (tambien relevante:
  [/freelance-jobs/supply-chain-management](https://www.upwork.com/freelance-jobs/supply-chain-management/)).
  Entra logueado, ordena por nuevo + fixed-price, puja con el Guion A.
- **Gancho narrativo verificado** (para la copy, NO como objetivo de
  outreach - son marcas cotizadas, demasiado grandes para un freelance
  solo): el ciclo de destock 2022-2024 dejo casos publicos y cuantificados
  - Allbirds escribio $30,3M (2023) + $9,1M (2024) en stock descontinuado
    ($419M perdidos en 5 anos sobre $1,24B de ingresos)
  - Solo Stove/Solo Brands: 309 dias de inventario, "el caso ejemplar" de
    sobre-stockeo post-2022
  - FIGS (200 dias), Olaplex (212 dias), BarkBox ($17,4M write-down FY24)
  - Usar como apertura: *"Hasta Allbirds escribio $30M en perdidas por
    overstock en 2023 - no hace falta ser una marca publica para que esto
    te este pasando a menor escala ahora mismo."* (fuente: analisis DIO
    2022-2024, eightx.co)

**ES — Odoo partners (el canal de mayor apalancamiento).** En vez de buscar
clientes finales en frío, subcontratarse a implementadores Odoo que YA tienen
pymes distribuidoras ahogadas en Excel; ofrecer el diagnóstico como su
add-on. Contacto real verificado (jul 2026):

| Partner | Sitio / contacto | Nota de encaje |
|---|---|---|
| **QUBIQ** | qubiq.es · info@qubiq.es · +34 931 797 762 (Barcelona/Madrid/Sevilla/Bilbao/Lisboa) | Gold Partner, foco BI+AI sobre Odoo — 100+ consultores, "Best Odoo Partner Europe 2022" |
| **Cravit Spain** | cravitgroup.es/odoo-espana · formulario en el sitio (contacto: Jaime Jiménez Titos) | Gold Partner, oficina España desde 2022 |
| **Octupus** | octupus.es/contacto | Gold Partner, Pinto (Madrid) + Cuba + India — **especialistas en ecommerce/retail multicanal** (conector propio Amazon/eBay/Shopify/Temu, 250+ integraciones); el mejor encaje temático: sus clientes YA tienen el dolor de SKUs repartidos entre canales |
| **Doodes** (TICSTRATEGY SAS) | doodes.co/contactus · info@doodes.co · +57 315 5324278 (Colombia/Argentina/USA) | Gold Partner, 15+ años, clientes manufactura/retail/logística |
| **Sinova SAS** | sinova.co/contactus · contacto@sinova.co · +57 312 577 0982 (Bogotá) | Silver Partner, 10+ años, foco en facturación electrónica/contabilidad además de ERP |
| **Xmarts** (Grupo Xmarts) | xmarts.com/en/contact · +52 3341647700 (Guadalajara/CDMX/Monterrey) | 100+ personas, presencia en 13 países |

Comunidad adicional: odoo-community.org (OCA).

**Guion D — acercamiento a un partner (no es el cliente final, es tu canal):**

> Hola [nombre] — soy consultor de inventario especializado en motores de
> optimización de supply chain (reorden, EOQ, excedente y obsoletos) que
> corren sobre datos de Odoo. Seguro varios de tus clientes distribuidores/
> retailers todavía manejan el inventario "a ojo" en Excel aparte de Odoo.
> Tengo un diagnóstico de 2 semanas (cuánto cash tienen atrapado en stock
> muerto + plan de reposición priorizado) que puedo correr como partner
> tuyo — white-label o co-branded — sumando valor a tus implementaciones
> sin que tengas que construirlo vos. ¿Vale la pena verlo con algún cliente
> tuyo como piloto?

**ES — marketplaces:** malt.es (tag supply-chain, ~60 perfiles,
€250-550/día) + Malt Strategy.

**LatAm — Workana (verificado jul 2026, priorizar esta):** categoria
dedicada "Inventory Management" bajo Finance and Business, **14 proyectos
activos hoy** -
[workana.com/jobs?skills=inventory-management](https://www.workana.com/en/jobs?skills=inventory-management)
(tambien revisar
[/hire/operations-management](https://www.workana.com/en/hire/operations-management)).
Plataforma real y establecida: fundada en Argentina 2012, 1M+ freelancers,
la marketplace lider de LatAm. Volumen mas chico que Upwork pero con
categoria propia y sesion en espanol - buen complemento al canal Odoo.

**SoyFreelancer (verificado jul 2026, despriorizar):** sin evidencia de
categoria de inventario/supply chain - el catalogo visible es
diseno/redaccion/marketing/desarrollo web, no encaja con el ICP. No vale
la pena invertir tiempo aqui salvo que surja lo contrario.

**Boards fraccionales (alertas permanentes, no la fuente principal):**
gofractional.com/hire/inventory-manager · fractionaljobs.io (Operations).
Registrarse como talento (canal "que-me-contraten"): scmtalent.com, Cast USA.

## 4. Guiones (listos para pegar)

**A) Propuesta Upwork (EN)** — ¡ojo con el ToS! Ver sección 6.

> Hi [name] — I help $1-10M brands turn dead & excess stock back into cash.
> For this I'd start with a fixed-scope 2-week diagnostic: I quantify the
> exact $ trapped in dead/overstocked SKUs, classify your catalog
> (ABC-XYZ), and hand you a prioritized recovery plan — every number
> QA-gated and traceable. Happy to scope a small paid discovery first so
> you see the approach before committing to the full diagnostic. What does
> your current stock data look like?

**B) DM/email en frío (ES)** — fuera de Upwork (LinkedIn, email directo,
partners Odoo):

> Hola [nombre]: ayudo a [distribuidores/marcas] a liberar el efectivo
> atrapado en stock muerto y excedido. Sin compromiso: mándame un archivo
> (stock a mano + demanda diaria por SKU) y en 48h te devuelvo gratis
> cuánto dinero tienes parado y los 10 SKUs que liberaría primero. Si la
> cifra vale la pena, tengo un diagnóstico de 2 semanas que la convierte en
> un plan de recuperación priorizado. ¿Te mando el escaneo?

**C) Seguimiento (día +3, si no responde):**

> [nombre], ¿te sigue interesando el escaneo gratis? Toma 2 minutos de tu
> lado (un CSV) y a veces la cifra sorprende — clientes suelen tener
> 15-30% del inventario en SKUs que no rotan. Sin costo ni compromiso.

**Asunto (subject lines):** 2-4 palabras / 36-50 caracteres, con un número o
personalización — `Quick question, [Name]`, `[Marca] + dead stock`. Evitar
"free"/"gratis" en el asunto (dispara spam); describirlo en el cuerpo.
Cuerpo: 50-125 palabras, soft-CTA ("¿te mando el número?") en vez de "agenda
una llamada".

## 5. La llamada de resultados (cómo cierra)

1. **Muestra el número:** "$X atrapados, [Y]% de tu inventario. Aquí los
   top 10."
2. **Urgencia con UNO:** toma el peor SKU — "esto solo son $Z que podrías
   liberar este mes."
3. **El puente:** "El escaneo es la foto. El Diagnóstico es el plan
   completo — calidad de datos, ABC-XYZ, KPIs financieros y la secuencia de
   qué liberar primero — en 2 semanas, $1.5-2.5k. Cada número trazable y
   con QA."
4. **Ancla de precio:** "$2k para recuperar $X no es gasto, es el mejor ROI
   de tu trimestre."

### Objeciones

| Objeción | Respuesta |
|---|---|
| "Uso ChatGPT + Excel" (la competencia real) | "ChatGPT no pasa QA ni cita la fuente ni deja un plan trazable que tu banco/socio acepte. Yo entrego números que firmo." |
| "No tengo los datos limpios" | "El escaneo corre con un export básico. Si está sucio, el Diagnóstico incluye la auditoría de calidad de datos — es parte del valor." |
| "Eres solo una persona" | "El motor hace el análisis pesado con QA automática; yo pongo el criterio y la firma. Pagas el resultado, no un equipo." |
| "Es caro" | "¿Comparado con los $X parados? El Diagnóstico cuesta una fracción de lo que vas a liberar." |

## 6. Reglas y riesgos (leer antes de escribir el primer mensaje)

- **Upwork — circumvention.** No compartir contacto ni sacar al lead de la
  plataforma antes de un contrato — riesgo de baneo permanente. El escaneo
  gratis **NUNCA se ofrece en Upwork**: ahí se puja al job normal y, si
  hace falta dar prueba, se ofrece un micro-contrato de descubrimiento
  **pago dentro de la plataforma**. El escaneo gratis corre solo en
  outbound propio (email, LinkedIn, tu web).
- **Cold email — cumplimiento por región:**
  - US (CAN-SPAM): sin consentimiento previo, pero dirección postal real +
    opt-out honrado en 10 días hábiles.
  - UE/España (GDPR + LSSI): B2B por interés legítimo (Art. 6.1.f) — sin
    consentimiento previo si se documenta la base y se ofrece opt-out
    siempre.
  - Brasil (LGPD): interés legítimo, pero la ANPD ya fiscaliza envíos
    comerciales desde 2025 (multas hasta 2% de ingresos BR) — documentar
    propósito y origen del dato.
  - México (LFPDPPP): técnicamente requiere consentimiento — incluir
    siempre aviso de privacidad + opt-out.
- **LinkedIn:** tope ~100 invitaciones/semana (~20/día) antes de
  throttling; InMail no cuenta contra ese tope.
- **Cadencia:** 4 emails en Día 1 / 3 / 10 / 17 capturan ~93% de las
  respuestas totales — no pasar de ~5 toques (correlaciona con spam/unsub).
  Enviar martes-jueves, 8am-mediodía hora local del prospecto.

## 7. Primera semana, día por día

- **Día 1:** armar la lista de 20 (Upwork + directos) y dejar lista la
  plantilla `stock.csv` para el escaneo.
- **Día 2-3:** 20 contactos Upwork (propuestas a jobs abiertos).
- **Día 4-5:** 20 contactos directos ES/EN (email/LinkedIn/partners Odoo).
- **Continuo:** entregar cada escaneo en <48h; cada entrega = pedir la
  llamada de resultados.
- **Fin de semana 1:** meta — 1-3 escaneos entregados, 1+ llamada agendada.

## 8. Métricas a registrar

Contactos enviados · respuestas · escaneos entregados · llamadas · cierres.
Si a los 40 contactos no hay cierre, el problema está en el ICP o el
gancho, no en el volumen — ajustar y repetir, no simplemente enviar más.
