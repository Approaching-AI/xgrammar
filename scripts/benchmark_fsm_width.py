"""CPU-only, full-tokenizer mask comparison on fixed synthetic prefixes."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics
import time

import torch
from transformers import AutoTokenizer
import xgrammar as xgr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer, trust_remote_code=True, local_files_only=True
    )
    info = xgr.TokenizerInfo.from_huggingface(tokenizer)
    assert info.vocab_size == 163840
    cases = [("plain-json", {"type": "string"}, "json", '"' + "a" * 128, "a")]
    for count in [100, 200, 2000]:
        cases.append(
            (f"min200-{count}", {"type": "string", "minLength": 200}, "qwen_xml", "a" * count, "a")
        )
    for style in ["json", "qwen_xml"]:
        prefix = ('"' if style == "json" else "") + "a" * 510
        cases.append((f"max512-{style}", {"type": "string", "maxLength": 512}, style, prefix, "a"))
    cases.append(
        ("pattern200", {"type": "string", "pattern": "^[a-z]{200,}$"}, "qwen_xml", "a" * 200, "a")
    )
    schema = {"type": "string", "minLength": 200}
    for _ in range(4):
        schema = {
            "type": "object",
            "properties": {"v": schema},
            "required": ["v"],
            "additionalProperties": False,
        }
    cases.append(("nested-min200", schema, "json", '{"v":' * 4 + '"' + "a" * 200, "a"))
    result = {
        "version": importlib.metadata.version("xgrammar"),
        "module": xgr.__file__,
        "serialization": xgr.get_serialization_version(),
        "vocab_size": info.vocab_size,
        "dependencies": {
            p: importlib.metadata.version(p) for p in ["torch", "transformers", "apache-tvm-ffi"]
        },
        "cases": [],
    }
    for name, schema, style, prefix, continuation in cases:
        tag = {
            "type": "structural_tag",
            "format": {
                "type": "tag",
                "begin": "<x>",
                "end": "</x>",
                "content": {"type": "json_schema", "json_schema": schema, "style": style},
            },
        }
        compiler = xgr.GrammarCompiler(info, max_threads=1, cache_enabled=True)
        start = time.perf_counter()
        context = compiler.compile_structural_tag(tag)
        compile_s = time.perf_counter() - start
        matcher = xgr.GrammarMatcher(context, max_rollback_tokens=4)
        tokens = tokenizer.encode("<x>" + prefix, add_special_tokens=False)
        for token in tokens:
            assert matcher.accept_token(token)
        next_tokens = tokenizer.encode(continuation, add_special_tokens=False) * 2
        assert len(next_tokens) == 2
        mask = xgr.allocate_token_bitmask(3, info.vocab_size)
        matcher.fill_next_token_bitmask(mask, 0)
        reference = mask[0].clone()
        fills, chains = [], []
        for i in range(110):
            start = time.perf_counter()
            matcher.fill_next_token_bitmask(mask, 0)
            elapsed = time.perf_counter() - start
            if i >= 10:
                fills.append(elapsed * 1000)
            start = time.perf_counter()
            matcher.fill_next_token_bitmask(mask, 0)
            for row, token in enumerate(next_tokens, 1):
                assert matcher.accept_token(token)
                matcher.fill_next_token_bitmask(mask, row)
            matcher.rollback(2)
            if i >= 10:
                chains.append((time.perf_counter() - start) * 1000)
            matcher.fill_next_token_bitmask(mask, 0)
            assert torch.equal(mask[0], reference)
        entry = {
            "name": name,
            "grammar_sha256": hashlib.sha256(json.dumps(tag, sort_keys=True).encode()).hexdigest(),
            "tokens_sha256": hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),
            "mask_sha256": hashlib.sha256(reference.numpy().tobytes()).hexdigest(),
            "compile_s": compile_s,
            "compiled_estimated_bytes": context.memory_size_bytes,
            "fill_median_ms": statistics.median(fills),
            "fill_max_ms": max(fills),
            "chain3_median_ms": statistics.median(chains),
            "chain3_max_ms": max(chains),
            "fill_samples_ms": fills,
            "chain3_samples_ms": chains,
        }
        result["cases"].append(entry)
        print(name, entry["fill_median_ms"], entry["chain3_median_ms"], flush=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
