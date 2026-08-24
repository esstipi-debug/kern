"""Mide si el clasificador de `odoo_connect` acierta contra un Odoo DE VERDAD.

Todo lo que sabemos del onboarding de Odoo Online esta verificado contra
`InMemoryOdooServer`, un doble escrito por nosotros. Eso comprueba que el codigo
hace lo que creemos que Odoo hace -- que es una afirmacion sobre nuestra idea de
Odoo, no sobre Odoo. Este script convierte esa afirmacion en una medicion.

LO QUE MAS IMPORTA MEDIR, y por que este script existe:

    _DB_MARKERS = ("does not exist", "no existe", "database", ...)

Es el UNICO lugar del modulo que clasifica mirando el texto que devuelve el
servidor. Todo lo demas es determinista (`authenticate` no puede devolver False
contra una base inexistente; `ir.model` separa modulo-ausente de permiso). Si
esos marcadores no coinciden con lo que dice un Odoo real, una base mal escrita
se reporta como `error_del_servidor` y el cliente se queda sin el mensaje que lo
manda a arreglarla. Por eso la salida incluye el faultString CRUDO: si falla, ahi
esta exactamente lo que hay que agregar.

Usage:
    export KERN_ODOO_API_KEY=...        # la clave NUNCA va por argumento
    python examples/verify_odoo_live.py --url https://miempresa.odoo.com \\
        --db miempresa --login ops@miempresa.cl

SOLO LECTURA, y ademas UN INTENTO POR MODO. Los sondeos de falla usan
credenciales deliberadamente invalidas contra tu propia instancia: se hace una
sola vez cada uno, a proposito, porque repetir autenticaciones fallidas puede
activar la proteccion antiabuso de odoo.com y dejarte afuera un rato. El sondeo
de host inalcanzable ni siquiera sale a internet -- va al puerto discard de
127.0.0.1, para no tocar un dominio que puede ser de alguien.

LO QUE ESTE SCRIPT NO PUEDE MEDIR SOLO: el modo `permisos_insuficientes` y el
`modulo_ausente` necesitan un usuario sin acceso a Compras o una base sin la app
instalada. Se reportan como NO EJERCITADO -- no como aprobados. Al final se
explica como montarlos si los queres cubrir.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors import odoo_connect  # noqa: E402
from webapp.demo_lead_time_scan import scan_from_deliveries  # noqa: E402

ENV_CLAVE = "KERN_ODOO_API_KEY"

#: Puerto discard: rechaza la conexion al instante y no sale de la maquina. Un
#: dominio inventado podria ser de alguien; esto no toca a nadie.
HOST_MUERTO = "http://127.0.0.1:9"
CLAVE_INVALIDA = "clave-deliberadamente-invalida-de-verificacion"
DB_INVALIDA = "base-que-no-existe-verificacion"

#: Lo que hay que montar del otro lado. Vive aca y no en un .md para que no se
#: desincronice de lo que el script valida: el que corre esto es el que necesita
#: los pasos, y una copia en otro archivo es la que queda vieja.
PASOS = """Montar el Odoo de prueba (~10 minutos, gratis):

  1. odoo.com -> "Probar gratis". Elegi la region que quieras; anota el
     subdominio, porque ES el nombre de la base (`miempresa` en
     miempresa.odoo.com). Es el error #1 de este onboarding.

  2. Instala la app COMPRAS. Sin ella no hay `purchase.order`, y el escaneo se
     niega a medir nombrandolo -- que es el comportamiento correcto, pero no
     verifica nada.

     Si el trial te ofrece DATOS DE DEMOSTRACION, aceptalos: traen ordenes de
     compra con recepciones y te ahorran el paso 3.

  3. Si no hay datos de demo, carga a mano el minimo que el scan exige:
       - un proveedor, con "Plazo de entrega" puesto en su ficha (Compras ->
         Proveedores). Poné algo distinto de 1: el 1 es el default de Odoo y el
         conector lo ignora a proposito.
       - TRES ordenes de compra confirmadas, cada una recibida y validada. Menos
         de tres y el scorecard no puede describir al proveedor.
       - Valida las recepciones en fechas separadas de la orden. Si validas todo
         el mismo dia, el plazo observado da ~0 y no hay brecha que medir.

  4. Clave de API: avatar -> Mi perfil -> Seguridad de la cuenta -> Claves de
     API -> Nueva clave. Se muestra UNA vez.

  5. Corre esto:
       export KERN_ODOO_API_KEY=<la clave>
       python examples/verify_odoo_live.py --url https://miempresa.odoo.com \
           --db miempresa --login <tu usuario>

