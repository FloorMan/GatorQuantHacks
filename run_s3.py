from pathlib import Path
import json

from interplanetary_exchange.config import default_config
from interplanetary_exchange.data import load_project_data
from interplanetary_exchange.orbital import OrbitalModel
from interplanetary_exchange.network import NetworkModel
from interplanetary_exchange.routing import Router
from interplanetary_exchange.evidence import EvidenceBuilder

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "evidence"
OUT.mkdir(parents=True, exist_ok=True)
config = default_config()
orbital_data, network_data = load_project_data(ROOT / "Alpha_Orbital_Data.zip")
orbital = OrbitalModel(orbital_data, network_data)
network = NetworkModel(orbital, network_data, config)
router = Router(network, config)
baseline = json.loads((ROOT / "output" / "baseline_topology.json").read_text())
evidence = EvidenceBuilder(network, router, config, baseline["core"], baseline["assignment"])
evidence.access_table(0.0).to_csv(OUT / "S3_access_hour_0.csv", index=False)
evidence.access_table(300.0).to_csv(OUT / "S3_access_hour_300.csv", index=False)
print(f"S3 tables written to {OUT}")
