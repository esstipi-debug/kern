"""Tests for the pure SOP template/render engine: bilingual content, substitution, markdown."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.cycle_count import DEFAULT_POLICY
from src.procedures.models import DocumentControl
from src.procedures.templates import (
    PROCESS_KEYS,
    TEMPLATES,
    JobBreakdownStep,
    ProcedureTemplate,
    draft_to_markdown,
    get_template,
    render_draft,
    resolve_next_review,
)

ISO_SENTENCE_ES = "BORRADOR - requiere revision y aprobacion para adecuacion y suficiencia (ISO 9001:2015 7.5.2c)"
ISO_SENTENCE_EN = "DRAFT - requires review and approval for suitability and adequacy (ISO 9001:2015 7.5.2c)"


def _control(**overrides) -> DocumentControl:
    """A fully-formed, still-unapproved document control header: only overrides misbehave."""
    defaults = dict(
        owner="Ana",
        approver="Beto",
        version="1",
        effective_date=date(2026, 9, 4),
        next_review=date(2027, 9, 4),
        review_cadence_days=365,
        cadence_source="convention",
    )
    defaults.update(overrides)
    return DocumentControl(**defaults)


# ---- template content -------------------------------------------------------------


def test_three_templates_exist_in_both_languages():
    assert set(PROCESS_KEYS) == {"replenishment", "cycle_count", "sop_cycle"}
    for key in PROCESS_KEYS:
        template = TEMPLATES[key]
        assert template.key == key
        for lang in ("es", "en"):
            assert template.title[lang].strip()
            assert template.purpose[lang].strip()
            assert template.scope[lang].strip()


def test_every_step_has_key_points_and_reasons_in_es_and_en():
    for template in TEMPLATES.values():
        assert len(template.steps) >= 5
        for step in template.steps:
            for lang in ("es", "en"):
                assert step.important_step[lang].strip()
                assert len(step.key_points[lang]) > 0
                assert len(step.key_points[lang]) == len(step.reasons[lang])


def test_control_plan_rows_reference_existing_step_numbers():
    for template in TEMPLATES.values():
        assert len(template.control_plan) >= 3
        assert len(template.training) >= 2
        step_numbers = {s.n for s in template.steps}
        for row in template.control_plan:
            assert row.process_step in step_numbers


def test_cycle_count_template_imports_default_policy_not_a_copy():
    template = TEMPLATES["cycle_count"]
    assert template.parameters["counts_per_year"] == DEFAULT_POLICY.counts_per_year
    assert template.parameters["counts_per_year"] is DEFAULT_POLICY.counts_per_year

    # cycle_count's own template module, not the package's __init__ -- the literal
    # counts must never be retyped anywhere in the templates package.
    source = Path(__file__).resolve().parent.parent / "src" / "procedures" / "templates" / "_cycle_count.py"
    text = source.read_text(encoding="utf-8")
    assert '{"A": 12' not in text


# ---- render_draft: parameter substitution and sourcing -----------------------------


def test_render_substitutes_params_and_records_their_source():
    template = get_template("replenishment")
    control = _control()
    draft = render_draft(
        template, control=control, params={"service_level": "98%", "lead_time_days": 10}, lang="es"
    )
    used = {name: (value, source) for name, value, source in draft.parameters_used}
    assert used["service_level"] == ("98%", "param")
    assert used["lead_time_days"] == ("10", "param")

    haystack = " ".join(
        [draft.purpose, draft.scope]
        + [s.important_step for s in draft.steps]
        + [kp for s in draft.steps for kp in s.key_points]
    )
    assert "98%" in haystack
    assert "10" in haystack


def test_render_defaults_are_labelled_convention():
    template = get_template("replenishment")
    control = _control()
    draft = render_draft(template, control=control, params={}, lang="es")
    used = {name: (value, source) for name, value, source in draft.parameters_used}
    assert used["review_period_days"] == ("7", "convention")
    assert used["service_level"] == ("no declarado", "convention")
    assert used["lead_time_days"] == ("no declarado", "convention")
    assert used["policy_source"] == ("hoja de politica vigente (inventory_optimization)", "convention")


def test_replenishment_purpose_is_coherent_when_service_level_and_lead_time_are_undeclared():
    """Minor 1: the default/no-params render (the capability-smoke path, and any
    first run before a client profile exists) used to render the Purpose sentence
    as 'con el service level (no declarado) y el lead time (no declarado)
    declarados por el cliente' -- claiming in the same clause that the values were
    declared BY THE CLIENT and that they are NOT declared. Same contradiction in
    English: 'using the declared service level (not declared)'. It is the first
    sentence of the client-facing SOP."""
    template = get_template("replenishment")
    draft_es = render_draft(template, control=_control(), params={}, lang="es")
    draft_en = render_draft(template, control=_control(), params={}, lang="en")

    assert "no declarado" in draft_es.purpose  # the substitution itself still works
    assert "declarados por el cliente" not in draft_es.purpose
    assert "no declarado) declarados" not in draft_es.purpose  # the literal old contradiction

    assert "not declared" in draft_en.purpose
    assert "using the declared service level" not in draft_en.purpose
    assert "declared service level (not declared)" not in draft_en.purpose


def test_replenishment_purpose_still_substitutes_real_service_level_and_lead_time():
    """The Minor 1 rewrite must not break substitution for the normal case where the
    caller (or a client profile) actually supplies these two params."""
    template = get_template("replenishment")
    draft = render_draft(
        template, control=_control(), params={"service_level": "98%", "lead_time_days": 10}, lang="es"
    )
    assert "98%" in draft.purpose
    assert "10" in draft.purpose


def test_render_draft_rejects_unknown_lang():
    template = get_template("replenishment")
    control = _control()
    with pytest.raises(ValueError):
        render_draft(template, control=control, params={}, lang="fr")


# Review finding 1: an `en` draft rendered with no `params` override was shipping the
# Spanish convention-default text verbatim (plain-string defaults are not per-language).
_SPANISH_ONLY_MARKERS = ("no declarado", "hoja de politica vigente", "/anio")


def test_english_render_never_leaks_spanish_convention_defaults():
    for key in PROCESS_KEYS:
        template = get_template(key)
        draft = render_draft(template, control=_control(), params={}, lang="en")
        md = draft_to_markdown(draft)
        for marker in _SPANISH_ONLY_MARKERS:
            assert marker not in md, f"{key}: found Spanish marker {marker!r} in an English render"


def test_english_render_shows_the_english_convention_defaults():
    template = get_template("replenishment")
    draft = render_draft(template, control=_control(), params={}, lang="en")
    used = {name: (value, source) for name, value, source in draft.parameters_used}
    assert used["service_level"] == ("not declared", "convention")
    assert used["lead_time_days"] == ("not declared", "convention")
    assert used["policy_source"] == ("current inventory policy sheet (inventory_optimization)", "convention")

    cc_template = get_template("cycle_count")
    cc_draft = render_draft(cc_template, control=_control(), params={}, lang="en")
    cc_used = {name: (value, source) for name, value, source in cc_draft.parameters_used}
    assert cc_used["counts_per_year"][1] == "convention"
    assert "/yr" in cc_used["counts_per_year"][0]


# ---- render_draft: document control / warnings / dates -----------------------------


def test_approved_at_is_none_and_status_is_draft():
    template = get_template("replenishment")
    control = _control()
    assert control.approved_at is None
    assert control.status == "draft"
    draft = render_draft(template, control=control, params={}, lang="es")
    md = draft_to_markdown(draft)
    assert "None" not in md
    assert "draft" in md


def test_next_review_is_as_of_plus_cadence():
    as_of = date(2026, 9, 4)
    assert resolve_next_review(as_of, 30) == as_of + timedelta(days=30)
    assert resolve_next_review(as_of, 365) == date(2027, 9, 4)


def test_owner_equal_approver_raises_a_warning_not_an_error():
    template = get_template("replenishment")
    control = _control(owner="Ana Diaz", approver="ANA diaz")
    draft = render_draft(template, control=control, params={}, lang="es")
    assert any("segregacion" in w for w in draft.warnings)


def test_owner_different_from_approver_has_no_warning():
    template = get_template("replenishment")
    control = _control(owner="Ana", approver="Beto")
    draft = render_draft(template, control=control, params={}, lang="es")
    assert draft.warnings == ()


def test_training_targets_are_as_of_plus_offsets():
    template = get_template("cycle_count")
    control = _control(effective_date=date(2026, 1, 1))
    draft = render_draft(template, control=control, params={}, lang="es")
    for rendered, row in zip(draft.training, template.training):
        assert rendered.target_date == control.effective_date + timedelta(days=row.target_offset_days)


# ---- draft_to_markdown ---------------------------------------------------------------


def test_markdown_is_deterministic_across_two_renders():
    template = get_template("sop_cycle")
    control = _control()
    draft1 = render_draft(template, control=control, params={}, lang="en")
    draft2 = render_draft(template, control=control, params={}, lang="en")
    assert draft_to_markdown(draft1) == draft_to_markdown(draft2)


def test_markdown_has_no_none_or_nan():
    for key in PROCESS_KEYS:
        template = get_template(key)
        for lang in ("es", "en"):
            draft = render_draft(template, control=_control(), params={}, lang=lang)
            md = draft_to_markdown(draft)
            # exact Python str(None)/str(float("nan")) artifacts -- not a blanket substring
            # check, which would false-positive on legitimate words like "gobernanza"
            assert "None" not in md
            assert "NaN" not in md


def test_markdown_carries_the_iso_sentence_per_lang():
    template = get_template("replenishment")
    control = _control()
    draft_es = render_draft(template, control=control, params={}, lang="es")
    draft_en = render_draft(template, control=control, params={}, lang="en")
    assert ISO_SENTENCE_ES in draft_to_markdown(draft_es)
    assert ISO_SENTENCE_EN in draft_to_markdown(draft_en)


def test_markdown_table_escapes_pipes_in_control_header_cells():
    """A free-text owner/approver containing a literal '|' (e.g. --owner "Ops | Bodega")
    must not split a markdown table row into an extra cell -- reproduces the
    whole-branch review's Minor 2."""
    template = get_template("replenishment")
    control = _control(owner="Ops | Bodega")
    draft = render_draft(template, control=control, params={}, lang="es")
    md = draft_to_markdown(draft)
    owner_line = next(line for line in md.splitlines() if line.startswith("| Dueno "))
    assert owner_line == "| Dueno | Ops \\| Bodega |"


