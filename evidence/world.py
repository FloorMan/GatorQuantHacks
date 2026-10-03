"""Discrete-event driver: runs an ``mpex.Exchange`` on the network model in time order.

Every financial command and every packet launch happens at its simulated time,
in order, and the exchange's invariants are checked after each step. Timing
comes from ``network/python/network_graph.py`` (moving-receiver light time,
solar exclusion, maintenance), and the transport follows Section 5:

* Sessions are set up before hour 0 with SYN, SYN-ACK and ACK along a pinned route.
* Each hop: 1 s serialization, launch, hop receipt on the reverse link; retry if
  no receipt by R_h = 2 x flight + 60 min, at most 4 launches. Relays add 1 s
  and forward only the first copy.
* End to end: the destination acknowledges data; without an acknowledgment by
  R_e = 2 x T0 + 24 h the sender re-sends (new packet id, same bytes), at most
  4 attempts. Duplicate data is delivered to the application only once.
* A sender waits for a known closure (Sun, maintenance). Incidents are not
  announced: a launch emitted inside one simply fails.

This is a conditional (no random loss) trace. Losses happen only where a test
forces them or an incident covers the launch, and each one is labelled.
"""

import heapq
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "network" / "python"))
sys.path.insert(0, str(ROOT))

import network_graph as ng  # noqa: E402
from mpex import MessageStatus, PacketKind, SessionState  # noqa: E402
from mpex.constants import MAX_ENDPOINT_ATTEMPTS, MAX_HOP_LAUNCHES, PACKET_LIFETIME_H  # noqa: E402

SEC_H = 1 / 3600
# Shifted epochs (brief E5): scenario hour 0 sits this many hours after the epoch. Only
# geometry moves; scenario times, balances and the exchange journal stay 0-based.
EPOCH_OFFSET_H = float(os.environ.get("MPEX_EPOCH_OFFSET_H", "0"))


