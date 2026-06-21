"""Tests for the CHAIN leadership playbook (jobs/leadership.py)."""

import pytest

from jobs import leadership as ld
from jobs import qa


def test_coerce_scores_from_string_and_list():
    assert ld.coerce_scores("3 2 3 1 1") == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}
    assert ld.coerce_scores([0, 1, 2, 3, 4]) == {"C": 0, "H": 1, "A": 2, "I": 3, "N": 4}
    assert ld.coerce_scores("3,2,3,1,1") == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}


def test_coerce_scores_rejects_bad_input():
    assert ld.coerce_scores("1 2 3") is None        # too few
    assert ld.coerce_scores([1, 2, 3, 4, 9]) is None  # out of range
    assert ld.coerce_scores("a b c d e") is None     # non-int
    assert ld.coerce_scores(None) is None


def test_archetype_operador_invisible():
    # strong C/H/A, weak I/N -> the signature "invisible operator"
    name, _ = ld.archetype({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert name == "Operador invisible"


def test_archetype_lider_integral_and_en_formacion():
    assert ld.archetype({"C": 3, "H": 3, "A": 3, "I": 3, "N": 3})[0] == "Líder integral"
    assert ld.archetype({"C": 1, "H": 0, "A": 1, "I": 1, "N": 0})[0] == "En formación"


def test_priority_lever_breaks_ties_by_impact_order():
    # I and N both lowest at 1 -> I wins (impact order I,N,A,H,C)
    code, name, level = ld.priority_lever({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert code == "I" and level == 1 and name == "Influyente"


def test_score_profile_computes_average_gap_and_archetype():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    assert p.name == "Equipo X"
    assert p.average == pytest.approx(2.0)
    assert p.gap == 2  # max 3 - min 1
    assert p.archetype == "Operador invisible"
    assert p.lever_code == "I"


def test_score_profile_rejects_invalid_scores():
    with pytest.raises(ValueError):
        ld.score_profile({"C": 3, "H": 2})            # missing dims
    with pytest.raises(ValueError):
        ld.score_profile({"C": 9, "H": 2, "A": 3, "I": 1, "N": 1})  # out of range


def test_qa_verify_leadership_passes_clean_profile():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert qa.verify_leadership(p) == []
    assert qa.leadership_passed(p) is True


def test_qa_verify_leadership_catches_tampered_profile():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    object.__setattr__(p, "average", 9.9)  # corrupt
    assert any("average" in i for i in qa.verify_leadership(p))


def test_practices_and_questions_filled_for_every_dimension():
    for code, _ in ld.DIMS:
        assert len(ld.PRACTICES[code]) >= 2
        assert len(ld.QUESTIONS[code]) >= 2


def test_score_profile_attaches_lever_directives():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})  # lever = I
    assert p.directives == ld.PRACTICES["I"]
    assert len(p.directives) >= 2


def test_diagnostic_questions_cover_all_dimensions():
    qs = ld.diagnostic_questions()
    assert len(qs) >= 10
    assert any(q.startswith("C ") or q.startswith("[C]") or "Colaborativo" in q for q in qs)


def test_radar_chart_writes_png(tmp_path):
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    out = ld.radar_chart(p, tmp_path / "chain.png")
    assert out.exists() and out.stat().st_size > 0


def test_write_all_writes_report_and_chart(tmp_path):
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    written = ld.write_all(p, tmp_path, client="Acme")
    assert written["chart"].exists()
    assert written["report"].exists()
    md = written["report"].read_text(encoding="utf-8")
    # English scaffolding
    assert "## Score by dimension" in md
    assert "## Archetype" in md
    assert "## Priority lever" in md
    # Spanish CHAIN substance kept verbatim
    assert "Operador invisible" in md          # archetype (Spanish)
    assert "Influyente" in md                   # dimension / lever name (Spanish)
    assert "Palamariu" in md                    # attribution present (Spanish)
    assert p.directives[0].split(".")[0][:8] in md  # at least one directive rendered
