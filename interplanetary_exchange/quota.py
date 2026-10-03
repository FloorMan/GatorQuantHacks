from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
import pandas as pd

from .config import ModelConfig
from .network import SECONDS_PER_DAY


@dataclass(frozen=True)
class OriginationRecord:
    service: str
    principal: str
    time_days: float
    packet_id: str
    purpose: str
    quota_exempt: bool = False
    application_packet: bool = True


class RollingQuotaTracker:
    """Exact timestamped rolling-window accounting for originated packets.

    The brief's windows are continuous, not calendar-day buckets.  Each non-exempt
    origination is stored at its exact simulation timestamp and checked against the
    preceding 24 h interval ``(t-24h, t]``.  Direct traffic is additionally checked
    against the 60-second minimum spacing for that principal.
    """

    def __init__(self, config: ModelConfig):
        self.config = config
        self.records: list[OriginationRecord] = []

    def reset(self) -> None:
        self.records.clear()

    def _window(self, service: str, t_days: float,
                principal: Optional[str] = None) -> list[OriginationRecord]:
        cutoff = t_days - 1.0
        out = [
            r for r in self.records
            if r.service == service and not r.quota_exempt
            and cutoff < r.time_days <= t_days
        ]
        if principal is not None:
            out = [r for r in out if r.principal == principal]
        return out

    def can_originate(self, service: str, principal: str,
                      t_days: float) -> tuple[bool, str]:
        if service == "direct":
            recent = self._window("direct", t_days, principal)
            if len(recent) >= self.config.direct_packets_per_principal_rolling_24h:
                return False, "DIRECT_ROLLING_24H_QUOTA"
            if recent:
                last = max(r.time_days for r in recent)
                if ((t_days - last) * SECONDS_PER_DAY
                        < self.config.direct_min_launch_spacing_seconds - 1e-9):
                    return False, "DIRECT_60S_SPACING"
            return True, "OK"
        if service == "backbone":
            recent = self._window("backbone", t_days)
            if len(recent) >= self.config.backbone_originations_per_rolling_24h:
                return False, "BACKBONE_ROLLING_24H_QUOTA"
            return True, "OK"
        raise ValueError(service)

    def record(self, service: str, principal: str, t_days: float,
               packet_id: str, purpose: str, *, quota_exempt: bool = False,
               application_packet: bool = True) -> OriginationRecord:
        if not quota_exempt:
            ok, reason = self.can_originate(service, principal, t_days)
            if not ok:
                raise ValueError(reason)
        rec = OriginationRecord(
            service=service, principal=principal, time_days=float(t_days),
            packet_id=packet_id, purpose=purpose, quota_exempt=quota_exempt,
            application_packet=application_packet,
        )
        self.records.append(rec)
        return rec

    def max_rolling_count(self, service: str,
                          principal: Optional[str] = None) -> int:
        times = sorted(
            r.time_days for r in self.records
            if r.service == service and not r.quota_exempt
            and (principal is None or r.principal == principal)
        )
        best = 0
        left = 0
        for right, t in enumerate(times):
            while left <= right and times[left] <= t - 1.0:
                left += 1
            best = max(best, right - left + 1)
        return best

    def dataframe(self) -> pd.DataFrame:
        """Per-packet quota ledger with the exact count *after that packet*.

        This intentionally does not call ``_window`` against the completed record
        collection, because several packets can share a timestamp.  Instead it
        replays the ledger in deterministic timestamp/packet-id order so the
        ``rolling_24h_count_after`` column is truly packet-by-packet.
        """
        ordered = sorted(self.records, key=lambda x: (x.time_days, x.packet_id))
        backbone_window: list[OriginationRecord] = []
        direct_windows: dict[str, list[OriginationRecord]] = {}
        previous_direct_time: dict[str, float] = {}
        rows = []

        for r in ordered:
            d = asdict(r)
            d["time_hours"] = r.time_days * 24.0
            d["time_seconds"] = r.time_days * SECONDS_PER_DAY
            d["rolling_window_start_days"] = r.time_days - 1.0
            d["rolling_window_start_hours"] = (r.time_days - 1.0) * 24.0
            d["seconds_since_previous_direct_origination"] = None

            if r.quota_exempt:
                d["rolling_24h_count_after"] = None
                d["rolling_24h_limit"] = None
                d["rolling_24h_remaining_after"] = None
                rows.append(d)
                continue

            cutoff = r.time_days - 1.0
            if r.service == "backbone":
                backbone_window = [x for x in backbone_window if x.time_days > cutoff]
                backbone_window.append(r)
                count = len(backbone_window)
                limit = self.config.backbone_originations_per_rolling_24h
            elif r.service == "direct":
                w = direct_windows.setdefault(r.principal, [])
                w[:] = [x for x in w if x.time_days > cutoff]
                prior = previous_direct_time.get(r.principal)
                if prior is not None:
                    d["seconds_since_previous_direct_origination"] = (
                        r.time_days - prior
                    ) * SECONDS_PER_DAY
                w.append(r)
                previous_direct_time[r.principal] = r.time_days
                count = len(w)
                limit = self.config.direct_packets_per_principal_rolling_24h
            else:
                raise ValueError(r.service)

            d["rolling_24h_count_after"] = count
            d["rolling_24h_limit"] = limit
            d["rolling_24h_remaining_after"] = limit - count
            rows.append(d)

        return pd.DataFrame(rows)
