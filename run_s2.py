from pathlib import Path
import json

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel, Incident
from interplanetary_exchange.routing import Router
from interplanetary_exchange.simulator import FederatedExchangeSimulator
from interplanetary_exchange.stress import S2StressSearcher
from interplanetary_exchange.scenarios import run_future_stress_scenario

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
searcher = S2StressSearcher(make_sim, exchanges, core)
stress_df, worst = searcher.search_refined(coarse_start_hours=range(24, 241, 6))
stress_df.to_csv(OUT / "S2_incident_search.csv", index=False)
(OUT / "S2_worst_incident.json").write_text(json.dumps(worst, indent=2, default=str))
if worst:
    incident = Incident(**worst["incident"])
    sim = make_sim()
    detail = run_future_stress_scenario(sim, incident)
    sim.export(OUT / "S2_worst_trace")
    (OUT / "S2_worst_trace" / "result.json").write_text(json.dumps(detail, indent=2, default=str))
print(f"S2 search rows: {len(stress_df)}")
print(json.dumps({"incident": worst.get("incident"), "stress_score": worst.get("stress_score")}, indent=2))
