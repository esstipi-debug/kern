# 07 · Setup de Venta — Stripe, Calendly, y quien firma

> Pasos exactos para que un prospecto pueda ir de `/paquetes` a pagar o agendar
> sin que vos toques GitHub. Todo lo de abajo son acciones de **login humano**
> (crear cuenta, generar un link, pegarlo en `fly secrets set`) — ningún agente
> puede hacerlas por vos. Sin estas variables configuradas, `/paquetes` sigue
> funcionando: cada CTA degrada a un `mailto:` con el asunto prellenado (ver
> [`webapp/offers.py`](../../webapp/offers.py)), así que podés lanzar el sitio
> hoy y completar esto cuando tengas Stripe/Calendly listos.

## 1 · Calendly (una sola llamada "Agendar" para todos los paquetes)

1. Creá una cuenta en [calendly.com](https://calendly.com) (el plan gratuito
   alcanza para un solo tipo de evento).
2. Creá un evento tipo "Llamada de 30 min" o similar.
3. Copiá el link público del evento (ej. `https://calendly.com/tu-usuario/intro`).
4. Configuralo como secreto en producción:
   ```bash
   fly secrets set CALENDLY_URL="https://calendly.com/tu-usuario/intro" --app linchpin
   ```
   En local, para probarlo antes de desplegar: `export CALENDLY_URL=...`.

Sin esta variable, el botón "Agendar una llamada" en cada paquete abre un
`mailto:` con el asunto "Agendar: <nombre del paquete> - Kern".

## 2 · Stripe Payment Links (uno por paquete)

Cada uno de los 8 paquetes tiene su propia variable — **no compartas un solo
link entre paquetes**, porque entonces no sabés cuál se vendió.

**El Sprint de Liquidación (precio contingente) es la excepción:** no tiene un
monto fijo para poner en un Payment Link (se cobra 10-20% de lo que se
recupera, medido al cierre — ver `documentation/paquetes/sprint-liquidacion.md`).
Dos opciones: (a) no configurar `STRIPE_LINK_SPRINT_LIQUIDACION` — el botón
"Pagar / Empezar" degrada a `mailto:` y coordinás el pago por factura al
cierre, la vía recomendada; o (b) configurar un Payment Link de "seña" a
cuenta del honorario final si preferís cobrar un adelanto simbólico.

1. Entrá a tu cuenta de Stripe → **Payment Links** → **New**.
2. Creá un link por paquete, con el precio/cadencia exactos de
   [`documentation/MONETIZATION_BRIEF.md`](../MONETIZATION_BRIEF.md) (no
   inventes precios nuevos acá — si el precio es un rango, ej. "$1.500–2.500",
   arma el Payment Link con el piso del rango y ajustá manualmente en la
   llamada de venta, o usá "Pagar lo que decidas" de Stripe).
3. Para paquetes mensuales (Starter, Growth, Scale, Retainer) usá un Payment
   Link de **suscripción**, no de pago único.
4. El nombre de la variable de entorno es `STRIPE_LINK_<SLUG>`, donde
   `<SLUG>` es el slug del paquete en mayúsculas con `-` reemplazado por `_`
   (ver `Offer.stripe_env_var` en [`webapp/offers.py`](../../webapp/offers.py)):

   | Paquete | Variable |
   |---|---|
   | Diagnóstico de Arranque | `STRIPE_LINK_DIAGNOSTICO_ARRANQUE` |
   | Starter — Fundamentos de Inventario | `STRIPE_LINK_STARTER_FUNDAMENTOS` |
   | Growth — Operación Completa de SC | `STRIPE_LINK_GROWTH_OPERACION` |
   | Scale — Red, S&OP y Mando Ejecutivo | `STRIPE_LINK_SCALE_RED_SOP` |
   | Retainer Ejecutivo Fraccional | `STRIPE_LINK_RETAINER_EJECUTIVO` |
   | Proyecto de Red, Almacén y Operación | `STRIPE_LINK_PROYECTO_RED_ALMACEN` |
   | Proyecto de Sourcing y Costo de Importación | `STRIPE_LINK_PROYECTO_SOURCING` |
   | Sprint de Liquidación (precio contingente, ver nota arriba) | `STRIPE_LINK_SPRINT_LIQUIDACION` |

5. Configurá las que ya tengas listas (podés hacerlo de a una, no hace falta
   completar las 8 el mismo día):
   ```bash
   fly secrets set \
     STRIPE_LINK_DIAGNOSTICO_ARRANQUE="https://buy.stripe.com/xxxxx" \
     STRIPE_LINK_STARTER_FUNDAMENTOS="https://buy.stripe.com/yyyyy" \
     --app linchpin
   ```

Un paquete sin su variable configurada degrada su botón "Pagar / Empezar" a un
`mailto:` con el asunto "Pagar: <nombre del paquete> - Kern" — el
prospecto igual puede escribirte, solo no puede pagar solo todavía.

## 3 · Quién firma (bloque "Quien firma" en `/paquetes`)

Un cliente que va a firmar un retainer de $2-12k/mes quiere saber quién
factura, no solo qué calcula el motor. Configurá:

```bash
fly secrets set \
  OPERATOR_NAME="Tu Nombre" \
  OPERATOR_BIO="2-3 lineas: quien sos, por que confiar en vos con la operacion de inventario del cliente." \
  OPERATOR_LINKEDIN="https://linkedin.com/in/tu-usuario" \
  OPERATOR_EMAIL="vos@tudominio.com" \
  --app linchpin
```

`OPERATOR_PHOTO_URL` es opcional pero **debe ser una ruta same-origin**
(`img-src` en la Content-Security-Policy del sitio es `'self' data:`, no
permite cargar imágenes de un dominio externo — ver
[`webapp/security.py`](../../webapp/security.py)). Subí tu foto a
`webapp/static/operator/` en un PR y usá una ruta relativa:
```bash
fly secrets set OPERATOR_PHOTO_URL="/static/operator/foto.jpg" --app linchpin
```

Sin `OPERATOR_NAME`/`OPERATOR_BIO`, el bloque muestra placeholders
`TODO-OPERADOR` visibles — reemplazalos antes de mandar el link a un
prospecto real.

## 4 · Verificar en producción

Después de cualquier `fly secrets set`, Fly reinicia la app sola (no hace
falta `fly deploy`). Confirmá que tomó los valores:

```bash
curl -s https://linchpin.fly.dev/paquetes | grep -o 'mailto:[^"]*' | head -3
```

Si un paquete que ya configuraste sigue apareciendo con `mailto:`, revisá que
el nombre de la variable coincida exactamente con la tabla de la sección 2
(un typo en el slug es el error más común).

## Checklist rápido

- [ ] `CALENDLY_URL` configurado
- [ ] Al menos 1 `STRIPE_LINK_<PAQUETE>` configurado (idealmente Diagnóstico +
      Starter, los dos puntos de entrada más comunes)
- [ ] `OPERATOR_NAME`, `OPERATOR_BIO`, `OPERATOR_EMAIL` configurados
- [ ] `OPERATOR_LINKEDIN` configurado
- [ ] `/paquetes` visitado en producción y revisado visualmente