def test_markdown_table_escapes_pipes_in_a_free_text_parameter_value():
    """Same bug, different table: a free-text params['policy_source'] with a pipe must
    not corrupt the 'Parametros usados' table -- prose sections (Proposito/Alcance)
    substitute the same value unescaped on purpose, since they are not markdown
    tables, so this checks only the parameters table itself."""
    template = get_template("replenishment")
    draft = render_draft(
        template, control=_control(), params={"policy_source": "hoja | politica.xlsx"}, lang="es"
    )
    md = draft_to_markdown(draft)
    params_section = md.split("## Parametros usados")[1]
    assert "hoja | politica.xlsx" not in params_section
    assert "hoja \\| politica.xlsx" in params_section


def test_unknown_process_raises_value_error_listing_the_three():
    with pytest.raises(ValueError) as exc:
        get_template("bogus")
    message = str(exc.value)
    for key in ("replenishment", "cycle_count", "sop_cycle"):
        assert key in message


# Minor finding 4: _SafeDict.__missing__ must leave an unresolved {placeholder} in
# place instead of raising KeyError -- proven here via render_draft's public surface,
# not by reaching into the private _SafeDict class directly.
def test_unresolved_placeholder_survives_instead_of_raising():
    fake_template = ProcedureTemplate(
        key="_test_only",
        title={"es": "t", "en": "t"},
        purpose={"es": "p", "en": "p"},
        scope={"es": "s", "en": "s"},
        steps=(
            JobBreakdownStep(
                n=1,
                important_step={
                    "es": "Paso con {clave_inexistente}",
                    "en": "Step with {clave_inexistente}",
                },
                key_points={"es": ("kp",), "en": ("kp",)},
                reasons={"es": ("r",), "en": ("r",)},
            ),
        ),
        control_plan=(),
        training=(),
        controlled_inputs={"es": (), "en": ()},
        parameters={},
    )
    draft = render_draft(fake_template, control=_control(), params={}, lang="es")
    assert draft.steps[0].important_step == "Paso con {clave_inexistente}"
