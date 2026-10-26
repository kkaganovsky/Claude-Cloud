import numpy as np, pandas as pd, scipy.sparse as sp, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prep"))
import summarize as S

def test_matches_dense_reference():
    rng = np.random.default_rng(0)
    counts = sp.csr_matrix(rng.poisson(0.3, (200, 30)))
    genes = [f"G{i}" for i in range(30)]
    meta = pd.DataFrame({"ct": rng.choice(["a", "b", "c"], 200)})
    expr = S.log_normalize(counts)
    out = S.summarize(expr, genes, meta, ["ct"], "T")
    dense = expr.toarray()
    for lvl in "abc":
        m = (meta.ct == lvl).to_numpy()
        r = out[(out.group_col == "ct") & (out.group == lvl)].set_index("gene")
        assert (r.n_cells == m.sum()).all()
        np.testing.assert_allclose(r.pct_expr, 100 * (dense[m] > 0).mean(0))
        np.testing.assert_allclose(r.mean_expr, dense[m].mean(0))
    a = out[out.group_col == "all"].set_index("gene")
    np.testing.assert_allclose(a.pct_expr, 100 * (dense > 0).mean(0))
