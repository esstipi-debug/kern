"""Floor-evidence catalog: 53 norms, native lookup, detectar contract, no 'cumple'."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.iso_catalog import (
    AVISO_DETECCION,
    DEFAULT_CATALOG_PATH,
    HUECO_14083_TKM,
    PerfilInvalido,
    SenalesOrden,
    buscar_norma,
    claim_permitido,
    de_modulo,
    detectar,
    evidencia_de_orden,
    evidencia_nativa,
    load_catalog,
    parse_perfil,
    tarjeta_evidencia,
    texto_prohibido,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iso" / "detectar.ejemplo.json"
_FORBIDDEN = ("cumple iso", "cumple ISO", "esta certificado", "está certificado", "planta certificada")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def ejemplo():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_catalog_path_is_committed():
    assert DEFAULT_CATALOG_PATH.is_file()


def test_catalog_loads_53_norms(catalog):
    assert catalog["paquete"] == "iso-operaciones-cadena"
    assert catalog["version"] == "1.0.0"
    assert catalog["estado"] == "integrado"
    assert catalog["stats"]["total"] == 53
    assert len(catalog["normas"]) == 53
    assert catalog["stats"]["nativo"] == 18
    coberturas = [n["kern"]["cobertura"] for n in catalog["normas"]]
    assert coberturas.count("nativo") == 18


def test_aviso_is_not_official_iso_text(catalog):
    aviso = catalog["aviso"].lower()
    assert "oficial" in aviso
    assert "conformidad" in aviso


def test_buscar_norma_by_id_and_codigo(catalog):
    n = buscar_norma("iso-14067", catalog)
    assert n is not None
    assert n["codigo"] == "ISO 14067"
    assert buscar_norma("ISO 14067", catalog)["id"] == "iso-14067"
    assert buscar_norma("no-existe", catalog) is None


def test_de_modulo_and_evidencia_nativa_energy(catalog):
    energy = de_modulo("energy", catalog)
    assert energy
    assert all("energy" in n["kern"]["modulos"] for n in energy)
    nativas = evidencia_nativa("energy", catalog)
    ids = {n["id"] for n in nativas}
    assert "iso-50001" in ids
    assert "iso-14067" in ids
    assert all(n["kern"]["cobertura"] == "nativo" for n in nativas)


def test_de_modulo_smartpro_lists_native_9001(catalog):
    nativas = evidencia_nativa("smartpro", catalog)
    ids = {n["id"] for n in nativas}
    assert "iso-9001" in ids
    assert "iatf-16949" in ids


def test_claim_permitido_never_says_cumple(catalog):
    for norma in catalog["normas"]:
        claim = claim_permitido(norma)
        low = claim.lower()
        assert "cumple" not in low
        assert "certificad" not in low
        assert not texto_prohibido(claim)
        card = tarjeta_evidencia(norma)
        assert card["claimPermitido"] == claim
        assert card["limite"] == norma["kern"]["limite"]
    assert texto_prohibido("la planta cumple ISO 9001")
    assert not texto_prohibido("KERN tiene evidencia de piso para ISO 9001.")


def test_14083_claim_names_the_tkm_gap(catalog):
    n = buscar_norma("iso-14083", catalog)
    assert "falta t·km" in claim_permitido(n)
    assert "cumple" not in claim_permitido(n).lower()


def test_parse_perfil_rejects_bad_types():
    with pytest.raises(PerfilInvalido):
        parse_perfil(["automotriz"])
    with pytest.raises(PerfilInvalido):
        parse_perfil({"clientePide": "iatf"})
    with pytest.raises(PerfilInvalido):
        parse_perfil({"texto": 12})
    with pytest.raises(PerfilInvalido):
        parse_perfil({"kern": ["no-existe"]})


def test_detectar_empty_profile_is_empty(ejemplo):
    out = detectar(ejemplo["perfilVacio"])
    assert out["aviso"] == AVISO_DETECCION
    assert out["detectadas"] == []


def test_detectar_contrato_post_hits_the_handoff_norms(ejemplo):
    out = detectar(ejemplo["contratoPost"])
    assert out["aviso"] == AVISO_DETECCION
    by_id = {row["id"]: row for row in out["detectadas"]}
    assert "iatf-16949" in by_id
    assert by_id["iatf-16949"]["aplicabilidad"] >= 0.8
    assert "iso-14067" in by_id
    assert by_id["iso-14067"]["aplicabilidad"] >= 0.7
    assert "iso-14083" in by_id
    assert by_id["iso-14083"]["aplicabilidad"] >= 0.8
    assert by_id["iso-14083"]["huecos"] == [HUECO_14083_TKM]
    assert by_id["iso-14083"]["potencial"] <= 0.3
    assert "iso-17364" in by_id
    assert "iso-9001" in by_id
    assert "iso-2859-1" in by_id
    assert "iso-8000-1" in by_id
    # Sectorials the plant did not name stay out.
    assert "iso-22000" not in by_id
    assert "iso-13485" not in by_id
    blob = json.dumps(out, ensure_ascii=False).lower()
    for phrase in _FORBIDDEN:
        assert phrase.lower() not in blob


def test_detectar_14083_hueco_clears_when_texto_has_tkm(ejemplo):
    body = dict(ejemplo["contratoPost"])
    body["texto"] = "El 3PL ya entrega t·km por embarque."
    out = detectar(body)
    row = next(r for r in out["detectadas"] if r["id"] == "iso-14083")
    assert row["huecos"] == []


def test_evidencia_de_orden_monitor_proof(catalog):
    panel = evidencia_de_orden(
        SenalesOrden(tiene_ciclo=True, tiene_scrap=True, tiene_kg=True, tiene_tkm=False),
        catalog,
    )
    ids = [e["id"] for e in panel["evidencias"]]
    assert ids == ["iso-9001", "iso-50001", "iso-14067"]
    assert "Evidencia nativa" in panel["resumen"]
    assert "ISO 9001" in panel["resumen"]
    assert "ISO 50001" in panel["resumen"]
    assert "14083" in panel["resumen"]
    assert "falta t·km" in panel["resumen"]
    assert panel["huecos"] == [HUECO_14083_TKM]
    assert panel["fueraDeTramo"]["id"] == "iso-14083"
    blob = json.dumps(panel, ensure_ascii=False).lower()
    assert "cumple" not in blob
    assert "certificad" not in blob


def test_evidencia_de_orden_without_signals_is_empty():
    panel = evidencia_de_orden(SenalesOrden())
    assert panel["evidencias"] == []
    assert "Sin evidencia nativa" in panel["resumen"]
    assert panel["huecos"] == [HUECO_14083_TKM]
