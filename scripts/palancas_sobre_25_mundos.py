"""Que pasa con las palancas de esta rama cuando se las mide sobre 25 mundos y no cinco.

`KERN_VENTAS_CENSURADAS.md` H3 encontro que su propia palanca se encoge **3,5 veces** al
pasar de las cinco semillas canonicas a las 25, y dio la causa: por H1 una palanca paga
donde Kern ya va mal (r=-0,61) y las cinco canonicas contienen TRES de los ocho mundos
donde Kern va peor. Ese documento cerro diciendo que lo que las palancas anteriores
leerian sobre 25 mundos **no estaba medido**, y se nego a suponerlo. Esto lo mide.

LOS DOS TITULARES QUE SE RE-MIDEN, Y LOS CINCO CARRILES QUE VIENEN CON ELLOS
    - `horizonte_estimado` -- topear la politica por lo que queda por vender hasta la
      fecha de fin del SKU. Publicada en **+0,1618 sobre 5/5 mundos**
      (`KERN_HORIZONTE_POR_SKU.md`). Es la UNICA palanca de "compra menos de lo que no
      vas a vender" que la rama midio.
    - `servicio_econ` -- pasarle a Kern la razon critica por SKU, que necesita el precio.
      Publicada en **+18,9 puntos** (`KERN_RESIDUO_DE_LA_BRECHA.md`). Es de la familia
      "compra mas", igual que la censura.

    Los dos barridos traen mas carriles que esos dos y se re-miden TODOS, porque
    `KERN_RESIDUO_DE_LA_BRECHA.md` apoya otras dos afirmaciones en los suyos. Publicar
    solo los titulares seria elegir cuales resultados se miran.

ESTO NO MIDE NADA NUEVO: AGREGA
    Los dos barridos son los scripts de siempre corridos con `--semillas 1000..1024`, sin
    tocarles una linea. Este script solo LEE las dos lineas base nuevas, las compara
    contra las congeladas de cinco mundos, y calcula lo que un promedio no dice: la
    dispersion, el test pareado, la correlacion de H1 y la POTENCIA -- sin la cual un
    numero mas chico no se distingue de un numero peor medido.

EL CONTROL, Y POR QUE ES LA MITAD DEL TRABAJO
    Las cinco semillas canonicas estan DENTRO de las 25. Su carril de control tiene que
    reproducir la linea base congelada al ultimo digito, o la comparacion no es entre dos
    tamanos de muestra sino entre dos experimentos. Se chequea y se publica.

    PYTHONPATH=. python scripts/palancas_sobre_25_mundos.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASES = ROOT / "documentation" / "lineas_base"
SALIDA = BASES / "palancas_sobre_25_mundos.json"

#: Las cinco con las que se publicaron los titulares que este script interroga.
CANONICAS = (1000, 1001, 1002, 1003, 1004)

HORIZONTE = ("horizonte_por_sku_25.json", "horizonte_por_sku.json")
RESIDUO = ("residuo_de_la_brecha_25.json", "residuo_de_la_brecha.json")

#: palanca -> (linea base de 25, linea base congelada de 5, carril, familia)
#:
#: Estan TODOS los carriles de los dos barridos, no solo los dos titulares. Publicar solo
#: los que sobreviven seria elegir el resultado: `KERN_RESIDUO_DE_LA_BRECHA.md` afirma
#: ademas que medir al proveedor "empeora en 3 de 5 mundos" y que "apilar palancas es peor
#: que la mejor palanca sola", y las dos son afirmaciones de CINCO mundos que esta
#: medicion tiene que poder desmentir.
PALANCAS = {
    "horizonte_estimado": (*HORIZONTE, "horizonte_estimado", "compra menos"),
    "horizonte_real": (*HORIZONTE, "horizonte_real", "compra menos"),
    "servicio_econ": (*RESIDUO, "servicio_econ", "compra mas"),
    "sigma_obs": (*RESIDUO, "sigma_obs", "compra mas"),
    "proveedor_medido": (*RESIDUO, "proveedor_medido", "compra mas"),
    "plazo_obs": (*RESIDUO, "plazo_obs", "compra mas"),
    "todo": (*RESIDUO, "todo", "apiladas"),
    # La ultima va de control del METODO: su numero ya esta publicado sobre 25 mundos,
    # asi que si este script le diera otro, el que estaria mal es el script.
    "censura_corregida": (
        "ventas_censuradas.json",
        "ventas_censuradas.json",
        "censura_corregida",
        "compra mas",
    ),
}


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else float("nan")


def _pareado(delta: list[float]) -> dict:
    """El test pareado por mundo, su intervalo, y la POTENCIA.

    La potencia es lo que separa "no esta ahi" de "no lo vimos", y sin ella un numero
    mas chico sobre mas mundos no dice nada. Se calcula contra el tamano PUBLICADO de la
    propia palanca: la pregunta es si esta medicion habria visto el efecto que se le
    atribuia.
    """
    from scipy import stats

    n = len(delta)
    media, sd = statistics.fmean(delta), statistics.stdev(delta)
    se = sd / math.sqrt(n)
    gl = n - 1
    t, p = stats.ttest_1samp(delta, 0.0)
    lo, hi = stats.t.interval(0.95, gl, loc=media, scale=se)
    critico = stats.t.ppf(0.975, gl)
    return {
        "n": n,
        "media": media,
        "sd": sd,
        "se": se,
        "t": float(t),
        "p": float(p),
        "ic95_bajo": float(lo),
        "ic95_alto": float(hi),
        "mundos_que_mejoran": sum(1 for x in delta if x > 0),
        "mde_80": float((critico + stats.t.ppf(0.80, gl)) * se),
        "_critico": float(critico),
        "_gl": gl,
    }


def _potencia(se: float, gl: int, critico: float, efecto: float) -> float:
    from scipy import stats

    ncp = efecto / se
    return float(
        1 - stats.nct.cdf(critico, gl, ncp) + stats.nct.cdf(-critico, gl, ncp)
    )


def _una_palanca(nombre: str) -> dict:
    archivo_25, archivo_5, carril, familia = PALANCAS[nombre]
    d25 = json.loads((BASES / archivo_25).read_text())
    d5 = json.loads((BASES / archivo_5).read_text())

    filas = {f["seed"]: f for f in d25["por_semilla"]}
    if len(filas) < 25:
        raise SystemExit(f"{nombre}: la linea base de 25 trae {len(filas)} mundos")

    # EL CONTROL. Las canonicas estan adentro; su carril de control tiene que salir
    # identico al congelado o esto no compara dos muestras sino dos experimentos.
    congeladas = {f["seed"]: f for f in d5["por_semilla"]}
    control = []
    for seed in CANONICAS:
        if seed not in congeladas:
            continue
        for campo in ("capture", "total", "unidades_pedidas"):
            a, b = filas[seed]["kern"][campo], congeladas[seed]["kern"][campo]
            if abs(a - b) > 1e-9 * max(1.0, abs(b)):
                raise SystemExit(
                    f"{nombre}: el control se separo del congelado en la semilla {seed}, "
                    f"campo {campo}: {a!r} vs {b!r}"
                )
        control.append(seed)

    delta = [filas[s][carril]["capture"] - filas[s]["kern"]["capture"] for s in filas]
    base = [filas[s]["kern"]["capture"] for s in filas]
    est = _pareado(delta)

    # El mismo carril restringido a las cinco canonicas, para que el "se encoge N veces"
    # sea una resta entre los MISMOS mundos y no contra un numero de otra corrida.
    cinco = [filas[s][carril]["capture"] - filas[s]["kern"]["capture"] for s in CANONICAS]
    media_5 = statistics.fmean(cinco)

    # Lo PUBLICADO sobre cinco mundos. Para la censura los dos archivos son el mismo
    # -- su titular ya se midio sobre 25 --, asi que su numero de cinco vive en
    # `resumen_canonico`: leerlo de `resumen` daria el de 25 y la fila se compararia
    # contra si misma.
    publicado = (
        d5["resumen_canonico"][carril]["mejora_vs_kern"]
        if archivo_25 == archivo_5
        else d5["resumen"][carril]["mejora_vs_kern"]
    )
    return {
        "familia": familia,
        "publicado_en_5_mundos": publicado,
        "en_5_mundos_de_esta_corrida": media_5,
        "en_25_mundos": est["media"],
        "encogimiento": media_5 / est["media"] if est["media"] else None,
        "mundos_que_mejoran_5": sum(1 for x in cinco if x > 0),
        "control_reproduce_el_congelado": sorted(control),
        "corr_con_la_captura_del_control": _corr(base, delta),
        "potencia_para_ver_lo_publicado": _potencia(
            est["se"], est["_gl"], est["_critico"], abs(publicado)
        ),
        **{k: v for k, v in est.items() if not k.startswith("_")},
    }


def medir() -> dict:
    return {
        "semillas": list(range(1000, 1025)),
        "canonicas": list(CANONICAS),
        "palancas": {n: _una_palanca(n) for n in PALANCAS},
    }


def frase(datos: dict) -> str:
    p = datos["palancas"]
    h, s = p["horizonte_estimado"], p["servicio_econ"]
    return (
        f"Sobre 25 mundos la fecha de fin por SKU queda en {h['en_25_mundos']:+.4f} "
        f"({h['mundos_que_mejoran']}/25, p={h['p']:.5f}) y el nivel de servicio en "
        f"{s['en_25_mundos']:+.4f} ({s['mundos_que_mejoran']}/25, p={s['p']:.5f}). "
        f"Se encogen {h['encogimiento']:.2f}x y {s['encogimiento']:.2f}x contra su "
        f"lectura sobre las cinco canonicas, con una correlacion contra la captura del "
        f"control de {h['corr_con_la_captura_del_control']:+.2f} y "
        f"{s['corr_con_la_captura_del_control']:+.2f}."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=Path, default=SALIDA)
    args = ap.parse_args()

    datos = medir()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n")

    print(f"{'palanca':22}{'familia':>14}{'en 5':>10}{'en 25':>10}{'encoge':>9}"
          f"{'t':>8}{'p':>8}{'mejoran':>10}{'r con base':>12}{'potencia':>10}")
    for nombre, v in datos["palancas"].items():
        print(f"{nombre:22}{v['familia']:>14}{v['en_5_mundos_de_esta_corrida']:>+10.4f}"
              f"{v['en_25_mundos']:>+10.4f}{v['encogimiento']:>8.1f}x{v['t']:>+8.2f}"
              f"{v['p']:>8.3f}{str(v['mundos_que_mejoran']) + '/25':>10}"
              f"{v['corr_con_la_captura_del_control']:>+12.2f}"
              f"{v['potencia_para_ver_lo_publicado']:>10.1%}")
    print(f"\n{frase(datos)}\nlinea base: {args.json}")


if __name__ == "__main__":
    main()
