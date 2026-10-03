"""Asset codes and quantity rules.

NeoDollars use the code ``NEO``. Shares of a named company use
``SHR:<Company>``. All quantities are ``Decimal`` so balances stay exact;
share quantities must be whole numbers.
"""

from decimal import Decimal

from .constants import NEODOLLAR
from .errors import ValidationError

SHARE_PREFIX = "SHR:"


def share_asset(company: str) -> str:
    if not company or ":" in company:
        raise ValidationError(f"invalid company name {company!r}")
    return f"{SHARE_PREFIX}{company}"


def is_share(asset: str) -> bool:
    return asset.startswith(SHARE_PREFIX)


def validate_asset(asset: str) -> str:
    if asset == NEODOLLAR or (is_share(asset) and len(asset) > len(SHARE_PREFIX)):
        return asset
    raise ValidationError(f"unknown asset code {asset!r}")


def to_quantity(asset: str, amount) -> Decimal:
    """Convert ``amount`` to an exact Decimal valid for ``asset``."""
    validate_asset(asset)
    if isinstance(amount, float):
        raise ValidationError("use int, str or Decimal for amounts, not float")
    q = Decimal(amount)
    if not q.is_finite():
        raise ValidationError(f"non-finite amount {amount!r}")
    if is_share(asset) and q != q.to_integral_value():
        raise ValidationError(f"share quantities must be whole: {amount!r}")
    return q
