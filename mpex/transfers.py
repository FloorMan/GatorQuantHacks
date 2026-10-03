"""Value moving between settlements.

Initiating a transfer removes the value from the source ledger and holds it
in transit. Nothing travels faster than light, so it reaches the destination
ledger only when ``complete`` is recorded (normally after the settlement
instruction is delivered over the backbone). A failed transfer returns the
value to the source. Conservation: ledger supply + in-transit = opening supply.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class TransferStatus(str, Enum):
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    RETURNED = "returned"   # failed; value restored at the source


@dataclass
class Transfer:
    id: str
    owner: str
    to_owner: str
    asset: str
    amount: Decimal
    from_settlement: str
    to_settlement: str
    initiated_h: float
    status: TransferStatus = TransferStatus.IN_TRANSIT
    completed_h: float | None = None
    stated_value: Decimal | None = None  # NeoDollar value for "value settled"
    reference: str | None = None         # position/trade this pays out, if any
    message_id: str | None = None        # settlement instruction carrying it
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "owner": self.owner, "to_owner": self.to_owner,
                "asset": self.asset, "amount": str(self.amount),
                "from_settlement": self.from_settlement, "to_settlement": self.to_settlement,
                "initiated_h": self.initiated_h, "status": self.status.value,
                "completed_h": self.completed_h,
                "stated_value": None if self.stated_value is None else str(self.stated_value),
                "reference": self.reference, "message_id": self.message_id,
                "reason": self.reason}
