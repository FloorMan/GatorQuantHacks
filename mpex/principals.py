"""Principals: identities that hold accounts and send messages (Section 2).

* ``Account``: a named account from the opening balance sheet. Fixed for the run.
* ``Institution``: something we charter (operator, clearing, settlement,
  agent, guarantor). At most 12; each starts with nothing.
* ``PriceSource``: an external information source that releases signed
  observations at one settlement. It holds no assets.
"""

from dataclasses import dataclass, field
from enum import Enum

from .constants import validate_settlement


class PrincipalKind(str, Enum):
    ACCOUNT = "account"
    INSTITUTION = "institution"
    PRICE_SOURCE = "price_source"


class InstitutionRole(str, Enum):
    OPERATOR = "operator"      # may use the backbone
    CLEARING = "clearing"      # clearing service of an operator (backbone user)
    SETTLEMENT = "settlement"  # settlement service of an operator (backbone user)
    AGENT = "agent"
    GUARANTOR = "guarantor"

    @property
    def backbone_user(self) -> bool:
        return self in (InstitutionRole.OPERATOR, InstitutionRole.CLEARING,
                        InstitutionRole.SETTLEMENT)


@dataclass
class Principal:
    name: str
    settlement: str
    kind: PrincipalKind

    def __post_init__(self) -> None:
        validate_settlement(self.settlement)

    @property
    def may_use_backbone(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {"name": self.name, "settlement": self.settlement, "kind": self.kind.value}


@dataclass
class Account(Principal):
    kind: PrincipalKind = field(default=PrincipalKind.ACCOUNT, init=False)


@dataclass
class Institution(Principal):
    roles: frozenset = frozenset()
    operator: str | None = None  # owning operator for clearing/settlement services
    chartered_h: float = 0.0
    kind: PrincipalKind = field(default=PrincipalKind.INSTITUTION, init=False)

    @property
    def may_use_backbone(self) -> bool:
        return any(InstitutionRole(r).backbone_user for r in self.roles)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(roles=sorted(InstitutionRole(r).value for r in self.roles),
                 operator=self.operator, chartered_h=self.chartered_h)
        return d


@dataclass
class PriceSource(Principal):
    kind: PrincipalKind = field(default=PrincipalKind.PRICE_SOURCE, init=False)
