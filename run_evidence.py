from pathlib import Path
import json

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel
from interplanetary_exchange.routing import Router
from interplanetary_exchange.evidence import EvidenceBuilder
from interplanetary_exchange.simulator import FederatedExchangeSimulator
from interplanetary_exchange.stress import S2StressSearcher

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

print("Generating S3 + full E4 + E5 evidence...")
evidence = EvidenceBuilder(network, router, config, core, assignment, simulator_factory=make_sim)
summary = evidence.export_all(OUT)
print(json.dumps(summary, indent=2, default=str))

print("Searching S2 incidents...")
searcher = S2StressSearcher(make_sim, exchanges, core)
# Six-hour S2 grid from hour 24 through maturity at hour 240.
stress_df, worst = searcher.search_refined(coarse_start_hours=range(24, 241, 6))
stress_df.to_csv(OUT / "S2_incident_search.csv", index=False)
(OUT / "S2_worst_incident.json").write_text(json.dumps(worst, indent=2, default=str))

# Re-run the worst candidate and export its exact packet/timer/queue/quota trace.
from interplanetary_exchange.network import Incident
from interplanetary_exchange.scenarios import run_future_stress_scenario
if worst:
    inc = Incident(**worst["incident"])
    sim = make_sim()
    detail = run_future_stress_scenario(sim, inc)
    sim.export(OUT / "S2_worst_trace")
    (OUT / "S2_worst_trace" / "result.json").write_text(json.dumps(detail, indent=2, default=str))

print(f"Evidence written to {OUT}")
