"""CPU-only full-vocabulary checks for externally supplied structural-tag prefixes.

Run baseline and candidate in separate processes/environments, then compare their
grammar/token/mask hashes. No captured production schema is bundled with this tool.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import time

import torch
from transformers import AutoTokenizer

import xgrammar as xgr


def digest(value):
    return hashlib.sha256(value).hexdigest()


def distribution(values):
    ordered = sorted(values)
    return {
        "median_ms": statistics.median(ordered),
        "p99_ms": ordered[min(len(ordered) - 1, int(0.99 * len(ordered)))],
        "max_ms": ordered[-1],
        "samples_ms": values,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--grammar", required=True)
    parser.add_argument("--cases", required=True, help="JSON list of {name, prefix} objects")
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer, trust_remote_code=True, local_files_only=True
    )
    info = xgr.TokenizerInfo.from_huggingface(tokenizer)
    grammar_bytes = Path(args.grammar).read_bytes()
    tag = json.loads(grammar_bytes)
    compiler = xgr.GrammarCompiler(info, max_threads=1, cache_enabled=True)
    start = time.perf_counter()
    compiled = compiler.compile_structural_tag(tag)
    cold_compile = time.perf_counter() - start
    start = time.perf_counter()
    compiler.compile_structural_tag(tag)
    hot_compile = time.perf_counter() - start
    result = {
        "version": importlib.metadata.version("xgrammar"),
        "module": xgr.__file__,
        "serialization_version": xgr.get_serialization_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "vocab_size": info.vocab_size,
        "grammar_sha256": digest(grammar_bytes),
        "compile_cold_s": cold_compile,
        "compile_hot_s": hot_compile,
        "cases": [],
    }
    continuation = tokenizer.encode("a", add_special_tokens=False)
    assert len(continuation) == 1
    next_tokens = continuation * 2
    for case in json.loads(Path(args.cases).read_text()):
        tokens = tokenizer.encode(case["prefix"], add_special_tokens=False)
        matcher = xgr.GrammarMatcher(compiled, max_rollback_tokens=32)
        for token in tokens:
            assert matcher.accept_token(token), f"Invalid prefix in {case['name']}"
        mask = xgr.allocate_token_bitmask(3, info.vocab_size)
        matcher.fill_next_token_bitmask(mask, 0)
        expected = mask[0].clone()
        fills, chains, cpu_fills = [], [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            cpu = time.process_time()
            matcher.fill_next_token_bitmask(mask, 0)
            fills.append((time.perf_counter() - start) * 1000)
            cpu_fills.append((time.process_time() - cpu) * 1000)
            assert torch.equal(mask[0], expected)
            start = time.perf_counter()
            accepted = 0
            try:
                matcher.fill_next_token_bitmask(mask, 0)
                for row, token in enumerate(next_tokens, 1):
                    assert matcher.accept_token(token)
                    accepted += 1
                    matcher.fill_next_token_bitmask(mask, row)
            finally:
                if accepted:
                    matcher.rollback(accepted)
            chains.append((time.perf_counter() - start) * 1000)
            matcher.fill_next_token_bitmask(mask, 0)
            assert torch.equal(mask[0], expected), "Rollback changed the full vocabulary mask"
        entry = {
            "name": case["name"],
            "prefix_token_count": len(tokens),
            "prefix_token_sha256": digest(json.dumps(tokens).encode()),
            "mask_sha256": digest(expected.numpy().tobytes()),
            "fill": distribution(fills),
            "fill_cpu": distribution(cpu_fills),
            "chain3": distribution(chains),
        }
        result["cases"].append(entry)
        print(json.dumps({"case": case["name"], "fill_p99_ms": entry["fill"]["p99_ms"],
                          "chain3_p99_ms": entry["chain3"]["p99_ms"]}), flush=True)
    Path(args.output).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
