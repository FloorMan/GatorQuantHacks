from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
import hashlib
import pandas as pd

from .config import ModelConfig, SETTLEMENTS
from .encoding import FixedWidthApplicationEncoding, FinancialRecord
from .quota import RollingQuotaTracker

SECONDS_PER_DAY = 86400.0


@dataclass
class WorkloadSummary:
    feasible: bool
    direct_packets: int
    backbone_originations: int
    max_backbone_rolling_24h: int
    max_direct_rolling_24h: Dict[str, int]


class WorkloadPlanner:
    """Timestamped recurring application workload using the real fixed-width wire format.

    Every workload row corresponds to a concrete 1,024-byte network packet.  For
    financial application packets we actually build the 960-byte application payload
    with :class:`FixedWidthApplicationEncoding`; packet counts therefore come from the
    same encoding used by the final model rather than a screening-only records/packet
    assumption.

    Baseline operating procedure:
      * remote client instruction: one ORDER record/packet;
      * remote exchange response: one application-control record/packet;
      * regional official coordination: exact instruction records distributed across
        four six-hour batches and packed 9 records/packet;
      * one CLEAR_CONFIRM record/packet for each received regional batch packet;
      * persistent backbone session SYNs are quota-counted application-control packets.
    """

    def __init__(self, config: ModelConfig, router=None):
        self.config = config
        self.router = router
        self.encoding = FixedWidthApplicationEncoding

    @staticmethod
    def exchange_principal(exchange: str) -> str:
        return f"EXCHANGE@{exchange}"

    @staticmethod
    def client_principal(settlement: str) -> str:
        return f"CLIENT@{settlement}"

    @staticmethod
    def _record_code(message_type: str) -> str:
        return {
            "ORDER": "OR",
            "APPLICATION_CONTROL": "AC",
            "BATCH": "BT",
            "CLEAR_CONFIRM": "CC",
        }.get(message_type, "AP")

    @staticmethod
    def _distribute_records(total: int, batches: int) -> list[int]:
        """Distribute exactly ``total`` records across ``batches`` without overcounting."""
        q, r = divmod(int(total), int(batches))
        return [q + (1 if i < r else 0) for i in range(batches)]

    def build(self, assignment: Dict[str, str], core: str, exchanges: tuple[str, ...],
              horizon_days: int = 3) -> tuple[RollingQuotaTracker, pd.DataFrame, WorkloadSummary]:
        q = RollingQuotaTracker(self.config)
        rows = []
        seq = 0
        message_seq = 0
        record_seq = 0

        def make_records(count: int, message_type: str, purpose: str,
                         created_days: float) -> list[FinancialRecord]:
            nonlocal record_seq
            out = []
            for _ in range(count):
                record_seq += 1
                out.append(FinancialRecord(
                    record_type=self._record_code(message_type),
                    object_id=f"W{record_seq:015d}"[-16:],
                    symbol="ARES",
                    quantity=1,
                    event_time_us=int(round(created_days * SECONDS_PER_DAY * 1_000_000)),
                    aux_micros=0,
                ))
            return out

        def add(service: str, principal: str, t_days: float, purpose: str,
                *, message_type: str = "APPLICATION_CONTROL",
                records: list[FinancialRecord] | None = None,
                quota_exempt: bool = False,
                application_packet: bool = True):
            nonlocal seq, message_seq
            requested = float(t_days)

            # Direct has no transport queue; schedule at earliest legally spaced time.
            if service == "direct" and not quota_exempt:
                prior = [
                    r.time_days for r in q.records
                    if r.service == "direct" and not r.quota_exempt
                    and r.principal == principal and r.time_days > requested - 1.0
                ]
                if prior:
                    t_days = max(
                        requested,
                        max(prior) + self.config.direct_min_launch_spacing_seconds / SECONDS_PER_DAY,
                    )

            seq += 1
            message_seq += 1
            pid = f"WL-{seq:08d}"
            mid = message_seq
            if records is None:
                records = make_records(0, message_type, purpose, float(t_days))

            # Build the actual fixed-width payload for every application/control packet.
            payload = None
            payload_sha256 = None
            if application_packet:
                payload = self.encoding.encode_payload(
                    records,
                    message_type=message_type,
                    message_id=mid,
                    batch_id=mid if message_type == "BATCH" else 0,
                    created_time_us=int(round(float(t_days) * SECONDS_PER_DAY * 1_000_000)),
                )
                payload_sha256 = hashlib.sha256(payload).hexdigest()

            ok, reason = (True, "EXEMPT") if quota_exempt else q.can_originate(
                service, principal, float(t_days)
            )
            rows.append({
                "packet_id": pid,
                "message_id": mid,
                "service": service,
                "principal": principal,
                "requested_time_days": requested,
                "time_days": float(t_days),
                "time_hours": float(t_days) * 24.0,
                "time_seconds": float(t_days) * SECONDS_PER_DAY,
                "purpose": purpose,
                "message_type": message_type,
                "record_count": len(records),
                "application_payload_bytes": len(payload) if payload is not None else None,
                "network_header_bytes": self.encoding.NETWORK_HEADER_BYTES if application_packet else None,
                "network_packet_bytes": self.encoding.PACKET_BYTES if application_packet else None,
                "payload_sha256": payload_sha256,
                "quota_exempt": quota_exempt,
                "quota_ok_before": ok,
                "quota_reason": reason,
            })
            if not ok:
                return False
            q.record(
                service, principal, float(t_days), pid, purpose,
                quota_exempt=quota_exempt,
                application_packet=application_packet,
            )
            return True

        # Persistent exchange->core sessions established six hours before hour 0.
        # SYN is quota-counted. The control packet has an envelope and zero financial records.
        for idx, e in enumerate(sorted(exchanges)):
            if e == core:
                continue
            add(
                "backbone", self.exchange_principal(e),
                -0.25 + idx / SECONDS_PER_DAY,
                f"SESSION_SYN_{e}_TO_{core}",
                message_type="APPLICATION_CONTROL", records=[],
            )

        batch_hours = self.config.batch_interval_hours
        batches_per_day = int(round(24.0 / batch_hours))

        for day in range(horizon_days):
            day0 = float(day)

            # Remote client instruction and exchange response: exactly one record each.
            for s in SETTLEMENTS:
                e = assignment[s]
                demand = int(self.config.demand_per_day[s])
                for j in range(demand):
                    t = day0 + (j + 0.5) / demand
                    if s != e:
                        rec = make_records(1, "ORDER", f"ORDER_{s}_TO_{e}", t)
                        add(
                            "direct", self.client_principal(s), t,
                            f"ORDER_{s}_TO_{e}", message_type="ORDER", records=rec,
                        )
                        response_t = t + (120.0 + j) / SECONDS_PER_DAY
                        resp = make_records(
                            1, "APPLICATION_CONTROL", f"ORDER_RESPONSE_{e}_TO_{s}", response_t
                        )
                        add(
                            "direct", self.exchange_principal(e), response_t,
                            f"ORDER_RESPONSE_{e}_TO_{s}",
                            message_type="APPLICATION_CONTROL", records=resp,
                        )

            # Exact regional official-coordination workload.
            for e in exchanges:
                if e == core:
                    continue
                assigned = [s for s in SETTLEMENTS if assignment[s] == e]
                volume_day = int(sum(self.config.demand_per_day[s] for s in assigned))
                batch_counts = self._distribute_records(volume_day, batches_per_day)

                for b, batch_records in enumerate(batch_counts, start=1):
                    if batch_records == 0:
                        continue
                    batch_t = day0 + b * batch_hours / 24.0
                    # Create exactly the records assigned to this six-hour batch and split at 9/packet.
                    all_records = make_records(batch_records, "BATCH", f"BATCH_{e}_{b}", batch_t)
                    for p0 in range(0, len(all_records), self.encoding.RECORDS_PER_PACKET):
                        packet_records = all_records[p0:p0 + self.encoding.RECORDS_PER_PACKET]
                        packet_no = p0 // self.encoding.RECORDS_PER_PACKET + 1
                        t = batch_t + packet_no / SECONDS_PER_DAY
                        sent = add(
                            "backbone", self.exchange_principal(e), t,
                            f"BATCH_{e}_TO_{core}_{b}_P{packet_no}",
                            message_type="BATCH", records=packet_records,
                        )
                        if not sent:
                            continue

                        # Core confirmation is created only after the regional batch can arrive.
                        confirm_t = t + 1.0
                        if self.router is not None:
                            rr = self.router.best_route(
                                e, core, t, objective="reliability", allow_known_wait=True
                            )
                            if rr is not None:
                                confirm_t = (
                                    rr["arrival_time_days"]
                                    + self.config.local_access_seconds / SECONDS_PER_DAY
                                )
                        confirm_records = make_records(
                            1, "CLEAR_CONFIRM", f"BATCH_CONFIRM_{core}_TO_{e}_{b}_P{packet_no}",
                            confirm_t,
                        )
                        add(
                            "backbone", self.exchange_principal(core), confirm_t,
                            f"BATCH_CONFIRM_{core}_TO_{e}_{b}_P{packet_no}",
                            message_type="CLEAR_CONFIRM", records=confirm_records,
                        )

        df = pd.DataFrame(rows).sort_values(["time_days", "packet_id"]).reset_index(drop=True)
        direct_principals = sorted({r.principal for r in q.records if r.service == "direct"})
        direct_max = {p: q.max_rolling_count("direct", p) for p in direct_principals}
        summary = WorkloadSummary(
            feasible=bool(df["quota_ok_before"].all()),
            direct_packets=sum(
                1 for r in q.records if r.service == "direct" and r.time_days >= 0 and not r.quota_exempt
            ),
            backbone_originations=sum(
                1 for r in q.records if r.service == "backbone" and r.time_days >= 0 and not r.quota_exempt
            ),
            max_backbone_rolling_24h=q.max_rolling_count("backbone"),
            max_direct_rolling_24h=direct_max,
        )
        return q, df, summary
