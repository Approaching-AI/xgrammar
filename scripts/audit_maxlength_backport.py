"""Check that the backport preserves the 0.2.1 regex/FSM/hash baseline."""

import hashlib
import json
import subprocess
from pathlib import Path

BASE = "5b4e9ce9e72524037ae24ecd831b9b6604d2eb48"


def original(path):
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"])


checks = {}
for name in ("regex_converter.cc", "fsm_builder.cc", "fsm.h", "fsm.cc"):
    path = f"cpp/{name}"
    checks[path] = original(path) == Path(path).read_bytes()

old = original("cpp/grammar_functor.cc").decode()
new = Path("cpp/grammar_functor.cc").read_text()
for begin, end in (
    ("class RuleRefGraphFinder", "class AllowEmptyRuleAnalyzerImpl"),
    ("class GrammarFSMBuilderImpl", "class RepetitionRangeExpanderImpl"),
    ("class GrammarFSMHasherImpl", "class RuleLevelCache::Impl"),
):
    checks[begin] = old[old.index(begin) : old.index(end)] == new[new.index(begin) : new.index(end)]

for path in ("python", "include/xgrammar", ".gitmodules"):
    checks[f"unchanged:{path}"] = not subprocess.check_output(["git", "diff", BASE, "--", path])

for name in ("cpptrace", "dlpack", "googletest"):
    path = f"3rdparty/{name}"
    expected = subprocess.check_output(["git", "ls-tree", BASE, path], text=True).split()[2]
    actual = subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"], text=True).strip()
    checks[path] = expected == actual

old_meta = original("pyproject.toml").decode()
new_meta = Path("pyproject.toml").read_text()
checks["package_metadata_only_version"] = (
    old_meta.replace('version = "0.2.1"', 'version = "0.2.1+maxlength.20260907"') == new_meta
)

print(
    json.dumps(
        {
            "base": BASE,
            "checks": checks,
            "diff_sha256": hashlib.sha256(
                subprocess.check_output(["git", "diff", BASE])
            ).hexdigest(),
            "pass": all(checks.values()),
        },
        indent=2,
    )
)
raise SystemExit(0 if all(checks.values()) else 1)
