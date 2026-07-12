# Programa de Partners — para integradores Odoo y consultoras de inventario

> Dos modelos: **rev-share 20%** (referís, nosotros entregamos) o
> **white-label** (tarifa fija mensual, corrés el motor completo bajo tu
> propia marca). Para integradores Odoo, consultoras boutique de supply
> chain, y freelancers con cartera de clientes que necesitan optimización de
> inventario pero no quieren construirla.

## Por qué esto existe

Ya tenés la relación con el cliente — implementaste su Odoo, conocés su
operación, confía en vos. Lo que no tenés (o no querés construir vos mismo)
es un motor de optimización de inventario con 37 herramientas, una capa de
QA que nunca deja salir un número sin fundamento, y un deck ejecutivo listo
para presentar. Eso es lo que aportamos nosotros; la relación comercial y la
firma siguen siendo tuyas.

## Modelo 1 — Rev-share (20%)

Referís al cliente, coordinás el intake (los mismos CSVs de cualquier
paquete Kern — ver [README.md](README.md)), y nosotros ejecutamos el
análisis y entregamos el deck. Vos facturás al cliente el precio de lista
del paquete elegido; nosotros te liquidamos el 20% de cada ciclo mientras el
cliente siga activo, sin límite de tiempo. Cero trabajo de entrega de tu
lado más allá de la relación comercial.

**Para quién:** integradores que quieren ofrecer esto como un servicio más
de su portafolio sin invertir tiempo de entrega propio.

## Modelo 2 — White-label (tarifa fija)

Corrés el catálogo completo bajo tu propia identidad: tu nombre y tu logo en
el **documento ejecutivo consolidado** que le entregás a tu cliente, en vez
de "Kern". Pagás una tarifa fija mensual (se acuerda según volumen
esperado, no es de lista pública) y facturás a tus clientes tus propios
precios.

**Para quién:** consultoras que ya venden "optimización de inventario" como
parte de su propia marca y quieren que el documento que presentan al
cliente lleve su propia identidad, no la de un tercero.

## Cómo se aplica tu marca (branding)

Cada cliente tuyo se carga como un perfil (`src/client_profile.py`) con un
bloque `branding` — tu nombre, URL de tu logo, y (reservado para una
versión futura con reporte HTML/PDF; hoy no se aplica visualmente) tu color
primario en formato `#RRGGBB`. Una vez cargado, el **documento ejecutivo
consolidado** de cada paquete para ese cliente lleva tu identidad en el
encabezado (logo, si lo configuraste) y en el pie ("Preparado por
&lt;tu marca&gt;") en lugar de "Kern" — tanto en el reporte Markdown
como en el Excel. Se resuelve automáticamente perfil por cliente, sin tocar
nada más.

**Alcance actual, léelo antes de prometerle esto a un cliente:** solo el
documento consolidado lleva tu marca hoy — el reporte propio de cada
herramienta individual dentro de la carpeta del paquete (data_quality,
abc_xyz, etc.) todavía sale con la marca Kern por defecto. Si vas a
entregarle la carpeta completa a tu cliente, aclaraselo o entregale
solamente el documento consolidado hasta que el whitelabeling cubra todos
los archivos (mejora pendiente, no todavía construida).

```python
from src.client_profile import upsert_profile
from src.deliverable import Branding

upsert_profile(
    "Acme Consulting", "Acme Consulting",
    branding=Branding(name="Acme Consulting", logo_url="https://acme.example/logo.png",
                       primary_color="#1F4E79"),
)
```

Un cliente sin `branding` configurado recibe el deck con la marca Kern
por defecto — no hace falta optar explícitamente por "sin marca".

## Cómo arrancamos

1. Nos escribís (ver el email de contacto en `https://linchpin.fly.dev/paquetes`)
   contándonos qué modelo te interesa y tu volumen aproximado de clientes.
2. Acordamos por escrito: modelo (rev-share 20% o tarifa fija white-label,
   con su monto), y quién factura a quién.
3. Cargamos tu perfil de partner con tu `branding` (ver arriba) — lo hacemos
   nosotros la primera vez; después es autoservicio si preferís.
4. Corrés tu primer cliente de prueba con `examples/run_package.py` (o pedís
   que lo corramos nosotros) y revisás el deck antes de presentarlo.

No hay contrato de exclusividad ni mínimo de clientes para arrancar.
