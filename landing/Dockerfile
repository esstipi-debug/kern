# La pagina es estatica y sin una sola dependencia externa: la unica URL en
# todo el motor de scrollcraft es el namespace de SVG. Asi que no hay runtime
# que desplegar, solo tres archivos servidos por nginx.
FROM nginx:1.27-alpine

COPY index.html scrollcraft.css scrollcraft.js /usr/share/nginx/html/

# nginx:alpine ya escucha en el 80, que es lo que declara internal_port en
# fly.toml. No se toca la config por defecto a proposito: cualquier cosa que
# se agregue aca hay que mantenerla.
