"""El decisor tipado (Jev) y su lugar en el ruteo de intencion.

Todo corre contra un transporte falso: ningun test sale a la red. Lo que se fija
es el contrato, no la calidad de Jev. Esa calidad no se puede afirmar sin medirla
sobre briefs reales, y para eso esta ``scripts/eval_intent_decider.py``.
"""

from __future__ import annotations

import json
import math

import pytest

from nosilo_core import llm
from nosilo_core.llm import Decision, JevDecider, get_decider, parse_jev_choice
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator

OPTIONS = {"inventory_optimization": "reorder points", "sourcing": "supplier award", "whatif": "sensitivity"}

#: Un brief que no comparte ninguna palabra clave con ningun tool.
AMBIGUOUS = "xyzzy plugh"


def _answer(choice="sourcing", confidence=0.9, probabilities=None) -> dict:
    probabilities = probabilities or {"sourcing": 0.9, "inventory_optimization": 0.07, "whatif": 0.03}
    return {
        "model": "jev-1.13.0",
        "answers": {"decision": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probabilities}},
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }


class _Transport:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, url, headers, body, timeout_s) -> bytes:
        self.calls.append((url, dict(headers), json.loads(body), timeout_s))
        return json.dumps(self.payload).encode("utf-8")


class _FixedDecider:
    def __init__(self, decision: Decision | None = None, error: Exception | None = None) -> None:
        self.decision, self.error, self.calls = decision, error, 0

    def available(self) -> bool:
        return True

    def choose(self, state, instructions, options) -> Decision:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.decision.choice in options
        return self.decision


class _GuessingProvider(llm.RulesFallback):
    def __init__(self, guess: str) -> None:
        self.guess = guess

    def available(self) -> bool:
        return True

    def extract(self, prompt, schema) -> dict:
        return {"job_type": self.guess}


# ---- el cliente HTTP -------------------------------------------------------------

def test_el_pedido_es_el_de_la_documentacion() -> None:
    transport = _Transport(_answer())
    JevDecider("k-123", transport=transport).choose("brief", "pick one", OPTIONS)
    ((url, headers, body, timeout_s),) = transport.calls
    assert url == llm.JEV_ENDPOINT
    assert headers["Authorization"] == "Bearer k-123"
    assert body["model"] == llm.JEV_DEFAULT_MODEL
    assert body["state"] == "brief"
    assert body["questions"]["decision"] == {"type": "choice", "instructions": "pick one", "criteria": OPTIONS}
    assert timeout_s == llm.JEV_TIMEOUT_S


def test_la_respuesta_se_lee_con_su_distribucion() -> None:
    decision = JevDecider("k", transport=_Transport(_answer())).choose("b", "i", OPTIONS)
    assert (decision.choice, decision.confidence) == ("sourcing", 0.9)
    assert decision.ranked() == ["sourcing", "inventory_optimization", "whatif"]


def test_una_decision_necesita_al_menos_dos_opciones() -> None:
    with pytest.raises(ValueError, match="two options"):
        JevDecider("k", transport=_Transport(_answer())).choose("b", "i", {"sourcing": "x"})


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"answers": {}}, "no answer"),
        (_answer(choice="pricing"), "not one of the options"),
        (_answer(confidence=1.5), "outside"),
        (_answer(confidence=math.nan), "finite"),
        (_answer(confidence=True), "finite"),
        (_answer(probabilities={"sourcing": 0.5, "pricing": 0.5}), "never offered"),
    ],
)
def test_una_respuesta_rara_levanta_en_vez_de_coercionarse(payload, match) -> None:
    with pytest.raises(ValueError, match=match):
        parse_jev_choice(payload, OPTIONS)


