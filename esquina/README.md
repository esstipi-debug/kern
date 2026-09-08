# Esquina 950

Pagina estatica del lote I-A1 (Ruta 9 Norte x Y-50, Cabeza de Mar).
No es Kern. No entra al motor, a `webapp/` ni a `linchpin.fly.dev`.

En vivo: https://esquina-950.fly.dev/ (`noindex`).

## Fly

Misma cuenta que Kern. App aparte, igual que `landing/` → `kern-landing`.

```
cd esquina
fly deploy --ha=false
```

Nunca `fly deploy` desde la raiz del repo: eso pisa `linchpin`.
Nunca desplegar este folder sobre `kern-landing`.

## Rama

El checkout de trabajo es la rama `esquina-950`, cortada de `main`.
No mezclar con PRs del motor.
