from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional
import pandas as pd


@dataclass
class TraceEvent:
    time_days: float
    actor: str
    local_knowledge: Any
    action: str
    transmission: Any = None
    arrival: Any = None
    financial_state_after: Any = None
    event_id: Optional[str] = None
    obligation_id: Optional[str] = None


class TraceLogger:
    def __init__(self):
        self.events: list[TraceEvent] = []
        self.packets: list[dict] = []

    def event(self, **kwargs) -> None:
        self.events.append(TraceEvent(**kwargs))

    def packet(self, **kwargs) -> None:
        self.packets.append(dict(kwargs))

    def events_df(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(e) for e in self.events])

    def packets_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.packets)

    def export(self, directory) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.events_df().to_csv(directory / "trace_events.csv", index=False)
        self.packets_df().to_csv(directory / "packet_trace.csv", index=False)
