from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple


def load_project_data(zip_path: str | Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "r") as z:
        orbital = json.loads(z.read("orbital_elements.json"))
        network = json.loads(z.read("network_model.json"))
    return orbital, network
