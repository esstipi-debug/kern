"""Post-check on the LLM narrative (brecha 8): the numbers the deterministic
engine computed must survive the rewrite that reaches the client.

The gap this closes: ``nosilo_core.llm.narrative_rewrite`` asks the model to
"Keep every number" and then verifies nothing. Its only guards are
``provider.available()``, a wide ``try/except`` and ``return text.strip() or
base_summary`` -- an emptiness check. The QA gate does look at numbers
(``jobs/qa.py``) but it runs BEFORE, and none of the repo's ``verify``
functions takes a prose string. Between the gate and the deliverable the
summary is replaced wholesale with no post-check, so a rewrite that drops or
mutates a figure ships as ``status="ok"``. That was not hypothetical:
``tests/test_scm_agent.py::test_orchestrator_narrative_upgrade_with_provider``
passed green with a provider returning "Upgraded narrative." -- a string with
no digit at all -- over a base summary reading "CHAIN 2.0/4".

WHERE THIS LIVES, AND WHY NOT INSIDE ``narrative_rewrite``. That function is in
``nosilo-core``, an external dependency pinned by tag and not in this repo. The
critic wraps Kern's two call sites instead: ``orchestrator.py::_narrative`` and
``packages.py::_run_step``. A critic on only the first would leave every
commercial package unreviewed.

SEVERITY: A FILTER, NOT A VETO. Same model the citation gate documents for
itself (see ``_step_citations`` in packages.py: "a step with zero surviving
citations still ships, the package's only hard veto stays the data QA gate")
and the same one ``src/seo/pdp_writer.py::enhance_with_llm`` already implements
for PDP prose -- it post-checks the LLM copy and reverts to the template when
the product's own name did not survive. Reverting to ``base_summary`` is also
exactly what ``narrative_rewrite`` already does when no provider is available
or the call raises; its contract says the polish is "never load-bearing". This
adds no new failure mode, it extends the existing one to the case it missed.

VALUES, NOT STRINGS. The package runner translates each step's narrative into
``PackageSpec.lang``, and Spanish swaps the thousands and decimal separators:
``1,234.5`` becomes ``1.234,5``. A critic comparing raw substrings would revert
every legitimate translation. Figures are therefore compared as digit
sequences, so the two spellings above are the same figure. The plain ASCII
space is deliberately NOT a group separator: "3 2 3 1 1" is five figures, not
32311 -- that is how this repo's own tests pass CHAIN scores.

ONE-DIRECTIONAL, ON PURPOSE. Only the disappearance of a base figure reverts.
A figure that appears in the rewrite and not in the base is reported in
``added`` and does not block, because ``narrative_rewrite``'s own prompt
appends "Ground it in these sources where relevant" with the L3 citations, and
those carry real figures ("Ch.9", "PDF p.163", "Khan et al. (2022)"). Blocking
on new figures would revert precisely the grounding the prompt asks for.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

_LOG = logging.getLogger("linchpin.narrative_critic")

#: A figure: digits, optionally grouped by ``,``/``.`` or by the TYPOGRAPHIC
#: spaces used as thousands marks (U+202F narrow no-break, U+00A0 no-break).
#: The plain ASCII space is excluded -- between digits it far more often
#: separates a list than groups one number.
_FIGURE = re.compile("\\d+(?:[.,\u202f\u00a0]\\d+)*")


def figures(text: str) -> tuple[str, ...]:
    """Every figure in ``text``, normalised to its digits, in order of appearance."""
    return tuple(re.sub(r"\D", "", m.group(0)) for m in _FIGURE.finditer(text or ""))


@dataclass(frozen=True)
class CriticResult:
    """The critic's verdict on one rewrite.

    ``summary`` is what the caller must ship: the rewrite when it survived, the
    deterministic ``base_summary`` when it did not. ``missing`` and ``added``
    are returned structurally, not only logged, so a test can assert on exactly
    which figures moved -- the same reason ``citation_gate.GateResult`` carries
    its ``omitted`` list.
    """

    summary: str
    reverted: bool
    missing: tuple[str, ...]
    added: tuple[str, ...]


def review_narrative(base_summary: str, rewritten: str, *, label: str = "") -> CriticResult:
    """Keep ``rewritten`` only if every figure of ``base_summary`` survived it.

    Never raises and never returns an empty summary: a critic that breaks the
    run would be worse than the prose it watches.
    """
    base = figures(base_summary)
    if not (rewritten or "").strip():
        # Same fallback narrative_rewrite already applies to an empty completion.
        return CriticResult(summary=base_summary, reverted=True, missing=base, added=())

    got = figures(rewritten)
    disponibles = Counter(got)
    missing: list[str] = []
    for f in base:
        if disponibles[f]:
            disponibles[f] -= 1
        else:
            missing.append(f)

    pendientes = Counter(base)
    added: list[str] = []
    for f in got:
        if pendientes[f]:
            pendientes[f] -= 1
        else:
            added.append(f)

    if missing:
        que = f"{label}: " if label else ""
        _LOG.info(
            "%snarrative rewrite dropped %d figure(s) %s - reverting to the deterministic "
            "summary: %r", que, len(missing), missing, rewritten,
        )
        return CriticResult(
            summary=base_summary, reverted=True, missing=tuple(missing), added=tuple(added),
        )
    return CriticResult(summary=rewritten, reverted=False, missing=(), added=tuple(added))
