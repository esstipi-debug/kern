"""Deja el Odoo de pruebas en el estado que los tests de integracion exigen.

    docker compose -f docker-compose.odoo.yml up -d
    python scripts/odoo_test_setup.py
    uv run --no-sync pytest -m integracion

Sin esto los tests fallan por tres razones que no son bugs de Kern, y que
costaron una tarde distinguir de los que si lo son:

1. **`purchase` no viene instalado.** El addon depende de base/stock/sale, pero
   el CONECTOR (``src/connectors/odoo.py``) escribe ordenes de compra. Sin el
   modulo Purchase, ``purchase.order`` no existe y Kern falla con un fault de
   Odoo. Es un prerequisito real para cualquier cliente que use el writeback, y
   hasta ahora no estaba escrito en ningun sitio.

2. **El servidor cachea su registro.** Instalar un modulo con un proceso `odoo`
   aparte NO lo hace visible al servidor que ya corre: hay que reiniciarlo. Los
   sintomas son identicos a "el modelo no existe", asi que sin saberlo se
   persigue el bug equivocado.

3. **`admin` no tiene los grupos.** En un Odoo 17 recien creado, el usuario
   administrador no puede leer ``sale.order``: falta Sales/Administrator. El
   mensaje que ve el cliente es el de Odoo, no uno de Kern, y no dice que basta
   con anadir un grupo.

Los tres son hallazgos, no accidentes del arnes. Estan en
``odoo_addon/PUBLICACION.md`` como prerequisitos que un cliente necesita saber.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import xmlrpc.client

#: Modulos que el conector necesita mas alla de los que el addon declara.
#: `purchase` es el que falta de verdad; los otros los arrastra el addon, y se
#: listan para que la instalacion sea explicita en vez de depender del orden.
MODULOS = ("linchpin_dry_run", "purchase", "sale_management", "stock")

#: Grupos minimos del usuario de API, con lo que cada uno destraba.
GRUPOS = {
    "sales_team.group_sale_manager": "leer sale.order (historial de demanda)",
    "stock.group_stock_manager": "leer stock.quant (niveles de inventario)",
    "purchase.group_purchase_manager": "crear purchase.order en borrador",
    "base.group_system": "leer ir.config_parameter (ajustes del addon)",
}


def _docker(compose: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", compose, *args],
        capture_output=True, text=True, check=False,
    )


def instalar(compose: str, db: str) -> None:
    print(f"instalando {', '.join(MODULOS)} en {db} ...")
    r = _docker(
        compose, "exec", "-T", "odoo", "odoo", "-d", db, "-i", ",".join(MODULOS),
        "--db_host=db", "--db_user=odoo", "--db_password=odoo",
        "--addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
        "--stop-after-init",
    )
    if r.returncode != 0:
        sys.exit(f"la instalacion fallo:\n{r.stderr[-2000:]}")
    print("  instalados")

    # Sin este reinicio el servidor sigue sirviendo el registro con el que
    # arranco, y los modelos recien instalados "no existen" via XML-RPC.
    print("reiniciando el servidor para que recargue el registro ...")
    _docker(compose, "restart", "odoo")


def esperar(url: str, intentos: int = 40) -> None:
    import urllib.error
    import urllib.request
    for i in range(intentos):
        try:
            urllib.request.urlopen(f"{url}/web/database/selector", timeout=5)
            print(f"  Odoo responde tras ~{i * 5}s")
            return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(5)
    sys.exit(f"Odoo no respondio en {url}")


def conceder(url: str, db: str, user: str, key: str) -> None:
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        sys.exit(f"no autentica {user} en {db}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = []
    for xmlid, para_que in GRUPOS.items():
        modulo, nombre = xmlid.split(".")
        filas = models.execute_kw(
            db, uid, key, "ir.model.data", "search_read",
            [[["module", "=", modulo], ["name", "=", nombre]]],
            {"fields": ["res_id"], "limit": 1},
        )
        if not filas:
            sys.exit(f"no existe el grupo {xmlid} -- falta instalar su modulo")
        ids.append(filas[0]["res_id"])
        print(f"  {xmlid:<40} {para_que}")

    models.execute_kw(db, uid, key, "res.users", "write",
                      [[uid], {"groups_id": [(4, g) for g in ids]}])
    print(f"concedidos {len(ids)} grupos a uid {uid}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--compose", default="docker-compose.odoo.yml")
    p.add_argument("--url", default="http://localhost:8069")
    p.add_argument("--db", default="kern_test")
    p.add_argument("--user", default="admin")
    p.add_argument("--key", default="admin")
    p.add_argument("--solo-grupos", action="store_true",
                   help="salta la instalacion de modulos")
    args = p.parse_args()

    if not args.solo_grupos:
        instalar(args.compose, args.db)
    esperar(args.url)
    conceder(args.url, args.db, args.user, args.key)
    print("\nlisto. Ahora: uv run --no-sync pytest -m integracion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
