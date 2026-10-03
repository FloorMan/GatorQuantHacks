from pathlib import Path

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel

ROOT = Path(__file__).resolve().parents[1]
orbital_data, network_data = load_project_data(ROOT / "Alpha_Orbital_Data.zip")
orbital = OrbitalModel(orbital_data, network_data)
network = NetworkModel(orbital, network_data, default_config())


def test_epoch_positions():
    checks = orbital.validate_epoch()
    assert all(v["passes_1e-5"] for v in checks.values())


def test_earth_mars_light_time_positive():
    x = network.moving_receiver_light_time("Earth", "Mars", 0.0)
    assert x["distance_au"] > 0
    assert x["arrival_time_days"] > 0
    assert x["error_ms"] < 1.0


def test_loss_models():
    assert 0 < network.backbone_loss_probability(1.0) < network.direct_loss_probability(1.0) < 1

from interplanetary_exchange.encoding import FixedWidthApplicationEncoding, FinancialRecord
from interplanetary_exchange.quota import RollingQuotaTracker
from interplanetary_exchange.routing import Router


def test_fixed_width_application_encoding():
    payload = FixedWidthApplicationEncoding.encode_payload([
        FinancialRecord(record_type="EQ", object_id="T1", account_a="A", account_b="B", symbol="ARES")
    ])
    assert len(payload) == 960
    assert FixedWidthApplicationEncoding.RECORDS_PER_PACKET == 9
    assert FixedWidthApplicationEncoding.packet_count(10) == 2


def test_rolling_direct_quota_and_spacing():
    c = default_config()
    q = RollingQuotaTracker(c)
    q.record("direct", "P", 0.0, "P1", "ORDER")
    ok, reason = q.can_originate("direct", "P", 30.0 / 86400.0)
    assert not ok and reason == "DIRECT_60S_SPACING"
    ok, _ = q.can_originate("direct", "P", 60.0 / 86400.0)
    assert ok


def test_router_service_path():
    r = Router(network, default_config())
    x = r.service_path("Earth", "Earth", "Mars", 0.0)
    assert "available" in x

from interplanetary_exchange.workload import WorkloadPlanner


def test_quota_dataframe_is_packet_sequential():
    c = default_config()
    q = RollingQuotaTracker(c)
    q.record("backbone", "A", 0.0, "P1", "X")
    q.record("backbone", "B", 0.0, "P2", "X")
    df = q.dataframe().sort_values(["time_days", "packet_id"])
    assert list(df["rolling_24h_count_after"]) == [1, 2]
    assert list(df["rolling_24h_remaining_after"]) == [599, 598]


def test_workload_uses_actual_fixed_width_packets():
    c = default_config()
    r = Router(network, c)
    planner = WorkloadPlanner(c, r)
    exchanges = ("Earth", "Mars", "Ceres")
    assignment = {s: (s if s in exchanges else "Earth") for s in c.demand_per_day}
    q, df, summary = planner.build(assignment, "Ceres", exchanges, horizon_days=1)
    app = df[df["network_packet_bytes"].notna()]
    assert not app.empty
    assert (app["application_payload_bytes"] == 960).all()
    assert (app["network_packet_bytes"] == 1024).all()
    assert (app["record_count"] <= FixedWidthApplicationEncoding.RECORDS_PER_PACKET).all()
    assert app["payload_sha256"].str.len().eq(64).all()
