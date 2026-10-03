from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import numpy as np

from .config import ModelConfig, RELAYS
from .network import Incident, NetworkModel, SECONDS_PER_DAY, MINUTES_PER_DAY
from .quota import RollingQuotaTracker
from .routing import Router
from .trace import TraceLogger


@dataclass
class Session:
    session_id: str
    initiator: str
    responder: str
    route: list[str]
    established_days: float
    last_received_days: float
    alive: bool = True
    reset_handled_at_days: float | None = None


class DirectedLinkQueues:
    """FIFO 1-packet/s directed-link scheduler with a 10,000-packet cap."""

    def __init__(self, network: NetworkModel, config: ModelConfig):
        self.network = network
        self.config = config
        self.reserved_emissions: dict[tuple[str, str], list[float]] = {}

    def reset(self) -> None:
        self.reserved_emissions.clear()

    def preview(self, sender: str, receiver: str, ready_days: float,
                original_creation_days: float) -> dict:
        key = (sender, receiver)
        slots = [t for t in self.reserved_emissions.get(key, []) if t > ready_days]
        if len(slots) >= self.config.backbone_queue_capacity:
            return {"accepted": False, "status": "QUEUE_FULL", "queue_depth": len(slots)}
        serialization = self.config.serialization_seconds / SECONDS_PER_DAY
        candidate_emit = ready_days + serialization
        if slots:
            candidate_emit = max(candidate_emit, slots[-1] + serialization)
        remaining = max(0.0, original_creation_days + self.config.packet_lifetime_days - candidate_emit)
        nxt = self.network.next_open_emission_time(
            sender, receiver, candidate_emit, service="backbone", max_wait_days=remaining,
            tolerance_seconds=1.0,
        )
        if not nxt.get("found"):
            return {"accepted": False, "status": nxt.get("status", "NO_OPEN_LINK"),
                    "queue_depth": len(slots)}
        emission = float(nxt["emission_time_days"])
        if emission > original_creation_days + self.config.packet_lifetime_days:
            return {"accepted": False, "status": "PACKET_EXPIRED_BEFORE_LAUNCH",
                    "queue_depth": len(slots)}
        return {
            "accepted": True,
            "emission_time_days": emission,
            "queue_depth_before": len(slots),
            "queue_wait_seconds": max(0.0, (emission - serialization - ready_days) * SECONDS_PER_DAY),
            "known_link_wait_seconds": max(0.0, (emission - candidate_emit) * SECONDS_PER_DAY),
        }

    def enqueue(self, sender: str, receiver: str, ready_days: float,
                original_creation_days: float) -> dict:
        preview = self.preview(sender, receiver, ready_days, original_creation_days)
        if not preview.get("accepted"):
            return preview
        key = (sender, receiver)
        slots = self.reserved_emissions.setdefault(key, [])
        slots[:] = [t for t in slots if t > ready_days]
        slots.append(float(preview["emission_time_days"]))
        slots.sort()
        return preview