def test_sin_clave_no_hay_decisor(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert get_decider() is None


def test_con_clave_el_decisor_es_jev(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert isinstance(get_decider(), JevDecider)


# ---- el ruteo --------------------------------------------------------------------

@pytest.fixture(scope="module")
def reg():
    return tools.build_default_registry()


def test_un_brief_que_las_reglas_resuelven_no_consulta_al_decisor(reg) -> None:
    decider = _FixedDecider(error=AssertionError("no deberia consultarse"))
    res = intent.classify("set up reorder points and safety stock", reg, llm.RulesFallback(), decider=decider)
    assert res.job_type == "inventory_optimization"
    assert decider.calls == 0


def test_un_decisor_seguro_rutea_con_su_propia_confianza(reg) -> None:
    decider = _FixedDecider(Decision("sourcing", 0.83, {"sourcing": 0.83, "whatif": 0.17}))
    res = intent.classify(AMBIGUOUS, reg, llm.RulesFallback(), decider=decider)
    assert (res.job_type, res.confidence) == ("sourcing", 0.83)


def test_un_decisor_inseguro_devuelve_la_pregunta_con_su_ranking(reg) -> None:
    probs = {"whatif": 0.3, "sourcing": 0.35, "inventory_optimization": 0.25, "pricing": 0.1}
    decider = _FixedDecider(Decision("sourcing", intent.MIN_DECISION_CONFIDENCE - 0.01, probs))
    res = intent.classify(AMBIGUOUS, reg, _GuessingProvider("whatif"), decider=decider)
    assert res.job_type is None
    assert res.candidates == ["sourcing", "whatif", "inventory_optimization"]


def test_el_decisor_va_antes_que_el_llm(reg) -> None:
    decider = _FixedDecider(Decision("sourcing", 0.9, {"sourcing": 0.9, "whatif": 0.1}))
    res = intent.classify(AMBIGUOUS, reg, _GuessingProvider("whatif"), decider=decider)
    assert res.job_type == "sourcing"


def test_un_decisor_que_falla_cae_al_camino_de_siempre(reg) -> None:
    decider = _FixedDecider(error=TimeoutError("jev down"))
    con_llm = intent.classify(AMBIGUOUS, reg, _GuessingProvider("whatif"), decider=decider)
    assert (con_llm.job_type, con_llm.confidence) == ("whatif", 0.6)
    sin_llm = intent.classify(AMBIGUOUS, reg, llm.RulesFallback(), decider=decider)
    assert sin_llm.job_type is None and sin_llm.candidates


def test_el_orquestador_le_pasa_el_decisor_al_ruteo(reg, monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert Orchestrator(registry=reg, provider=llm.RulesFallback(), clients_root=None).decider is None
    decider = _FixedDecider(Decision("sourcing", 0.9, {"sourcing": 0.9, "whatif": 0.1}))
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback(), clients_root=None, decider=decider)
    orch.run(AMBIGUOUS)
    assert decider.calls == 1


# ---- la medicion -----------------------------------------------------------------

def test_el_script_de_medicion_corre_con_y_sin_decisor(tmp_path, monkeypatch, capsys) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "eval_intent_decider.py"
    spec = importlib.util.spec_from_file_location("eval_intent_decider", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    sample = path.parents[1] / "examples" / "intent_briefs_sample.jsonl"

    monkeypatch.setattr(script.llm, "get_decider", lambda: None)
    assert script.main(["--briefs", str(sample)]) == 0
    assert "keyword part only" in capsys.readouterr().out

    probs = {"sourcing": 0.7, "whatif": 0.2, "pricing": 0.1}
    monkeypatch.setattr(script.llm, "get_decider", lambda: _FixedDecider(Decision("sourcing", 0.7, probs)))
    out_json = tmp_path / "out.json"
    assert script.main(["--briefs", str(sample), "--json", str(out_json)]) == 0
    dr = json.loads(out_json.read_text(encoding="utf-8"))["decider"]
    assert dr["answered"] == 12 and dr["errors"] == 0
    assert dr["accuracy_all"] == 1 / 12
    assert dr["by_threshold"]["0.8"]["coverage"] == 0.0
