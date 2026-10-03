"""Product specifications (Section 1 scope; Section 6 'Products').

An instrument names its quantity unit, the settlement whose ledger is
authoritative for it (``venue``), its maturity, and which observations its
payments depend on. Payoff functions return the total amount the long side
receives over the contract's life (negative means the long side pays), so
interim and final payments can be checked to add up to one total.
"""

from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from enum import Enum

from .assets import validate_asset
from .constants import NEODOLLAR, validate_settlement
from .errors import ValidationError


class InstrumentKind(str, Enum):
    EQUITY = "equity"        # spot share trade, delivery versus payment
    FUTURE = "future"        # cash-settled on a named observation rule
    OPTION = "option"        # cash-settled European option
    BOND = "bond"            # specification only for now
    LOAN = "loan"            # specification only for now
    CURRENCY = "currency"    # specification only for now


class ObservationRule(str, Enum):
    FINAL = "final"          # last observation at or before maturity
    AVERAGE = "average"      # mean of observations in (open, maturity]


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True, kw_only=True)
class Instrument:
    symbol: str
    kind: InstrumentKind
    venue: str                  # settlement holding the authoritative ledger
    quote_asset: str = NEODOLLAR

    def validate(self) -> None:
        if not self.symbol:
            raise ValidationError("instrument symbol required")
        validate_settlement(self.venue)
        validate_asset(self.quote_asset)

    @property
    def price_dependent(self) -> bool:
        return False

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (v.value if isinstance(v, Enum) else str(v) if isinstance(v, Decimal) else v)
                for k, v in d.items()}


@dataclass(frozen=True, kw_only=True)
class Equity(Instrument):
    kind: InstrumentKind = InstrumentKind.EQUITY
    base_asset: str = ""        # e.g. "SHR:AresHabitat"

    def validate(self) -> None:
        super().validate()
        validate_asset(self.base_asset)


@dataclass(frozen=True, kw_only=True)
class Future(Instrument):
    kind: InstrumentKind = InstrumentKind.FUTURE
    underlying: str = ""        # observed reference name, e.g. "ARES"
    multiplier: Decimal = Decimal(1)
    maturity_h: float = 0.0
    price_source: str = ""      # PriceSource principal name
    observation_rule: ObservationRule = ObservationRule.FINAL

    @property
    def price_dependent(self) -> bool:
        return True

    def validate(self) -> None:
        super().validate()
        if self.multiplier <= 0 or self.maturity_h <= 0 or not self.price_source:
            raise ValidationError(f"{self.symbol}: incomplete future specification")

    def long_payoff(self, quantity: Decimal, entry_price: Decimal, final_price: Decimal) -> Decimal:
        return quantity * self.multiplier * (final_price - entry_price)


@dataclass(frozen=True, kw_only=True)
class Option(Instrument):
    kind: InstrumentKind = InstrumentKind.OPTION
    underlying: str = ""
    option_type: OptionType = OptionType.CALL
    strike: Decimal = Decimal(0)
    multiplier: Decimal = Decimal(1)
    maturity_h: float = 0.0
    price_source: str = ""
    observation_rule: ObservationRule = ObservationRule.FINAL
    payoff_cap: Decimal | None = None  # bounded payoff per unit, if any

    @property
    def price_dependent(self) -> bool:
        return True

    def validate(self) -> None:
        super().validate()
        if self.multiplier <= 0 or self.maturity_h <= 0 or not self.price_source:
            raise ValidationError(f"{self.symbol}: incomplete option specification")

    def long_payoff(self, quantity: Decimal, final_price: Decimal) -> Decimal:
        """Exercise value to the holder; the premium is paid separately at trade."""
        if self.option_type is OptionType.CALL:
            intrinsic = max(final_price - self.strike, Decimal(0))
        else:
            intrinsic = max(self.strike - final_price, Decimal(0))
        if self.payoff_cap is not None:
            intrinsic = min(intrinsic, self.payoff_cap)
        return quantity * self.multiplier * intrinsic


@dataclass(frozen=True, kw_only=True)
class Bond(Instrument):
    kind: InstrumentKind = InstrumentKind.BOND
    issuer: str = ""
    face_value: Decimal = Decimal(0)
    coupon_rate: Decimal = Decimal(0)   # per coupon period, as a fraction of face
    coupon_interval_h: float = 0.0
    maturity_h: float = 0.0


@dataclass(frozen=True, kw_only=True)
class Loan(Instrument):
    kind: InstrumentKind = InstrumentKind.LOAN
    principal_amount: Decimal = Decimal(0)
    interest_amount: Decimal = Decimal(0)
    maturity_h: float = 0.0
    collateral_asset: str = ""


@dataclass(frozen=True, kw_only=True)
class Currency(Instrument):
    """A unit issued by one of our institutions, fully backed by encumbered assets."""
    kind: InstrumentKind = InstrumentKind.CURRENCY
    issuer: str = ""
    backing_asset: str = NEODOLLAR


INSTRUMENT_CLASSES = {cls.__name__: cls for cls in (Equity, Future, Option, Bond, Loan, Currency)}


def instrument_to_data(instrument: Instrument) -> dict:
    return {"class": type(instrument).__name__, "fields": instrument.to_dict()}


def instrument_from_data(data: dict) -> Instrument:
    cls = INSTRUMENT_CLASSES[data["class"]]
    kwargs = {}
    for f in fields(cls):
        if f.name not in data["fields"]:
            continue
        value = data["fields"][f.name]
        if value is not None:
            if f.type is Decimal or Decimal in getattr(f.type, "__args__", ()):
                value = Decimal(value)
            elif isinstance(f.type, type) and issubclass(f.type, Enum):
                value = f.type(value)
        kwargs[f.name] = value
    return cls(**kwargs)
