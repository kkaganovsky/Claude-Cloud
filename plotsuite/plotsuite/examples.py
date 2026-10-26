"""Example datasets / project so a new user can try every layout in seconds."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import Dataset
from .project import AnalysisSpec, GraphSpec, Project


def example_project() -> Project:
    rng = np.random.default_rng(7)
    p = Project("Example project")
    p.datasets["Dose groups"] = Dataset("Dose groups", "column", pd.DataFrame(
        {"Vehicle": rng.normal(10, 1.8, 10), "Low dose": rng.normal(12.5, 2.0, 10), "High dose": rng.normal(16, 2.4, 10)}))
    p.datasets["Before-after"] = Dataset("Before-after", "column", pd.DataFrame({"Before": (b := rng.normal(50, 8, 12)), "After": b + rng.normal(6, 4, 12)}), paired=True)
    rows = [(g, t, f"{g}{s}", rng.normal(10 + (g == "KO") * 2 + i * 1.5, 1.4)) for g in ["WT", "KO"] for i, t in enumerate(["Day 0", "Day 7", "Day 14"]) for s in range(6)]
    p.datasets["Genotype x time"] = Dataset("Genotype x time", "grouped", pd.DataFrame(rows, columns=["Genotype", "Time", "Mouse", "Weight"]),
                                            {"value": "Weight", "a": "Genotype", "b": "Time"})
    x = np.tile([0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30], 3)
    y = 4 + 96 / (1 + (1.0 / x) ** 1.1) + rng.normal(0, 3, len(x))
    p.datasets["Dose-response"] = Dataset("Dose-response", "xy", pd.DataFrame({"Conc (uM)": x, "Response": y}), {"x": "Conc (uM)", "ys": ["Response"]})
    n = 30
    sv = pd.DataFrame({"Days": np.r_[rng.exponential(25, n), rng.exponential(45, n)].round(1), "Died": rng.binomial(1, .8, 2 * n), "Arm": np.repeat(["Placebo", "Drug"], n)})
    p.datasets["Survival"] = Dataset("Survival", "survival", sv, {"time": "Days", "event": "Died", "group": "Arm"})
    p.datasets["Responders"] = Dataset("Responders", "contingency", pd.DataFrame({"Arm": ["Drug", "Placebo"], "Responded": [18, 7], "No response": [7, 18]}))
    a1 = AnalysisSpec("ANOVA + Tukey", "Dose groups", "oneway_anova", {"posthoc": "tukey"})
    a2 = AnalysisSpec("Two-way ANOVA", "Genotype x time", "two_way_anova", {"posthoc": "A within each B", "adjust": "sidak"})
    a3 = AnalysisSpec("4PL fit", "Dose-response", "nonlinear_fit", {"model": "[Agonist] vs response (variable slope)"})
    a4 = AnalysisSpec("Log-rank", "Survival", "survival_km", {})
    p.analyses = [a1, a2, a3, a4]
    p.graphs = [
        GraphSpec("Dose groups", "Dose groups", "column", {}, {}, [a1.id]),
        GraphSpec("Before-after", "Before-after", "column", {}, {"column_mode": "points only", "error": "none"}),
        GraphSpec("Genotype x time", "Genotype x time", "grouped", {"x_factor": "b"}, {}, [a2.id]),
        GraphSpec("Dose-response", "Dose-response", "xy", {}, {"x_scale": "log"}, [a3.id]),
        GraphSpec("Survival", "Survival", "survival", {}, {"show_ci_band": False, "legend_loc": "upper right"}, [a4.id]),
    ]
    return p
