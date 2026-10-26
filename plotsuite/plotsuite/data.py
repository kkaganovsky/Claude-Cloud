"""Data tables. Five layouts mirror Prism's table types.

column       each column is a group (rows may be matched subjects when ``paired``)
grouped      long format; roles: value, a (row factor), b (column factor), c (optional
             third factor), subject (optional, for repeated measures)
xy           roles: x, ys (list of Y columns)
contingency  first column = row labels, remaining columns = integer counts
survival     roles: time, event (1 = event, 0 = censored), group (optional)
"""
from __future__ import annotations

import dataclasses as dc
import io
from typing import Any

import numpy as np
import pandas as pd

KINDS = ("column", "grouped", "xy", "contingency", "survival")
KIND_LABELS = {
    "column": "Column (one column per group)",
    "grouped": "Grouped / long (factors + value)",
    "xy": "XY (x column + y series)",
    "contingency": "Contingency (counts)",
    "survival": "Survival (time + event)",
}


@dc.dataclass
class Dataset:
    name: str
    kind: str
    df: pd.DataFrame
    roles: dict = dc.field(default_factory=dict)
    paired: bool = False  # column kind: each row is one matched subject

    # ---- generic ------------------------------------------------------------
    def copy(self) -> "Dataset":
        return Dataset(self.name, self.kind, self.df.copy(), dict(self.roles), self.paired)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "roles": self.roles, "paired": self.paired,
            "columns": [str(c) for c in self.df.columns],
            "rows": json_safe(self.df.astype(object).where(self.df.notna(), None).values.tolist()),
        }

    @staticmethod
    def from_dict(d: dict) -> "Dataset":
        df = pd.DataFrame(d["rows"], columns=d["columns"])
        for c in df.columns:
            if df[c].dtype == object and _all_num(df[c]):
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return Dataset(d["name"], d["kind"], df, d.get("roles", {}), d.get("paired", False))

    # ---- problems that must be fixed before any statistics -------------------
    def bad_cells(self, columns=None) -> list[tuple[str, int, Any]]:
        """Non-empty cells that are not numbers (never silently coerced)."""
        out = []
        for c in (columns if columns is not None else self.df.columns):
            s = self.df[c]
            if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                continue
            num = pd.to_numeric(s, errors="coerce")
            for i in np.where(num.isna() & s.notna() & (s.astype(str).str.strip() != ""))[0]:
                out.append((str(c), int(i) + 1, s.iloc[i]))
        return out

    def numeric(self, columns=None) -> pd.DataFrame:
        cols = list(columns if columns is not None else self.df.columns)
        out = self.df[cols].apply(pd.to_numeric, errors="coerce").astype(float)
        return out.replace([np.inf, -np.inf], np.nan)

    # ---- column layout -------------------------------------------------------
    def group_arrays(self) -> dict[str, np.ndarray]:
        m = self.numeric()
        return {str(c): m[c].dropna().to_numpy() for c in m.columns if m[c].notna().any()}

    def group_names(self) -> list[str]:
        return list(self.group_arrays())

    # ---- long layout ---------------------------------------------------------
    def long(self) -> pd.DataFrame:
        """Tidy frame with columns value, a, [b, c, subject] regardless of layout."""
        if self.kind == "column":
            m = self.numeric()
            m = m.loc[:, m.notna().any()]
            t = m.copy()
            t["subject"] = [f"s{i + 1}" for i in range(len(t))]
            t = t.melt(id_vars="subject", var_name="a", value_name="value").dropna(subset=["value"])
            t["a"] = t["a"].astype(str)
            return t.reset_index(drop=True)
        if self.kind == "grouped":
            r = self.roles
            cols = {"value": r.get("value"), "a": r.get("a"), "b": r.get("b"), "c": r.get("c"),
                    "subject": r.get("subject")}
            t = pd.DataFrame({k: self.df[v] for k, v in cols.items() if v})
            t["value"] = pd.to_numeric(t["value"], errors="coerce")
            for k in ("a", "b", "c", "subject"):
                if k in t:
                    t[k] = t[k].astype(str)
            return t.dropna(subset=["value"]).reset_index(drop=True)
        raise ValueError(f"long() not defined for {self.kind}")

    # ---- xy ------------------------------------------------------------------
    def xy_series(self) -> dict[str, pd.DataFrame]:
        x = self.roles.get("x")
        ys = self.roles.get("ys") or [c for c in self.df.columns if c != x]
        out = {}
        for y in ys:
            t = pd.DataFrame({"x": pd.to_numeric(self.df[x], errors="coerce"),
                              "y": pd.to_numeric(self.df[y], errors="coerce")}).dropna()
            if len(t):
                out[str(y)] = t.reset_index(drop=True)
        return out

    # ---- contingency ---------------------------------------------------------
    def counts(self) -> pd.DataFrame:
        first = self.df.columns[0]
        body = self.df.drop(columns=[first]).apply(pd.to_numeric, errors="coerce")
        body.index = self.df[first].astype(str)
        return body

    # ---- survival ------------------------------------------------------------
    def survival(self) -> pd.DataFrame:
        r = self.roles
        t = pd.DataFrame({"time": pd.to_numeric(self.df[r["time"]], errors="coerce"),
                          "event": pd.to_numeric(self.df[r["event"]], errors="coerce")})
        t["group"] = self.df[r["group"]].astype(str) if r.get("group") else "all"
        return t.dropna(subset=["time", "event"]).reset_index(drop=True)


def _all_num(s: pd.Series) -> bool:
    return bool(pd.to_numeric(s.dropna(), errors="coerce").notna().all()) and s.notna().any()


def json_safe(o):
    if isinstance(o, list):
        return [json_safe(x) for x in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    return o


# ---- import helpers -----------------------------------------------------------
def read_table(text_or_file, filename: str = "", sheet=0) -> pd.DataFrame:
    """CSV / TSV / XLSX / pasted text -> DataFrame. Numeric columns are converted only when
    every non-empty cell parses; otherwise the column is kept as text so the problem is
    reported to the user instead of being silently dropped."""
    if isinstance(text_or_file, str):
        buf = io.StringIO(text_or_file)
        sep = "\t" if "\t" in text_or_file.split("\n")[0] else (";" if text_or_file.count(";") > text_or_file.count(",") else ",")
        df = pd.read_csv(buf, sep=sep, dtype=str, skipinitialspace=True)
    elif filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(text_or_file, sheet_name=sheet, dtype=str)
    else:
        raw = text_or_file.read() if hasattr(text_or_file, "read") else open(text_or_file, "rb").read()
        text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
        return read_table(text)
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().sum() == df[c].notna().sum():
            df[c] = conv
    return df.reset_index(drop=True)
