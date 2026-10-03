from pathlib import Path
import json

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel
from interplanetary_exchange.routing import Router
from interplanetary_exchange.evidence import EvidenceBuilder
from interplanetary_exchange.simulator import FederatedExchangeSimulator

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "evidence"
OUT.mkdir(parents=True, exist_ok=True)
config = default_config()
orbital_data, network_data = load_project_data(ROOT / "Alpha_Orbital_Data.zip")
orbital = OrbitalModel(orbital_data, network_data)
network = NetworkModel(orbital, network_data, config)
router = Router(network, config)
baseline = json.loads((ROOT / "output" / "baseline_topology.json").read_text())
core = baseline["core"]
assignment = baseline["assignment"]
exchanges = tuple(baseline["exchanges"])
def make_sim():
    return FederatedExchangeSimulator(config, network, router, core, assignment, exchanges)
evidence = EvidenceBuilder(network, router, config, core, assignment, simulator_factory=make_sim)
e4_summary = json.loads((OUT / "E4_summary.json").read_text())
difficult = float(e4_summary["difficult_epoch"]["time_days"])
e5 = evidence.full_e5(difficult)
e5["table"].to_csv(OUT / "E5_shifted_epochs.csv", index=False)
(OUT / "E5_scenarios.json").write_text(json.dumps(e5["scenarios"], indent=2, default=str))
print(f"E5 Tier {e5['tier_claimed']} evidence written to {OUT}")
