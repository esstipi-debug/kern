"""Step 5 of A5's integrated-planning pipeline: WHEN the purchase plan's money
actually leaves the bank.

Steps 1-4 answer "how much do we buy". A total is not a cash decision: the
question a buyer is actually held to is whether the disbursements FIT, week by
week, into the working capital the business has that week. Two portfolios with
the same 1,860 usd total are different businesses if one pays it all on Monday
and the other spreads it over six weeks.

This is the accounts-payable leg of the Cash-to-Cash Cycle Time (SCOR level-1
metric AM.1.1; Vollmann et al., *Manufacturing Planning and Control for Supply
Chain Management*, ch. 2 on the financial consequences of the plan), which this
repo already computes AGGREGATED as days: ``src.financial_kpis.cash_to_cash``
takes a single portfolio ``dpo``. A DPO of 30 says nothing about which week the
money leaves. This module is the same money seen PER LINE and PER WEEK instead
of per period, which is the resolution a purchase decision is made at.

**The payment term is an EXTERNAL INPUT, like ``incoming_po``** (see
``purchase_plan``'s module docstring for that parallel): it comes from the
supplier contract / AP master, and this module never infers it from the data.
When the caller does not supply it, the line falls back to the plan's declared
``default_payment_terms`` -- and every line carries :data:`TERMS_ASSUMED` in
``payment_terms_source`` so the calendar can never be read as if the plazo had
been looked up. A silent default here would date someone's cash outflow on an
assumption they were never shown.

:data:`DEFAULT_PAYMENT_TERMS` is the fallback for a caller who declared no
``default_payment_terms`` EITHER, never necessarily the term in force. Any text
naming the plazo therefore reads ``default_terms_in_effect`` off the schedule
and never that constant: interpolating the constant printed "net_30" over a
calendar a caller had dated on net 90.

Pure, no I/O, and no ``date.today()``: the calendar is either anchored to an
``as_of`` the caller supplies or expressed in weeks relative to the order date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from src.sop_engine.purchase_plan import PurchasePlanLine

#: Paid on delivery / prepaid: the money leaves in week 0.
CASH = "cash"
NET_15 = "net_15"
NET_30 = "net_30"
NET_45 = "net_45"
NET_60 = "net_60"
NET_90 = "net_90"

#: What a line's terms are when the caller supplied none. THIRTY DAYS IS AN
#: ASSUMPTION, not a reading: net 30 is the most common commercial term in the
#: SMB supplier base this planner targets, and it is stated here -- once, named,
#: importable -- precisely so no caller has to guess what the number was. Every
#: line built on it is stamped :data:`TERMS_ASSUMED`; see the module docstring.
DEFAULT_PAYMENT_TERMS = NET_30

#: The caller (or the file) stated this line's payment condition.
TERMS_STATED = "stated"
#: Nobody stated it: the line runs on :data:`DEFAULT_PAYMENT_TERMS`.
TERMS_ASSUMED = "assumed_default"

DAYS_PER_WEEK = 7

#: What counts as "the same amount of money" once a week's disbursement is a
#: RUNNING SUM of per-line ``order_value`` products. Same value as
#: ``src.sop_engine.coherence._TOL``, restated here rather than imported because
#: ``coherence`` imports ``purchase_plan`` which imports this module -- the
#: import would close a cycle. Per-module tolerance constants are the repo's
#: existing shape for this (``src.logistics.routing._CAPACITY_TOL``).
_TOL = 1e-6

#: Spellings of "no credit at all" that a procurement export actually contains.
_CASH_WORDS = frozenset({"cash", "contado", "cod", "prepaid", "immediate", "net_0"})

_NET_PREFIXES = ("net_", "neto_", "n_")


def _opens_with_a_minus_sign(text: str) -> bool:
    """Whether some ``-`` in ``text`` is a MINUS SIGN rather than the separator
    in ``"net-30"``.

    :func:`parse_payment_terms` turns ``-`` into a space so ``"net-30"`` reads,
    and that same replace ate the SIGN of ``"-30"``, which came back out as a
    positive ``net_30`` -- against this module's own contract that a term it
    cannot read must raise. The two uses are told apart by what sits BEFORE the
    dash: a separator joins two tokens (``"net-30"``, ``"30-60"``), a sign opens
    one (``"-30"``, ``"net -30"``, ``"net_-30"``).
    """
    return any(
        text[i + 1:i + 2].isdigit() and not text[i - 1:i].isalnum()
        for i, char in enumerate(text)
        if char == "-"
    )


def parse_payment_terms(text: str) -> tuple[str, int]:
    """Normalise one supplier payment condition into ``(token, net_days)``.

    Accepts what a real AP export holds -- ``"net_30"``, ``"NET 30"``,
    ``"net-30"``, ``"30"``, ``"30 days"``, ``"cash"``, ``"contado"``, ``"COD"``
    -- and RAISES ``ValueError`` naming the text it could not read. Unknown
    terms must not fall back to the default: the default exists for the line
    that said NOTHING, and quietly treating "45/60 split" as net 30 would put a
    number nobody wrote into a cash calendar (repo convention: fail fast at a
    system boundary, and never let an unclassified code land as a value).

    ``"30.0"`` reads as 30 days. Nobody types that: it is what a spreadsheet
    export or a pandas ``float64`` column (one blank cell is enough to force
    the dtype) makes of a plain ``30``, so refusing it rejected a term the
    client did state. A NON-ZERO fraction is still unreadable -- half a
    commercial day does not exist, and rounding it either way would date
    someone's disbursement on a plazo nobody agreed to.

    A NEGATIVE plazo is unreadable too, and is rejected BEFORE the normalising
    replace that would otherwise erase its sign (see
    :func:`_opens_with_a_minus_sign`). :func:`payment_week` already refuses
    ``net_days < 0``; the only way a negative term ever reached a calendar was
    by arriving there with the minus already lost.
    """
    raw = str(text).strip()
    if _opens_with_a_minus_sign(raw):
        raise ValueError(
            f"unreadable payment terms {text!r}: a negative plazo would date the "
            f"disbursement before the order that causes it"
        )
    token = "_".join(raw.lower().replace("-", " ").replace("/", " ").split())
    if not token:
        raise ValueError("payment terms are empty: pass a term or leave the field unstated")
    if token in _CASH_WORDS:
        return CASH, 0
    digits = token
    for prefix in _NET_PREFIXES:
        if digits.startswith(prefix):
            digits = digits[len(prefix):]
            break
    digits = digits.removesuffix("_days").removesuffix("_dias").removesuffix("_d")
    whole, dot, fraction = digits.partition(".")
    if dot and not fraction.strip("0"):
        digits = whole  # "30.0" is a float64 column's rendering of the "30" the file holds
    if not digits.isdigit():
        raise ValueError(
            f"unreadable payment terms {text!r}: expected net_<days>, a plain number of days, "
            f"or one of {sorted(_CASH_WORDS)}"
        )
    days = int(digits)
    return (CASH if days == 0 else f"net_{days}"), days


def resolve_payment_terms(
    stated: str, default: str = DEFAULT_PAYMENT_TERMS
) -> tuple[str, int, str]:
    """``(token, net_days, source)`` for one line, declaring WHICH of the two it is.

    An empty ``stated`` is not an error -- it is the ordinary case where the
    stock/supplier file has no payment-terms column at all. What is not allowed
    is for the resulting line to look identical to one whose plazo was read from
    a contract, which is what ``source`` prevents.
    """
    if str(stated).strip():
        token, days = parse_payment_terms(stated)
        return token, days, TERMS_STATED
    token, days = parse_payment_terms(default)
    return token, days, TERMS_ASSUMED


def payment_week(net_days: int) -> int:
    """The week the due date FALLS IN, counted from the order date (week 0).

    Floor division on purpose. This is a VENCIMIENTO landing in a bucket, not a
    duration being covered: net 30 is due on day 30, which is inside week 4, and
    net 6 is due inside week 0. ``src.liquidation_calendar._step_duration_weeks``
    rounds the other way (``ceil``, floored at 1) because it measures how many
    weeks a recovery is SPREAD OVER -- a span always occupies at least one week,
    while a payment due today occupies none.
    """
    if net_days < 0:
        raise ValueError(f"net_days must be >= 0, got {net_days}")
    return net_days // DAYS_PER_WEEK


#: Net days behind :data:`DEFAULT_PAYMENT_TERMS`, resolved once so no caller
#: restates "30" as a literal and the two can never drift apart.
DEFAULT_NET_DAYS = parse_payment_terms(DEFAULT_PAYMENT_TERMS)[1]
DEFAULT_PAYMENT_WEEK = payment_week(DEFAULT_NET_DAYS)


@dataclass(frozen=True)
class CashCommitmentWeek:
    """One week of the disbursement calendar.

    ``week_start`` is an ISO date when the caller anchored the plan with an
    ``as_of``, and the EMPTY STRING when it did not -- never a fabricated
    today(). A blank there means "week 4 after whenever you place these orders",
    which is a weaker but honest statement.
    """

    week: int
    week_start: str
    disbursement: float
    cumulative: float
    cap: float | None
    headroom: float | None
    within_cap: bool
    n_lines: int


@dataclass(frozen=True)
class CashCommitmentSchedule:
    """The purchase plan's money, laid out on a weekly axis and compared with
    the weekly cash the business said it has.

    ``terms_assumed_for`` is the part a reader must not be able to skip: the
    SKUs whose payment week rests on an assumed plazo. A calendar that cannot
    name those is a calendar that reads as measured. It names the SKUs THIS
    CALENDAR DATES -- a line that orders nothing has no payment week here for an
    assumption to rest on, and listing it would inflate the warning with lines
    whose disbursement is zero.

    ``default_terms_in_effect`` says WHICH plazo that was, and it is read back
    off the lines rather than restated from :data:`DEFAULT_PAYMENT_TERMS`.
    ``build_purchase_plan`` takes ``default_payment_terms`` as a PARAMETER, so
    the module constant is only the fallback, never necessarily the term in
    force: a plan run on net 90 dated its lines in week 12 while every sentence
    about it said net 30 -- a plazo contradicting the calendar printed beside
    it. Empty when this calendar dates no assumed line: there is no default in
    force to name, and naming one anyway is the same fabrication moved
    somewhere quieter.
    """

    weeks: tuple[CashCommitmentWeek, ...]
    total_committed: float
    cap_per_week: float | None
    n_weeks_over_cap: int
    first_week_over_cap: int | None
    terms_assumed_for: tuple[str, ...]
    default_terms_in_effect: str
    summary: str

    @property
    def peak_week(self) -> CashCommitmentWeek | None:
        """The week the plan asks the most of the bank account."""
        return max(self.weeks, key=lambda w: w.disbursement, default=None)


def _week_start(as_of: date | None, week: int) -> str:
    return "" if as_of is None else (as_of + timedelta(days=DAYS_PER_WEEK * week)).isoformat()


def _lay_out_weeks(
    by_week: dict[int, float],
    n_lines: dict[int, int],
    cap_per_week: float | None,
    as_of: date | None,
) -> tuple[CashCommitmentWeek, ...]:
    """Weeks 0..last, CONTIGUOUS. A week with no egress is emitted with a zero
    rather than skipped: "nothing is due that week" is what a cash plan is read
    for, and a calendar with holes cannot be scanned for the quiet week.

    ``within_cap`` carries :data:`_TOL` because ``out`` is a running sum of
    ``quantity * unit_cost`` products: three 1.10 usd lines against a 3.30 usd
    cap add up to 3.3000000000000003, and the bare ``<=`` reported the plan that
    fits EXACTLY as over its cap -- a red finding reading "over by 0 usd".
    ``headroom`` stays the exact arithmetic difference, tolerance and all: the
    tolerance belongs to the VERDICT, and rounding the number would hide how
    close the week actually ran.
    """
    horizon = max(by_week) if by_week else 0
    weeks: list[CashCommitmentWeek] = []
    cumulative = 0.0
    for week in range(horizon + 1):
        out = by_week.get(week, 0.0)
        cumulative += out
        weeks.append(CashCommitmentWeek(
            week=week,
            week_start=_week_start(as_of, week),
            disbursement=out,
            cumulative=cumulative,
            cap=cap_per_week,
            headroom=None if cap_per_week is None else cap_per_week - out,
            within_cap=True if cap_per_week is None else out <= cap_per_week + _TOL,
            n_lines=n_lines.get(week, 0),
        ))
    return tuple(weeks)


def _summary(
    schedule_weeks: tuple[CashCommitmentWeek, ...],
    total: float,
    cap_per_week: float | None,
    assumed: tuple[str, ...],
    assumed_terms: str,
    as_of: date | None,
) -> str:
    """``assumed_terms`` is the plazo those lines were actually dated on, NOT
    :data:`DEFAULT_PAYMENT_TERMS`: the two differ whenever the caller passed
    ``default_payment_terms``, and this sentence sits next to the week numbers
    that plazo produced."""
    over = [w for w in schedule_weeks if not w.within_cap]
    text = (
        f"{total:,.0f} usd of purchase commitments across {len(schedule_weeks)} week(s)"
    )
    if cap_per_week is None:
        text += "; no weekly cash cap supplied, so nothing was contrasted against one"
    elif over:
        text += (
            f"; {len(over)} week(s) exceed the {cap_per_week:,.0f} usd weekly cap, "
            f"first in week {over[0].week} by {over[0].disbursement - cap_per_week:,.0f} usd"
        )
    else:
        text += f"; every week fits the {cap_per_week:,.0f} usd weekly cap"
    if assumed:
        text += (
            f". Payment terms were ASSUMED ({assumed_terms}) for {len(assumed)} line(s): "
            + ", ".join(assumed[:5]) + ("..." if len(assumed) > 5 else "")
        )
    if as_of is None:
        text += ". Weeks are relative to the order date; no as_of was supplied to date them"
    return text + "."


def _bucket_by_week(
    lines: Iterable[PurchasePlanLine],
) -> tuple[dict[int, float], dict[int, int], tuple[str, ...], str]:
    """One pass over the plan: money per week, lines per week, WHOSE plazo was
    assumed, and WHICH plazo that was.

    Split out of :func:`build_cash_commitment_schedule` so that function reads
    as the assembly it is, and so the two rules that hang off the
    ``order_value <= 0`` filter sit next to the filter itself.

    A line that orders nothing is skipped whole: no money leaves, so there is no
    week to book it in. The assumed-terms bookkeeping is therefore also AFTER
    the filter. The deck turns it into "N line(s) carry no supplier payment
    condition", a warning about the DATES IN THIS CALENDAR; a line that books no
    disbursement puts no date here for an assumption to rest on, and counting it
    inflates the warning with lines that cost the reader nothing. (A stock file
    with no payment-terms column at all is a data-quality finding about the
    FILE, which is a different claim.)

    The plazo is read OFF the lines, never restated from
    :data:`DEFAULT_PAYMENT_TERMS` -- ``build_purchase_plan`` resolves it against
    ITS ``default_payment_terms``, which the caller may have set. Distinct terms
    are joined rather than collapsed to the first: picking one of two would name
    a term half these lines never ran on, the same defect one level down.
    """
    by_week: dict[int, float] = {}
    n_lines: dict[int, int] = {}
    assumed: list[str] = []
    assumed_terms: dict[str, None] = {}  # ordered set
    for line in lines:
        if line.order_value <= 0:
            continue
        if line.payment_terms_source == TERMS_ASSUMED:
            assumed.append(line.product_id)
            assumed_terms[line.payment_terms] = None
        week = line.payment_due_week
        by_week[week] = by_week.get(week, 0.0) + line.order_value
        n_lines[week] = n_lines.get(week, 0) + 1
    return by_week, n_lines, tuple(assumed), ", ".join(assumed_terms)


def build_cash_commitment_schedule(
    lines: Iterable[PurchasePlanLine],
    *,
    cap_per_week: float | None = None,
    as_of: date | None = None,
) -> CashCommitmentSchedule:
    """Group the purchase plan's ``order_value`` by the week each line is DUE.

    ``cap_per_week`` is the working capital the business says it can disburse in
    one week; when omitted, the calendar is still produced and simply carries no
    verdict (``within_cap`` is ``True`` everywhere and the summary says why) --
    the same contract ``build_purchase_plan`` keeps for an absent ``budget``,
    never a fabricated cap.
    """
    if cap_per_week is not None and cap_per_week < 0:
        raise ValueError("cap_per_week must be >= 0")

    by_week, n_lines, assumed, in_effect = _bucket_by_week(lines)
    weeks = _lay_out_weeks(by_week, n_lines, cap_per_week, as_of)
    over = [w.week for w in weeks if not w.within_cap]
    total = sum(by_week.values())
    return CashCommitmentSchedule(
        weeks=weeks,
        total_committed=total,
        cap_per_week=cap_per_week,
        n_weeks_over_cap=len(over),
        first_week_over_cap=over[0] if over else None,
        terms_assumed_for=assumed,
        default_terms_in_effect=in_effect,
        summary=_summary(weeks, total, cap_per_week, assumed, in_effect, as_of),
    )
