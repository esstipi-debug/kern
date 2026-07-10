"""The "Quien firma" block shown on GET /paquetes.

Cold prospects buying a fractional-operator service want to know who is actually
signing the work, not just what the engine computes. Every field is read lazily
from an env var and falls back to a TODO-OPERADOR placeholder the operator must
replace before going live -- see documentation/operator/07_setup_venta.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_PLACEHOLDER_BIO = (
    "TODO-OPERADOR: bio breve (2-3 lineas) -- quien sos, por que confiar en vos "
    "con la operacion de inventario del cliente."
)


@dataclass(frozen=True)
class OperatorProfile:
    name: str
    bio: str
    photo_url: str
    linkedin_url: str
    email: str


def get_operator_profile() -> OperatorProfile:
    return OperatorProfile(
        name=os.environ.get("OPERATOR_NAME", "TODO-OPERADOR").strip() or "TODO-OPERADOR",
        bio=os.environ.get("OPERATOR_BIO", "").strip() or _PLACEHOLDER_BIO,
        photo_url=os.environ.get("OPERATOR_PHOTO_URL", "").strip(),
        linkedin_url=os.environ.get("OPERATOR_LINKEDIN", "").strip(),
        email=os.environ.get("OPERATOR_EMAIL", "").strip(),
    )
