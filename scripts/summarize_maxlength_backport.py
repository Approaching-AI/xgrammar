"""Summarize saved gates without rerunning or modifying the measured inputs."""

import argparse
import collections
import json
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("evidence", type=Path)
args = parser.parse_args()


def load(name):
    return json.loads((args.evidence / name).read_text())


report = {"correctness": {}, "performance": [], "control_overhead": []}
for mode in ("compile", "semantics", "stateful", "large-mask"):
    cases = [
        load(f"{label}-{mode}.json")["cases"] for label in ("official", "baseline", "boundary")
    ]
    if mode == "compile":
        cases = [[{k: v for k, v in row.items() if k != "error"} for row in rows] for rows in cases]
    report["correctness"][mode] = {
        "counts": [len(rows) for rows in cases],
        "equal": cases[0] == cases[1] == cases[2],
        "self_checks_pass": all(c.get("pass", True) for rows in cases for c in rows),
    }


def groups(data):
    result = collections.defaultdict(list)
    for c in data:
        result[c["style"], c["workload"], c["maxLength"]].append(c)
    return result


base_data = load("baseline-performance.json")
patch_data = load("boundary-performance.json")
report["same_token_ids"] = (
    base_data["provenance"]["token_ids_sha256"] == patch_data["provenance"]["token_ids_sha256"]
)
report["provenance"] = {"baseline": base_data["provenance"], "patch": patch_data["provenance"]}
bs, ps = groups(base_data["cases"]), groups(patch_data["cases"])
for key, baseline in bs.items():
    patched = ps[key]
    row = {
        "style": key[0],
        "workload": key[1],
        "maxLength": key[2],
        "runs": [len(baseline), len(patched)],
    }
    for label, runs in (("baseline", baseline), ("patch", patched)):
        row[label] = {
            f"{op}_{p}": statistics.median(c[op][p] for c in runs)
            for op in ("fill", "accept", "rollback", "chain")
            for p in ("p50_ms", "p99_ms")
        }
        row[label]["compile_ms"] = runs[0]["compile_ms"]
        row[label]["warm_compile_ms"] = runs[0]["warm_compile_ms"]
    # Compute the experiment p99 from all directly measured samples; retain
    # per-replay p99s as well (64-sample boundary replays have p99 == maximum).
    row["patch_pooled"] = {}
    row["per_run_p99"] = {op: [c[op]["p99_ms"] for c in patched] for op in ("fill", "chain")}
    for op in ("fill", "chain"):
        samples = sorted(v for c in patched for v in c[f"raw_{op}_ms"])
        row["patch_pooled"][f"{op}_p99_ms"] = samples[
            min(len(samples) - 1, int(len(samples) * 0.99))
        ]
        row["patch_pooled"][f"{op}_n"] = len(samples)
    row["latency_gate"] = key[2] is None or (
        row["patch_pooled"]["fill_p99_ms"] < 10 and row["patch_pooled"]["chain_p99_ms"] < 30
    )
    report["performance"].append(row)

# First-pass tails were noisy. Preserve them above and use the recorded ABBAAB
# follow-up (three processes x five replays per build) to test sustained overhead.
controls = {label: [] for label in ("baseline", "boundary")}
for path in sorted(args.evidence.glob("control-*.json")):
    label = "baseline" if "baseline" in path.name else "boundary"
    controls[label].extend(json.loads(path.read_text())["cases"])
cb, cp = groups(controls["baseline"]), groups(controls["boundary"])
for key, baseline in cb.items():
    patched = cp[key]
    for op in ("fill", "accept", "rollback", "chain"):
        for p in ("p50_ms", "p99_ms"):
            old = statistics.median(c[op][p] for c in baseline)
            new = statistics.median(c[op][p] for c in patched)
            report["control_overhead"].append(
                {
                    "style": key[0],
                    "workload": key[1],
                    "op": op,
                    "quantile": p,
                    "baseline_ms": old,
                    "patch_ms": new,
                    "delta_us": (new - old) * 1000,
                    "relative": new / old - 1,
                    "runs": [len(baseline), len(patched)],
                    "pass": not (new > old * 1.1 and new - old > 0.001),
                }
            )
report["G0_G1_pass"] = all(
    c["equal"] and c["self_checks_pass"] for c in report["correctness"].values()
)
report["G2_pass"] = (
    report["same_token_ids"]
    and all(c["latency_gate"] for c in report["performance"])
    and bool(report["control_overhead"])
    and all(c["pass"] for c in report["control_overhead"])
)
report["G3"] = {"linux_sglang": "NOT_RUN", "production_replay": "MISSING_FULL_GRAMMAR"}
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["G0_G1_pass"] and report["G2_pass"] else 1)
