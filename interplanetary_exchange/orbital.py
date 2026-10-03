from __future__ import annotations

import numpy as np


class OrbitalModel:
    """Fixed-Kepler propagation for the nine settlements plus two relays."""

    def __init__(self, orbital_data: dict, network_data: dict):
        self.orbital_data = orbital_data
        self.network_data = network_data
        self.bodies = {b["name"]: b for b in orbital_data["bodies"]}
        self.relays = {r["name"]: r for r in network_data["relays"]}

    @staticmethod
    def solve_kepler(M: float, e: float, tol: float = 1e-13, max_iter: int = 100) -> float:
        E = M
        for _ in range(max_iter):
            f = E - e * np.sin(E) - M
            fp = 1.0 - e * np.cos(E)
            E_new = E - f / fp
            if abs(E_new - E) < tol:
                return float(E_new)
            E = E_new
        raise RuntimeError("Kepler solver did not converge")

    @staticmethod
    def _rz(theta: float) -> np.ndarray:
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    @staticmethod
    def _rx(theta: float) -> np.ndarray:
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])

    def planetary_position(self, body_name: str, t_days: float) -> np.ndarray:
        b = self.bodies[body_name]
        a, e = float(b["a_au"]), float(b["e"])
        i = np.deg2rad(b["i_deg"])
        Omega = np.deg2rad(b["node_deg"])
        omega = np.deg2rad(b["arg_peri_deg"])
        M_deg = (b["mean_anomaly_deg"] + b["mean_motion_deg_day"] * t_days) % 360.0
        M = np.deg2rad(M_deg)
        E = self.solve_kepler(M, e)
        xp = a * (np.cos(E) - e)
        yp = a * np.sqrt(1.0 - e * e) * np.sin(E)
        r_orbit = np.array([xp, yp, 0.0])
        return self._rz(Omega) @ self._rx(i) @ self._rz(omega) @ r_orbit

    def relay_position(self, relay_name: str, t_days: float) -> np.ndarray:
        r = self.relays[relay_name]
        theta = np.deg2rad((r["mean_anomaly_deg"] + r["mean_motion_deg_day"] * t_days) % 360.0)
        a = float(r["a_au"])
        return np.array([a * np.cos(theta), a * np.sin(theta), 0.0])

    def position(self, node: str, t_days: float) -> np.ndarray:
        if node in self.bodies:
            return self.planetary_position(node, t_days)
        if node in self.relays:
            return self.relay_position(node, t_days)
        raise KeyError(f"Unknown node {node!r}")

    def validate_epoch(self) -> dict:
        expected = {
            "Mercury": (-0.213426, -0.410281, -0.013955),
            "Venus": (0.679067, -0.257950, -0.042725),
            "Earth": (1.003581, -0.023693, -0.000003),
            "Mars": (0.247476, 1.526869, 0.025929),
            "Ceres": (0.375786, 2.653941, 0.014791),
            "Jupiter": (-3.438179, 4.038309, 0.060149),
            "Saturn": (9.271221, 1.717485, -0.398962),
            "Uranus": (8.962475, 17.253919, -0.052129),
            "Neptune": (29.839098, 1.351785, -0.715470),
            "Relay A": (2.0, 2.0, 0.0),
            "Relay B": (-2.0, 2.0, 0.0),
        }
        out = {}
        for name, exp in expected.items():
            got = self.position(name, 0.0)
            err = float(np.linalg.norm(got - np.array(exp)))
            out[name] = {"position": got, "error_au": err, "passes_1e-5": err <= 1e-5}
        return out