class World:
    def __init__(self, ex):
        self.ex = ex
        self.net = ng.System()
        self._queue = []
        self._n = 0
        self.now = None
        self.incidents = []    # {"kind": "isolation"|"forced_loss", "node", "start_h", "end_h"}
        self.drop = None       # callable(info dict) -> reason str | None (forced test losses)
        self.sessions = {}     # frozenset({a, b}) -> {"id", "initiator", "route"}
        self.launch_log = []   # one row per launch, for evidence
        self.messages = {}     # message id -> endpoint state
        self.violations = []   # invariant failures, with the step that caused them
        self.steps = 0

    # ------------------------------------------------------------ scheduler
    def at(self, t, label, fn, *args):
        heapq.heappush(self._queue, (float(t), self._n, label, fn, args))
        self._n += 1

    def run(self, until=math.inf):
        while self._queue and self._queue[0][0] <= until:
            t, _, label, fn, args = heapq.heappop(self._queue)
            self.now = t
            fn(t, *args)
            self.steps += 1
            bad = self.ex.check_invariants()
            if bad:
                self.violations.append({"t": t, "step": label, "problems": bad})

    # -------------------------------------------------------------- network
    def launch(self, a, b, t_h, loss_coeff=ng.BACKBONE_LOSS):
        r = self.net.launch(a, b, (t_h + EPOCH_OFFSET_H) / 24, loss_coeff)
        r["te_h"], r["ta_h"] = r["te"] * 24 - EPOCH_OFFSET_H, r["ta"] * 24 - EPOCH_OFFSET_H
        return r

    def next_valid(self, a, b, t_h):
        """Earliest emission >= t_h whose flight avoids known Sun and maintenance closures."""
        r = self.launch(a, b, t_h)
        if r["status"] == "open":
            return t_h, r
        lo, hi = t_h, t_h + 0.25
        while self.launch(a, b, hi)["status"] != "open":
            lo, hi = hi, hi + 0.25
            if hi - t_h > 60 * 24:
                raise RuntimeError(f"{a} -> {b} has no valid launch within 60 days of h {t_h}")
        while hi - lo > SEC_H:
            mid = (lo + hi) / 2
            if self.launch(a, b, mid)["status"] == "open":
                hi = mid
            else:
                lo = mid
        return hi, self.launch(a, b, hi)

    def route_timing_h(self, route, t_h):
        """T0: flight + serialization + relay processing along a route, empty queues."""
        return self.net.evaluate_route(route, (t_h + EPOCH_OFFSET_H) / 24)["light_min"] / 60

    def best_route(self, a, b, t_h):
        return self.net.routes(a, b, (t_h + EPOCH_OFFSET_H) / 24)[0]["path"]

    def _loss_reason(self, info):
        for inc in self.incidents:
            if inc["start_h"] <= info["te"] < inc["end_h"] and inc["node"] in (info["from"], info["to"]):
                return f"incident: {inc['kind'].replace('_', ' ')} at {inc['node']}"
        if self.drop:
            return self.drop(info)
        return None

    # ------------------------------------------------------------- sessions
    def session(self, a, b):
        return self.sessions[frozenset((a, b))]

    def route_from(self, sender, recipient):
        s = self.session(sender, recipient)
        return list(s["route"]) if sender == s["initiator"] else list(reversed(s["route"]))

    def open_session(self, t, initiator, peer, route=None):
        """Three-way handshake along a pinned route. Usable once the ACK lands."""
        ex = self.ex
        sa, sb = ex.state.principals[initiator].settlement, ex.state.principals[peer].settlement
        route = route or self.best_route(sa, sb, t)
        sid = ex.open_session(t, initiator, peer, route)
        self.sessions[frozenset((initiator, peer))] = {"id": sid, "initiator": initiator,
                                                       "route": route}
        syn = next(p.id for p in ex.state.packets.values()
                   if p.session_id == sid and p.kind is PacketKind.SYN)
        back = list(reversed(route))

        def ack_arrived(t3):
            ex.set_session_state(t3, sid, SessionState.ESTABLISHED,
                                 note="final ACK received; data may now be released")

        def synack_arrived(t2):
            ack = ex.create_transport_packet(t2, sid, PacketKind.ACK, route[0])
            self.traverse(t2, ack, route, sid, "ACK", ack_arrived)

        def syn_arrived(t1):
            sa_ = ex.create_transport_packet(t1, sid, PacketKind.SYN_ACK, route[-1])
            self.traverse(t1, sa_, back, sid, "SYN-ACK", synack_arrived)

        self.traverse(t, syn, route, sid, "SYN", syn_arrived)
        return sid

    # ------------------------------------------------------------ transport
    def traverse(self, t_ready, packet_id, route, session_id, label, on_arrive, meta=None):
        """Carry one packet hop by hop, with hop receipts and hop retries."""
        st = {"packet": packet_id, "route": route, "session": session_id, "label": label,
              "on_arrive": on_arrive, "meta": meta or {}, "arrived": False,
              "created": t_ready,
              "hops": [{"forwarded": False, "receipt": False, "launches": 0, "abandoned": False}
                       for _ in route[1:]]}
        self._hop_ready(t_ready, st, 0, 1)
        return st

    def _hop_ready(self, t_ready, st, i, attempt):
        a, b = st["route"][i], st["route"][i + 1]
        te, _ = self.next_valid(a, b, t_ready + SEC_H)  # 1 s serialization
        self.at(te, f"{st['label']} launch {a}->{b} #{attempt}", self._emit, st, i, attempt)

    def _emit(self, te, st, i, attempt):
        a, b = st["route"][i], st["route"][i + 1]
        if te > st["created"] + PACKET_LIFETIME_H:
            self.ex.annotate(te, a, f"{st['packet']} expired after 30 days; forwarding stops")
            return
        r = self.launch(a, b, te)
        if r["status"] != "open":  # a known closure appeared: wait for it
            self._hop_ready(te - SEC_H, st, i, attempt)
            return
        info = {"packet": st["packet"], "label": st["label"], "from": a, "to": b, "te": te,
                "attempt": attempt, "hop": i, "meta": st["meta"]}
        reason = self._loss_reason(info)
        ta = r["ta_h"]
        self.ex.record_launch(te, st["packet"], a, b, ta, lost=bool(reason), attempt=attempt,
                              note=f"{st['label']}: LOST ({reason})" if reason else None)
        self.launch_log.append({
            "packet": st["packet"], "label": st["label"], "message": st["meta"].get("message"),
            "kind": st["meta"].get("kind"), "from": a, "to": b, "te": te, "ta": ta,
            "distance_au": r["distance_au"], "light_min": r["light_min"],
            "loss_prob": r["loss"], "lost": bool(reason), "loss_reason": reason,
            "attempt": attempt})
        st["hops"][i]["launches"] = attempt
        if not reason:
            self.at(ta, f"{st['label']} arrives {b}", self._arrive, st, i)
        if st["label"] == "RECEIPT":
            return
        rh = 2 * (ta - te) + 1.0  # R_h = 2 x one-way flight + 60 min
        self.at(te + rh, f"{st['label']} R_h {a}->{b}", self._rh_expired, st, i, attempt)

    def _rh_expired(self, t, st, i, attempt):
        hop = st["hops"][i]
        if hop["receipt"]:
            return
        a, b = st["route"][i], st["route"][i + 1]
        if attempt >= MAX_HOP_LAUNCHES:
            hop["abandoned"] = True
            self.ex.annotate(t, a, f"{st['label']} {st['packet']}: hop {a}->{b} abandoned after "
                                   f"{MAX_HOP_LAUNCHES} launches without a receipt")
            return
        self._hop_ready(t, st, i, attempt + 1)

    def _arrive(self, ta, st, i):
        a, b = st["route"][i], st["route"][i + 1]
        if st["label"] == "RECEIPT":
            st["on_arrive"](ta)
            return
        # The receiver stores the copy and queues a hop receipt on the reverse link at once.
        rid = self.ex.create_transport_packet(ta, st["session"], PacketKind.HOP_RECEIPT, b)
        hop = st["hops"][i]

        def receipt_in(t):
            hop["receipt"] = True
        self.traverse(ta, rid, [b, a], st["session"], "RECEIPT", receipt_in,
                      {"kind": "hop_receipt", "for": st["packet"]})
        if hop["forwarded"]:
            self.ex.annotate(ta, b, f"duplicate copy of {st['packet']}: receipt sent, not forwarded")
            return
        hop["forwarded"] = True
        if i + 2 == len(st["route"]):
            st["arrived"] = True
            st["on_arrive"](ta)
        else:
            self._hop_ready(ta + SEC_H, st, i + 1, 1)  # relay processing 1 s

    # ------------------------------------------------------------- messages
    def send(self, t, sender, recipient, kind, payload, on_deliver, references=(),
             on_ack=None, on_fail=None):
        """Official message: over the backbone, or by local access at one settlement."""
        ex = self.ex
        snd, rcv = ex.state.principals[sender], ex.state.principals[recipient]
        if sender == recipient:
            self.at(t, f"{kind} (same institution)", lambda tt: on_deliver(tt))
            return None
        if snd.settlement == rcv.settlement:
            mid = ex.send_message(t, sender, recipient, "local", "official", kind, payload,
                                  references=references)
            self.at(t + SEC_H, f"{kind} local delivery", lambda tt: on_deliver(tt))
            return mid
        sid = self.session(sender, recipient)["id"]
        mid = ex.send_message(t, sender, recipient, "backbone", "official", kind, payload,
                              session_id=sid, references=references)
        pkts = ex.state.messages[mid].packet_ids
        assert len(pkts) == 1, "evidence scenarios use single-packet messages"
        m = {"id": mid, "kind": kind, "sender": sender, "recipient": recipient,
             "route": self.route_from(sender, recipient), "session": sid,
             "first_packet": pkts[0], "delivered": False, "acked": False, "attempts": 0,
             "on_deliver": on_deliver, "on_ack": on_ack, "on_fail": on_fail,
             "sent_h": t, "delivered_h": None, "acked_h": None}
        self.messages[mid] = m
        self._endpoint_attempt(t, m, pkts[0])
        return mid

    def _endpoint_attempt(self, t, m, packet_id):
        m["attempts"] += 1
        t0 = self.route_timing_h(m["route"], t)
        m.setdefault("timers", []).append({"attempt": m["attempts"], "t": t, "T0_h": t0,
                                           "R_e_h": 2 * t0 + 24})
        self.traverse(t, packet_id, m["route"], m["session"], m["kind"],
                      lambda ta: self._data_arrived(ta, m),
                      {"message": m["id"], "kind": m["kind"], "endpoint_attempt": m["attempts"]})
        self.at(t + 2 * t0 + 24, f"{m['kind']} R_e", self._re_expired, m)

    def _re_expired(self, t, m):
        if m["acked"]:
            return
        if m["attempts"] >= MAX_ENDPOINT_ATTEMPTS:
            if not m["delivered"]:
                self.ex.set_message_status(t, m["id"], MessageStatus.ABANDONED,
                                           note="endpoint attempts exhausted; delivery status unknown")
            if m["on_fail"]:
                m["on_fail"](t)
            return
        pid = self.ex.create_transport_packet(t, m["session"], PacketKind.DATA, m["route"][0],
                                              retry_of=m["first_packet"],
                                              note=f"endpoint retry {m['attempts'] + 1} of {m['id']}")
        self._endpoint_attempt(t, m, pid)

    def _data_arrived(self, ta, m):
        if not m["delivered"]:
            m["delivered"], m["delivered_h"] = True, ta
            self.ex.set_message_status(ta, m["id"], MessageStatus.DELIVERED)
            m["on_deliver"](ta)
        else:
            self.ex.annotate(ta, m["recipient"], f"duplicate data for {m['id']} suppressed "
                                                 "(never delivered twice in one session)")
        ack = self.ex.create_transport_packet(ta, m["session"], PacketKind.DATA_ACK, m["route"][-1])

        def acked(t2):
            if not m["acked"]:
                m["acked"], m["acked_h"] = True, t2
                if m["on_ack"]:
                    m["on_ack"](t2)
        self.traverse(ta, ack, list(reversed(m["route"])), m["session"], "DATA-ACK", acked,
                      {"kind": "data_ack", "message": m["id"]})
