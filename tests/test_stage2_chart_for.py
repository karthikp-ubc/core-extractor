import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from stage2_metrics import filter_to_chart_for  # noqa: E402

BASE = "https://portal.core.edu.au/core/media/2025/centiles_graphs/"


def link(cid, kind, code, rnd="ICORE2026"):
    return {"core_id": cid, "round": rnd, "link_type": kind,
            "url": f"{BASE}X_{cid}_{kind}_FOR{code}_centileGraph.png"}


def test_filter_keeps_mapped_for_and_falls_back(tmp_path):
    links = pd.DataFrame([
        link(147, "h_index", "4606"), link(147, "h_index", "4612"),   # mapped 4606
        link(1629, "h_index", "4602"), link(1629, "h_index", "4611"), # mapped 4602
        link(1996, "h_index", "CSE"),                                 # mapped CSE
        link(5, "h_index", "4604"),                                   # mapped 4606, absent -> keep
        link(9, "h_index", "4604"), link(9, "h_index", "4605"),       # unmapped -> keep all
    ])
    m = tmp_path / "chart_for_code.csv"
    m.write_text("core_id,for_code,source\n147,4606,x\n1629,4602,x\n1996,CSE,x\n5,4606,x\n")
    out = filter_to_chart_for(links, m)
    got = sorted(zip(out["core_id"], out["url"].str.extract(r"FOR(\w+?)_centile")[0]))
    assert got == [(5, "4604"), (9, "4604"), (9, "4605"), (147, "4606"),
                   (1629, "4602"), (1996, "CSE")]


def test_reuse_existing_mixed_with_fresh_rows_writes(tmp_path, monkeypatch):
    """Regression: reused rows + freshly extracted rows in one run crashed
    to_parquet() on a mixed str/int core_id column, losing the whole run."""
    import stage2_metrics as s2
    rnd = "ICORE2026"
    (tmp_path / "images" / rnd).mkdir(parents=True)
    for name in ["A_1_h_index_FOR4606_centileGraph.png",
                 "B_2_h_index_FOR4606_centileGraph.png"]:
        (tmp_path / "images" / rnd / name).write_bytes(b"\x89PNG")
    pd.DataFrame([
        {"core_id": 1, "round": rnd, "link_type": "h_index", "doc_index": None,
         "url": BASE + "A_1_h_index_FOR4606_centileGraph.png"},
        {"core_id": 2, "round": rnd, "link_type": "h_index", "doc_index": None,
         "url": BASE + "B_2_h_index_FOR4606_centileGraph.png"},
    ]).to_csv(tmp_path / "links.csv", index=False)
    pd.DataFrame([{"core_id": 1, "round": rnd, "metric_name": "m", "value": 5,
                   "window": None, "retrieved_at": "t",
                   "raw_payload_ref": f"images/{rnd}/A_1_h_index_FOR4606_centileGraph.png"}]
                 ).to_parquet(tmp_path / "icore_metrics.parquet", index=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"in_scope_rounds: [{rnd}]\npaths:\n  cache_dir: {tmp_path}/cache\n")

    calls = []

    def fake(row, image_path, cache_dir, model):
        calls.append(image_path.name)
        return ([{"core_id": row["core_id"], "round": row["round"],
                  "metric_name": "m", "value": 7, "window": None,
                  "retrieved_at": "t", "raw_payload_ref": "x"}], [], None)
    monkeypatch.setattr(s2, "process_row", fake)
    assert s2.main(["--links-csv", str(tmp_path / "links.csv"),
                    "--images-dir", str(tmp_path / "images"),
                    "--out-dir", str(tmp_path), "--config", str(cfg),
                    "--reuse-existing"]) == 0
    assert calls == ["B_2_h_index_FOR4606_centileGraph.png"]
    out = pd.read_parquet(tmp_path / "icore_metrics.parquet")
    assert sorted(zip(out["core_id"], out["value"])) == [(1, 5), (2, 7)]
