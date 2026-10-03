"""Signed price observations released by external sources (Section 6).

A source releases each observation as a single packet, locally, at one
settlement. How it reaches anyone else is a message the exchange records
separately.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PriceObservation:
    id: str
    source: str
    underlying: str
    price: Decimal
    released_h: float
    settlement: str
    seq: int  # per-source sequence number, part of the signed content

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "underlying": self.underlying,
                "price": str(self.price), "released_h": self.released_h,
                "settlement": self.settlement, "seq": self.seq}