class BackboneTransport:
    """Brief-oriented session, hop retry, endpoint retry and queue model.

    Incident runs are conditional on no ordinary random loss by default.  Random-loss
    probabilities are still recorded on every launch; `stochastic=True` can draw them.
    """

    def __init__(self, network: NetworkModel, router: Router, config: ModelConfig,
                 trace: TraceLogger, quota: RollingQuotaTracker,
                 id_factory: Callable[[str], str], rng: np.random.Generator):
        self.network = network
        self.router = router
        self.config = config
        self.trace = trace
        self.quota = quota
        self.id_factory = id_factory
        self.rng = rng
        self.queues = DirectedLinkQueues(network, config)
        self.sessions: dict[tuple[str, str], Session] = {}

    def reset(self) -> None:
        self.queues.reset()
        self.sessions.clear()

    @staticmethod
    def _session_key(a: str, b: str) -> tuple[str, str]:
        return tuple(sorted((a, b)))

    def _reset_time_days(self, incident: Optional[Incident], endpoint: str) -> float | None:
        if incident is None or incident.kind != "endpoint_reset" or incident.node != endpoint:
            return None
        return incident.start_hours / 24.0

    def _session_usable(self, s: Session, t_days: float, incident: Optional[Incident]) -> bool:
        if not s.alive:
            return False
        if t_days - s.last_received_days >= self.config.session_expiry_days:
            s.alive = False
            self.trace.event(
                time_days=t_days, actor=f"Transport@{s.initiator}",
                local_knowledge={"session_id": s.session_id},
                action="Session expired after 7 days without received session packet",
                transmission={"session_id": s.session_id}, arrival=None,
                financial_state_after=None, event_id=s.session_id, obligation_id=None,
            )
            return False
        for endpoint in (s.initiator, s.responder):
            rt = self._reset_time_days(incident, endpoint)
            if rt is not None and s.established_days <= rt <= t_days:
                s.alive = False
                s.reset_handled_at_days = rt
                self.trace.event(
                    time_days=rt, actor=f"Transport@{endpoint}",
                    local_knowledge={"session_id": s.session_id, "endpoint_reset": True},
                    action="Endpoint reset killed session state; durable financial records retained",
                    transmission={"session_id": s.session_id, "reset_endpoint": endpoint},
                    arrival=None, financial_state_after=None,
                    event_id=s.session_id, obligation_id=None,
                )
                return False
        return True

    def _launch_outcome(self, g: dict, stochastic: bool) -> str:
        if g.get("status") == "FORCED_FAIL":
            return "LOST_INCIDENT"
        if stochastic and self.rng.random() < float(g.get("loss_probability", 0.0)):
            return "LOST_RANDOM"
        return "DELIVERED"

    def _trace_launch(self, *, packet_id: str, message_id: str, sender: str, receiver: str,
                      route: list[str], q: dict, g: dict, outcome: str, purpose: str,
                      transport_kind: str, endpoint_attempt: int, hop_attempt: int,
                      quota_exempt: bool) -> None:
        self.trace.packet(
            packet_id=packet_id,
            financial_message_id=message_id,
            service="backbone",
            sender=sender, receiver=receiver, route=route,
            emission_time_days=q.get("emission_time_days"),
            arrival_time_days=g.get("arrival_time_days") if outcome == "DELIVERED" else None,
            nominal_arrival_time_days=g.get("arrival_time_days"),
            distance_au=g.get("distance_au"), loss_probability=g.get("loss_probability"),
            outcome=outcome, purpose=purpose,
            transport_kind=transport_kind,
            endpoint_attempt=endpoint_attempt, hop_launch_attempt=hop_attempt,
            queue_depth_before=q.get("queue_depth_before"),
            queue_wait_seconds=q.get("queue_wait_seconds"),
            known_link_wait_seconds=q.get("known_link_wait_seconds"),
            quota_exempt=quota_exempt,
        )
        queue_wait = float(q.get("queue_wait_seconds") or 0.0)
        link_wait = float(q.get("known_link_wait_seconds") or 0.0)
        if queue_wait > 0.0 or link_wait > 0.0:
            self.trace.event(
                time_days=float(q.get("emission_time_days")),
                actor=f"Transport@{sender}",
                local_knowledge={
                    "queue_depth_before": q.get("queue_depth_before"),
                    "known_geometry_and_maintenance": True,
                },
                action="Launch after FIFO / next-open-link wait",
                transmission={
                    "sender": sender, "receiver": receiver, "purpose": purpose,
                    "queue_wait_seconds": queue_wait,
                    "known_link_wait_seconds": link_wait,
                    "emission_time_days": q.get("emission_time_days"),
                },
                arrival=g.get("arrival_time_days") if outcome == "DELIVERED" else None,
                financial_state_after=None, event_id=message_id, obligation_id=None,
            )

    def _hop_transfer(self, sender: str, receiver: str, ready_days: float,
                      original_creation_days: float, route: list[str], message_id: str,
                      purpose: str, endpoint_attempt: int, packet_id: str,
                      incident: Optional[Incident], stochastic: bool) -> dict:
        first_data_arrival = None
        acknowledged = False
        last_timeout = ready_days
        launch_rows = []

        next_ready = ready_days
        for hop_attempt in range(1, self.config.hop_max_launches + 1):
            q = self.queues.enqueue(sender, receiver, next_ready, original_creation_days)
            if not q.get("accepted"):
                return {"delivered": first_data_arrival is not None,
                        "arrival_time_days": first_data_arrival,
                        "acknowledged": acknowledged, "abandoned": True,
                        "status": q.get("status"), "launches": launch_rows}
            t_emit = q["emission_time_days"]
            g = self.network.link_launch(sender, receiver, t_emit, "backbone", incident=incident)
            outcome = self._launch_outcome(g, stochastic)
            self._trace_launch(
                packet_id=packet_id, message_id=message_id, sender=sender, receiver=receiver,
                route=route, q=q, g=g, outcome=outcome, purpose=purpose,
                transport_kind="DATA_OR_CONTROL", endpoint_attempt=endpoint_attempt,
                hop_attempt=hop_attempt, quota_exempt=(purpose in {"SYN_ACK", "FINAL_ACK"} or endpoint_attempt > 1 or hop_attempt > 1),
            )
            flight_min = float(g.get("light_time_minutes", 0.0))
            timeout = t_emit + (2.0 * flight_min + 60.0) / MINUTES_PER_DAY
            last_timeout = timeout
            launch_rows.append({"attempt": hop_attempt, "emission": t_emit, "outcome": outcome,
                                "timeout": timeout})

            if outcome == "DELIVERED":
                if first_data_arrival is None:
                    first_data_arrival = float(g["arrival_time_days"])
                # Hop receipt is queued immediately at the receiver; it is one-shot.
                rq = self.queues.enqueue(receiver, sender, float(g["arrival_time_days"]), original_creation_days)
                receipt_arrival = None
                if rq.get("accepted"):
                    rg = self.network.link_launch(receiver, sender, rq["emission_time_days"],
                                                  "backbone", incident=incident)
                    rout = self._launch_outcome(rg, stochastic)
                    self._trace_launch(
                        packet_id=self.id_factory("PKT"), message_id=message_id,
                        sender=receiver, receiver=sender, route=list(reversed(route)), q=rq, g=rg,
                        outcome=rout, purpose=f"HOP_RECEIPT:{purpose}", transport_kind="HOP_RECEIPT",
                        endpoint_attempt=endpoint_attempt, hop_attempt=hop_attempt, quota_exempt=True,
                    )
                    if rout == "DELIVERED":
                        receipt_arrival = float(rg["arrival_time_days"])
                        if receipt_arrival <= timeout + 1e-15:
                            acknowledged = True
                            break
                # If a late receipt arrives before the retry can actually emit, cancel retry.
                if receipt_arrival is not None:
                    candidate = timeout + self.config.serialization_seconds / SECONDS_PER_DAY
                    nxt = self.network.next_open_emission_time(sender, receiver, candidate,
                                                               service="backbone",
                                                               max_wait_days=self.config.packet_lifetime_days)
                    if nxt.get("found") and receipt_arrival < nxt["emission_time_days"]:
                        acknowledged = True
                        break
            # no receipt by R_h => retry due at timeout
            next_ready = timeout

        return {"delivered": first_data_arrival is not None,
                "arrival_time_days": first_data_arrival,
                "acknowledged": acknowledged,
                "abandoned": not acknowledged,
                "status": "DELIVERED" if first_data_arrival is not None else "HOP_ABANDONED",
                "last_timeout_days": last_timeout,
                "launches": launch_rows}

    def nominal_T0_days(self, route: list[str], t_enqueue_days: float) -> float:
        current = t_enqueue_days
        for i in range(len(route) - 1):
            current += self.config.serialization_seconds / SECONDS_PER_DAY
            lt = self.network.moving_receiver_light_time(route[i], route[i + 1], current)
            current = lt["arrival_time_days"]
            if route[i + 1] in RELAYS and i < len(route) - 2:
                current += self.config.relay_processing_seconds / SECONDS_PER_DAY
        return current - t_enqueue_days

    def _route_copy(self, route: list[str], enqueue_days: float, original_creation_days: float,
                    message_id: str, purpose: str, endpoint_attempt: int,
                    incident: Optional[Incident], stochastic: bool,
                    packet_id: str | None = None) -> dict:
        current = enqueue_days
        hop_details = []
        first_emission = None
        # One network packet ID follows the packet through relays. A new endpoint
        # attempt obtains a new ID because _route_copy is called again.
        endpoint_packet_id = packet_id or self.id_factory("PKT")
        for i in range(len(route) - 1):
            sender, receiver = route[i], route[i + 1]
            hop = self._hop_transfer(sender, receiver, current, original_creation_days,
                                     route, message_id, purpose, endpoint_attempt,
                                     endpoint_packet_id, incident, stochastic)
            hop_details.append(hop)
            if not hop["delivered"]:
                return {"delivered": False, "status": hop["status"],
                        "hop_details": hop_details, "first_emission_time_days": first_emission}
            if first_emission is None and hop["launches"]:
                first_emission = hop["launches"][0]["emission"]
            current = float(hop["arrival_time_days"])
            if receiver in RELAYS and i < len(route) - 2:
                current += self.config.relay_processing_seconds / SECONDS_PER_DAY
        return {"delivered": True, "arrival_time_days": current,
                "status": "DELIVERED", "hop_details": hop_details,
                "first_emission_time_days": first_emission}

    def endpoint_message(self, route: list[str], t_enqueue_days: float, message_id: str,
                         purpose: str, incident: Optional[Incident] = None,
                         stochastic: bool = False, first_packet_id: str | None = None) -> dict:
        original_creation = t_enqueue_days
        enqueue = t_enqueue_days
        attempts = []
        for attempt in range(1, self.config.endpoint_max_attempts + 1):
            T0 = self.nominal_T0_days(route, enqueue)
            Re = 2.0 * T0 + 1.0  # +24 h
            due = enqueue + Re
            # If a known closure/backlog prevents even the first hop from launching
            # before R_e, this endpoint copy is discarded when the retry is enqueued.
            preview = self.queues.preview(route[0], route[1], enqueue, original_creation)
            if preview.get("accepted") and preview.get("emission_time_days", enqueue) > due:
                result = {
                    "delivered": False,
                    "status": "DISCARDED_UNLAUNCHED_AT_ENDPOINT_RETRY",
                    "first_emission_time_days": preview.get("emission_time_days"),
                    "known_link_wait_seconds": preview.get("known_link_wait_seconds"),
                }
            else:
                result = self._route_copy(route, enqueue, original_creation, message_id,
                                          purpose, attempt, incident, stochastic,
                                          packet_id=(first_packet_id if attempt == 1 else None))
            result["endpoint_attempt"] = attempt
            result["enqueue_time_days"] = enqueue
            result["T0_days"] = T0
            result["endpoint_retry_deadline_days"] = due
            attempts.append(result)
            self.trace.event(
                time_days=enqueue, actor=f"Transport@{route[0]}",
                local_knowledge={"route": route, "endpoint_attempt": attempt},
                action="Endpoint attempt enqueued with R_e timer",
                transmission={"purpose": purpose, "T0_days": T0, "R_e_days": Re,
                              "retry_deadline_days": due, "status": result.get("status")},
                arrival=result.get("arrival_time_days"), financial_state_after=None,
                event_id=message_id, obligation_id=None,
            )
            if result.get("delivered"):
                # Receiver reset during flight => dead-session packet is ignored.
                rt = self._reset_time_days(incident, route[-1])
                arr = float(result["arrival_time_days"])
                if rt is not None and enqueue <= rt <= arr:
                    result["delivered"] = False
                    result["status"] = "IGNORED_DEAD_SESSION_AFTER_RESET"
                else:
                    return {"status": "DELIVERED", "arrival_time_days": arr,
                            "attempts": attempts, "endpoint_attempts_used": attempt}
            enqueue = due
            if enqueue > original_creation + self.config.packet_lifetime_days:
                break
        return {"status": "DELIVERY_UNKNOWN", "arrival_time_days": None,
                "attempts": attempts, "endpoint_attempts_used": len(attempts)}

    def ensure_session(self, initiator: str, responder: str, t_ready_days: float,
                       incident: Optional[Incident] = None,
                       stochastic: bool = False) -> dict:
        key = self._session_key(initiator, responder)
        old = self.sessions.get(key)
        if old is not None and self._session_usable(old, t_ready_days, incident):
            route = old.route if old.initiator == initiator else list(reversed(old.route))
            self.trace.event(
                time_days=t_ready_days, actor=f"Transport@{initiator}",
                local_knowledge={"session_id": old.session_id, "route": route},
                action="Reuse established backbone session",
                transmission={"session_id": old.session_id, "route": route},
                arrival=t_ready_days, financial_state_after=None,
                event_id=old.session_id, obligation_id=None,
            )
            return {"status": "REUSED", "session": old, "ready_time_days": t_ready_days,
                    "route": route}

        route_result = self.router.best_route(initiator, responder, t_ready_days,
                                              objective="reliability", allow_known_wait=True)
        if route_result is None:
            return {"status": "NO_ROUTE"}
        route = route_result["route"]
        sid = self.id_factory("SES")

        # New session SYN is an originated application/control packet and consumes quota.
        syn_id = self.id_factory("PKT")
        ok, reason = self.quota.can_originate("backbone", f"EXCHANGE@{initiator}", t_ready_days)
        if not ok:
            return {"status": reason}
        self.quota.record("backbone", f"EXCHANGE@{initiator}", t_ready_days, syn_id,
                          f"SESSION_SYN:{sid}")
        syn_msg = self.id_factory("MSG")
        syn = self.endpoint_message(route, t_ready_days, syn_msg, "SYN",
                                    incident=incident, stochastic=stochastic,
                                    first_packet_id=syn_id)
        if syn["status"] != "DELIVERED":
            return {"status": "SYN_FAILED", "detail": syn}

        # SYN-ACK and final ACK are automatic transport control and quota-exempt.
        reverse = list(reversed(route))
        synack_msg = self.id_factory("MSG")
        synack = self.endpoint_message(reverse, syn["arrival_time_days"], synack_msg, "SYN_ACK",
                                       incident=incident, stochastic=stochastic)
        if synack["status"] != "DELIVERED":
            return {"status": "SYN_ACK_FAILED", "detail": synack}
        ack_msg = self.id_factory("MSG")
        ack = self._route_copy(route, synack["arrival_time_days"], synack["arrival_time_days"],
                               ack_msg, "FINAL_ACK", 1, incident, stochastic)
        if not ack.get("delivered"):
            return {"status": "FINAL_ACK_FAILED", "detail": ack}
        established = float(ack["arrival_time_days"])
        s = Session(sid, initiator, responder, route, established, established)
        self.sessions[key] = s
        self.trace.event(
            time_days=established, actor=f"Transport@{initiator}",
            local_knowledge={"session_id": sid, "route": route},
            action="Session established after SYN / SYN-ACK / final ACK",
            transmission={"session_id": sid, "route": route},
            arrival=established, financial_state_after=None,
            event_id=sid, obligation_id=None,
        )
        return {"status": "ESTABLISHED", "session": s,
                "ready_time_days": established, "route": route}

    def send_application(self, sender: str, receiver: str, t_ready_days: float,
                         purpose: str, message_id: str,
                         incident: Optional[Incident] = None,
                         stochastic: bool = False,
                         application_resubmissions: int = 1) -> dict:
        """Send one official application packet through a reusable session.

        Initial data and any application-level resubmission consume the shared 600 quota;
        automatic endpoint/hop retries do not.
        """
        ready = t_ready_days
        histories = []
        for app_attempt in range(1, application_resubmissions + 1):
            hs = self.ensure_session(sender, receiver, ready, incident=incident,
                                     stochastic=stochastic)
            if hs.get("status") not in {"REUSED", "ESTABLISHED"}:
                return {"status": hs.get("status"), "handshake": hs, "history": histories}
            session: Session = hs["session"]
            route = hs["route"]
            ready = max(ready, float(hs["ready_time_days"]))

            app_pkt = self.id_factory("PKT")
            ok, reason = self.quota.can_originate("backbone", f"EXCHANGE@{sender}", ready)
            if not ok:
                return {"status": reason, "history": histories}
            self.quota.record("backbone", f"EXCHANGE@{sender}", ready, app_pkt, purpose)
            result = self.endpoint_message(route, ready, message_id, purpose,
                                           incident=incident, stochastic=stochastic,
                                           first_packet_id=app_pkt)
            histories.append({"application_attempt": app_attempt, "result": result,
                              "session_id": session.session_id, "route": route})
            if result["status"] == "DELIVERED":
                session.last_received_days = float(result["arrival_time_days"])
                # Sender reset during/after launch makes its local session dead for subsequent work.
                rt_sender = self._reset_time_days(incident, sender)
                if rt_sender is not None and ready <= rt_sender <= result["arrival_time_days"]:
                    session.alive = False
                return {
                    "status": "DELIVERED", "message_id": message_id,
                    "route": route, "arrival_time_days": result["arrival_time_days"],
                    "history": histories, "session_id": session.session_id,
                }

            # Application recovery across sessions. A reset endpoint knows it reset; otherwise
            # the local attempt ends unknown and this explicit resubmission is new traffic.
            session.alive = False
            if incident and incident.kind == "endpoint_reset":
                ready = max(ready, incident.start_hours / 24.0 + 1.0 / SECONDS_PER_DAY)
            else:
                ready = ready + self.config.direct_application_retry_hours / 24.0
        return {"status": "DELIVERY_UNKNOWN", "message_id": message_id, "history": histories}