Para cubrir los dos modos que este script no puede montar solo:
  - permisos_insuficientes: Ajustes -> Usuarios -> crea uno SIN acceso a Compras,
    generale su propia clave, y corre el script con ese usuario.
  - modulo_ausente: un segundo trial sin instalar Compras.
"""


@dataclass
class Caso:
    nombre: str
    esperado: str
    obtenido: str = ""
    mensaje: str = ""
    crudo: str = ""
    ejercitado: bool = True

    @property
    def acierta(self) -> bool:
        return self.ejercitado and self.obtenido == self.esperado


def _clave() -> str:
    desde_entorno = os.environ.get(ENV_CLAVE, "").strip()
    if desde_entorno:
        return desde_entorno
    if not sys.stdin.isatty():
        raise SystemExit(f"exporta {ENV_CLAVE}: la clave no se acepta por argumento")
    return getpass.getpass("Clave de API de Odoo (no se muestra): ").strip()


def _crudo_del_fault(creds: odoo_connect.OdooCredentials, timeout: float) -> str:
    """El faultString tal cual lo manda el servidor, depurado de la clave.

    El check ya lo clasifica; esto guarda la evidencia. Sin el texto crudo, un
    fallo de `_DB_MARKERS` dice "no acerto" y no dice con que arreglarlo.
    """
    import xmlrpc.client

    try:
        common, _ = odoo_connect.server_proxies(creds.url, timeout=timeout)
        common.authenticate(creds.db, creds.login, creds.api_key, {})
    except xmlrpc.client.Fault as exc:
        return odoo_connect._scrub(exc.faultString, creds.api_key)[:400]
    except Exception as exc:  # transporte: no aporta evidencia de texto
        return f"({type(exc).__name__}: {odoo_connect._scrub(exc, creds.api_key)[:200]})"
    return "(authenticate no levanto: devolvio un valor)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verifica el clasificador de odoo_connect contra un Odoo real.",
    )
    parser.add_argument("--pasos", action="store_true",
                        help="imprime como montar el Odoo de prueba y sale")
    if "--pasos" in sys.argv:
        print(PASOS)
        raise SystemExit(0)
    parser.add_argument("--url", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--since", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    clave = _clave()
    reales = dict(url=args.url, db=args.db, login=args.login, api_key=clave)
    casos: list[Caso] = []

    def _probar(nombre: str, esperado: str, **cambios) -> odoo_connect.ConnectionCheck:
        creds = odoo_connect.OdooCredentials(**{**reales, **cambios})
        check = odoo_connect.check_connection(creds, timeout=args.timeout)
        casos.append(Caso(nombre, esperado, check.reason or "ok", check.message))
        return check

    print(f"=== Verificando contra {odoo_connect.OdooCredentials(**reales).host} ===")
    print("    solo lectura, un intento por modo de falla\n")

    # 1. El camino bueno. Si esto falla, lo demas no significa nada.
    print("  [1/5] conexion valida...")
    bueno = _probar("conexion valida", "ok")
    if not bueno.ok:
        print(f"\n  FALLO EL CAMINO BUENO [{bueno.reason}]: {bueno.message}")
        print("  El resto de los sondeos no se corre: sin una conexion buena de "
              "referencia, un 'no conecta' no distingue tu instancia de un bug.")
        raise SystemExit(1)
    print(f"        ok, Odoo {bueno.server_version or '(sin version)'}")

    # 2-4. Los tres modos que se pueden provocar sin tocar la instancia.
    print("  [2/5] host inalcanzable (127.0.0.1:9, no sale de la maquina)...")
    _probar("host inalcanzable", odoo_connect.UNREACHABLE, url=HOST_MUERTO)

    print("  [3/5] base inexistente...")
    caso_db = _probar("base inexistente", odoo_connect.DATABASE, db=DB_INVALIDA)
    casos[-1].crudo = _crudo_del_fault(
        odoo_connect.OdooCredentials(**{**reales, "db": DB_INVALIDA}), args.timeout
    )

    print("  [4/5] credencial invalida...")
    _probar("credencial invalida", odoo_connect.CREDENTIALS, api_key=CLAVE_INVALIDA)

    # 5. Los que no se pueden montar desde aca.
    for nombre, como in (
        ("permisos insuficientes",
         "crea un segundo usuario SIN acceso a Compras y corre esto con su clave"),
        ("modulo ausente",
         "una base sin la app Compras instalada"),
    ):
        casos.append(Caso(nombre, "-", mensaje=como, ejercitado=False))

    # -- el veredicto ---------------------------------------------------------
    print("\n=== Clasificacion ===")
    print(f"  {'caso':<24} {'esperado':<22} {'obtenido':<22} veredicto")
    for c in casos:
        if not c.ejercitado:
            print(f"  {c.nombre:<24} {'-':<22} {'NO EJERCITADO':<22} sin medir")
            continue
        print(f"  {c.nombre:<24} {c.esperado:<22} {c.obtenido:<22} "
              f"{'ACIERTA' if c.acierta else 'FALLA'}")

    if caso_db.reason != odoo_connect.DATABASE:
        print("\n  _DB_MARKERS NO COINCIDIO. Esto es lo que dijo el servidor:")
        print(f"    {casos[2].crudo}")
        print("    Agregale a `_DB_MARKERS` en src/connectors/odoo_connect.py el "
              "pedazo estable de ese texto.")
    else:
        print(f"\n  _DB_MARKERS coincidio. El servidor dijo: {casos[2].crudo[:160]}")

    # -- y la lectura de verdad ----------------------------------------------
    print("\n=== Lectura real (solo lectura) ===")
    entrada = odoo_connect.scan_input(
        odoo_connect.OdooCredentials(**reales),
        since=args.since.strip() or None, timeout=args.timeout,
    )
    pre = entrada.preflight
    print(f"  recepciones : {pre.n_receipts}")
    print(f"  proveedores : {pre.n_suppliers}")
    if pre.first_receipt:
        print(f"  rango       : {pre.first_receipt} -> {pre.last_receipt}")
    if not pre.ok:
        print("  sin datos medibles todavia:")
        for falta in pre.missing:
            print(f"    - {falta}")
    else:
        scan = scan_from_deliveries(entrada.grouped, entrada.dropped, entrada.source_notes)
        if scan.ok:
            h = scan.headline
            print(f"  BRECHA MEDIDA: {h['worst_supplier']} promete "
                  f"{h['worst_declared']:.0f} dias y entrega en {h['worst_observed']:.0f}")
        else:
            print("  el QA del scan no paso:")
            for issue in scan.qa_issues:
                print(f"    - {issue}")

    fallados = [c for c in casos if c.ejercitado and not c.acierta]
    sin_medir = [c for c in casos if not c.ejercitado]
    print("\n=== Resumen ===")
    print(f"  ejercitados : {len(casos) - len(sin_medir)}")
    print(f"  aciertan    : {len(casos) - len(sin_medir) - len(fallados)}")
    print(f"  sin medir   : {len(sin_medir)} ({', '.join(c.nombre for c in sin_medir)})")
    for c in sin_medir:
        print(f"    para cubrir '{c.nombre}': {c.mensaje}")
    if fallados:
        print("\n  NO se puede describir el clasificador como verificado.")
        raise SystemExit(1)
    print("\n  Los modos ejercitados aciertan contra este Odoo. Los no ejercitados "
          "siguen apoyados solo en el doble: no los describas como comprobados.")


if __name__ == "__main__":
    main()
