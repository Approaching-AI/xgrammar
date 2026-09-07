"""CPU-only, cache-enabled backport gates. Run identically against each build.

Use --package-root and DYLD_LIBRARY_PATH to select an unpacked wheel. Output is
JSON; no source grammar, model or server configuration is modified by this probe.
"""

import argparse
import base64
import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--package-root", required=True)
parser.add_argument(
    "--mode",
    choices=["compile", "semantics", "stateful", "large-mask", "performance"],
    default="compile",
)
parser.add_argument("--rounds", type=int, default=5)
parser.add_argument("--controls-only", action="store_true")
args = parser.parse_args()
sys.meta_path = [
    f for f in sys.meta_path if not type(f).__module__.startswith("_editable_skbc_xgrammar")
]
sys.path.insert(0, str(Path(args.package_root).resolve()))

import torch
import xgrammar as xgr


def compile_tag(compiler, schema, style):
    return compiler.compile_structural_tag(
        {
            "type": "structural_tag",
            "format": {
                "type": "tag",
                "begin": "<arg>",
                "end": "</arg>",
                "content": {"type": "json_schema", "json_schema": schema, "style": style},
            },
        }
    )


def summary(values):
    values = sorted(values)
    return {
        "n": len(values),
        "total_ms": sum(values),
        **{
            f"p{p}_ms": values[min(len(values) - 1, int(len(values) * p / 100))]
            for p in (50, 95, 99)
        },
        "max_ms": values[-1],
    }


result = {
    "provenance": {
        "python": sys.version,
        "platform": platform.platform(),
        "xgrammar_file": xgr.__file__,
        "library_path": os.environ.get("DYLD_LIBRARY_PATH"),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("torch", "apache-tvm-ffi", "transformers")
        },
        "cache_enabled": True,
        "max_threads": 1,
    },
    "cases": [],
}
native_lib = Path(os.environ.get("DYLD_LIBRARY_PATH", "")) / "libxgrammar_bindings.dylib"
if native_lib.is_file():
    result["provenance"]["library_sha256"] = hashlib.file_digest(
        native_lib.open("rb"), "sha256"
    ).hexdigest()

if args.mode == "compile":
    ti = xgr.TokenizerInfo(["a", "b", "0", "1", '"', "<arg>", "</arg>", "{", "}", ":"])
    for style in ("json", "qwen_xml"):
        for pattern in (
            "^a{128}$",
            "^a{129}$",
            "^a{129,200}$",
            "^(ab){129,200}$",
            "^(?=a)a$",
            "^(?!a)b$",
        ):
            for nested in (False, True):
                schema = {"type": "string", "pattern": pattern}
                if nested:
                    schema = {
                        "type": "object",
                        "properties": {"value": schema},
                        "required": ["value"],
                    }
                case = {"style": style, "pattern": pattern, "nested": nested}
                try:
                    compile_tag(xgr.GrammarCompiler(ti, max_threads=1), schema, style)
                    case["compiled"] = True
                except RuntimeError as e:
                    case.update(compiled=False, error=str(e))
                case["pass"] = case["compiled"] == ("(?" not in pattern)
                result["cases"].append(case)

elif args.mode == "semantics":
    vocab = [
        "<arg>",
        "</arg>",
        "a",
        "b",
        "ab",
        "你好",
        "😀",
        '"',
        '"</arg>',
        "</arg><arg>",
        "{",
        "}",
        "[",
        "]",
        ":",
        ",",
        "null",
        "value",
        "a</arg>",
        "ab</arg>",
        "a" * 129,
        b"\xe4",
        b"\xbd\xa0",
        "\\n",
        "\n",
        '\\"',
        "<stop>",
    ]
    ti = xgr.TokenizerInfo(vocab, stop_token_ids=[len(vocab) - 1])
    compiler = xgr.GrammarCompiler(ti, max_threads=1)
    for style in ("json", "qwen_xml"):
        for upper in (0, 1, 2, 127, 128, 129, 200, 512, 4096, 100000, 524288):
            for lower in sorted({0, min(1, upper), upper}):
                schema = {"type": "string", "minLength": lower, "maxLength": upper}
                compiled = compile_tag(compiler, schema, style)
                for size in sorted(
                    {0, max(0, lower - 1), lower, max(0, upper - 1), upper, upper + 1}
                ):
                    prefix = "<arg>" + ('"' if style == "json" else "") + "a" * size
                    matcher = xgr.GrammarMatcher(compiled)
                    accepted = matcher.accept_string(prefix)
                    case = {
                        "style": style,
                        "min": lower,
                        "max": upper,
                        "size": size,
                        "prefix_accepted": accepted,
                    }
                    if accepted:
                        mask = xgr.allocate_token_bitmask(1, len(vocab))
                        matcher.fill_next_token_bitmask(mask)
                        bits = [
                            bool((int(mask[0, i // 32]) >> (i % 32)) & 1) for i in range(len(vocab))
                        ]
                        truth = [matcher.fork().accept_token(i) for i in range(len(vocab))]
                        repeat_mask = mask.clone()
                        matcher.fill_next_token_bitmask(repeat_mask)
                        case.update(
                            bits=bits,
                            truth=truth,
                            repeated_fill_equal=torch.equal(mask, repeat_mask),
                        )
                        case["pass"] = bits == truth and case["repeated_fill_equal"]
                    else:
                        case["pass"] = size > upper
                    result["cases"].append(case)

elif args.mode == "stateful":
    vocab = [
        "a",
        "b",
        "ab",
        "你好",
        "😀",
        '"',
        '"</arg>',
        "</arg><arg>",
        "{",
        "}",
        "[",
        "]",
        ":",
        ",",
        "null",
        "value",
        "a</arg>",
        "ab</arg>",
        b"\xe4",
        b"\xbd\xa0",
        "\\n",
        "\n",
        '\\"',
        "<stop>",
    ]
    ti = xgr.TokenizerInfo(vocab, stop_token_ids=[len(vocab) - 1])
    compiler = xgr.GrammarCompiler(ti, max_threads=1)
    scalar = {"type": "string", "minLength": 1, "maxLength": 2}
    cases = [
        (style, scalar, "<arg>" + ('"' if style == "json" else "") + text)
        for style in ("json", "qwen_xml")
        for text in ("", "a", "ab", "你", "你好", "😀", "😀a", "\\n")
    ]
    cases += [
        ("json", {"type": "array", "items": scalar}, '<arg>["a","你'),
        (
            "json",
            {"type": "object", "properties": {"value": scalar}, "additionalProperties": scalar},
            '<arg>{"value":"a","other":"',
        ),
        ("json", {"type": "object", "properties": {"value": scalar}}, '<arg>{"value":"你'),
    ]
    for index, (style, schema, prefix) in enumerate(cases):
        compiled = compile_tag(compiler, schema, style)
        matcher = xgr.GrammarMatcher(compiled)
        accepted = matcher.accept_string(prefix)
        case = {"index": index, "prefix_accepted": accepted}
        if accepted:

            def mask_of(m):
                mask = xgr.allocate_token_bitmask(1, len(vocab))
                m.fill_next_token_bitmask(mask)
                return mask

            original = mask_of(matcher)
            bits = [bool((int(original[0, i // 32]) >> (i % 32)) & 1) for i in range(len(vocab))]
            truth = [matcher.fork().accept_token(i) for i in range(len(vocab))]
            case.update(
                bits=bits, truth=truth, fork_equal=torch.equal(original, mask_of(matcher.fork()))
            )
            accepted_count = 0
            for _ in range(2):
                current = mask_of(matcher)
                choices = [
                    i for i in range(len(vocab) - 1) if (int(current[0, i // 32]) >> (i % 32)) & 1
                ]
                if not choices:
                    break
                assert matcher.accept_token(choices[0])
                accepted_count += 1
            matcher.rollback(accepted_count)
            case["rollback_equal"] = torch.equal(original, mask_of(matcher))
            restored = xgr.GrammarMatcher(
                xgr.CompiledGrammar.deserialize_json(compiled.serialize_json(), ti)
            )
            assert restored.accept_string(prefix)
            case["serialization_equal"] = torch.equal(original, mask_of(restored))
            matcher.reset()
            assert matcher.accept_string(prefix)
            case["reset_equal"] = torch.equal(original, mask_of(matcher))
            # Test a token that begins a Unicode character and one completing it.
            partial = matcher.fork()
            if partial.accept_token(18):
                pm = mask_of(partial)
                pb = [bool((int(pm[0, i // 32]) >> (i % 32)) & 1) for i in range(len(vocab))]
                pt = [partial.fork().accept_token(i) for i in range(len(vocab))]
                case.update(partial_bits=pb, partial_truth=pt)
            case["pass"] = (
                bits == truth
                and all(
                    case[k]
                    for k in ("fork_equal", "rollback_equal", "serialization_equal", "reset_equal")
                )
                and case.get("partial_bits") == case.get("partial_truth")
            )
        else:
            case["pass"] = (
                True  # Compare rejection with baseline; do not redefine its escape language.
            )
        result["cases"].append(case)

else:
    from transformers import AutoTokenizer

    revision = "f831ab66814297da540d832a5235f8e904f29d06"
    tokenizer = AutoTokenizer.from_pretrained(
        "moonshotai/Kimi-K3", revision=revision, trust_remote_code=True, local_files_only=True
    )
    ti = xgr.TokenizerInfo.from_huggingface(tokenizer, vocab_size=len(tokenizer))
    vocab_hash = hashlib.sha256()
    for token in ti.decoded_vocab:
        vocab_hash.update(len(token).to_bytes(4, "little"))
        vocab_hash.update(token)
    text = "const value = Number(event.target.value); return value > 0 ? value : 0; " * 40
    with contextlib.redirect_stdout(sys.stderr):
        ids = tokenizer.encode(text, add_special_tokens=False)
    result["provenance"].update(
        tokenizer_revision=revision,
        vocab_size=ti.vocab_size,
        token_count=len(ids),
        token_ids_sha256=hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
        vocab_sha256=vocab_hash.hexdigest(),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    if args.mode == "large-mask":
        compiler = xgr.GrammarCompiler(ti, max_threads=1)
        for style in ("json", "qwen_xml"):
            for upper in (1, 2, 129, 512):
                compiled = compile_tag(compiler, {"type": "string", "maxLength": upper}, style)
                for count in (max(0, upper - 1), upper):
                    matcher = xgr.GrammarMatcher(compiled)
                    assert matcher.accept_string(
                        "<arg>" + ('"' if style == "json" else "") + "a" * count
                    )
                    mask = xgr.allocate_token_bitmask(1, ti.vocab_size)
                    matcher.fill_next_token_bitmask(mask)
                    raw = mask.numpy().tobytes()
                    result["cases"].append(
                        {
                            "style": style,
                            "max": upper,
                            "count": count,
                            "mask_sha256": hashlib.sha256(raw).hexdigest(),
                            "mask_base64": base64.b64encode(raw).decode(),
                        }
                    )
        print(json.dumps(result))
        sys.exit(0)
    for style in ("json", "qwen_xml"):
        workloads = [("synthetic761", "", ids, None), ("synthetic761", "", ids, 100000)]
        with contextlib.redirect_stdout(sys.stderr):
            a_id = tokenizer.encode("a", add_special_tokens=False)[0]
        workloads += [("long_string", "a" * 8192, [a_id] * 64, bound) for bound in (None, 100000)]
        workloads += [("near_boundary", "a" * 448, [a_id] * 64, bound) for bound in (None, 512)]
        for workload, extra_prefix, replay_ids, upper in workloads:
            if args.controls_only and upper is not None:
                continue
            schema = {"type": "string"}
            if upper is not None:
                schema["maxLength"] = upper
            compiler = xgr.GrammarCompiler(ti, max_threads=1)
            begin = time.perf_counter()
            compiled = compile_tag(compiler, schema, style)
            compile_ms = (time.perf_counter() - begin) * 1000
            warm_start = time.perf_counter()
            compiler.compile_structural_tag(
                {
                    "type": "structural_tag",
                    "format": {
                        "type": "tag",
                        "begin": "<arg>",
                        "end": "</arg>",
                        "content": {"type": "json_schema", "json_schema": schema, "style": style},
                    },
                }
            )
            warm_compile_ms = (time.perf_counter() - warm_start) * 1000
            for run in range(args.rounds):
                matcher = xgr.GrammarMatcher(compiled)
                assert matcher.accept_string(
                    "<arg>" + ('"' if style == "json" else "") + extra_prefix
                )
                mask = xgr.allocate_token_bitmask(1, ti.vocab_size)
                fills, accepts, rollbacks, chains = [], [], [], []
                for pos, tid in enumerate(replay_ids):
                    t = time.perf_counter()
                    matcher.fill_next_token_bitmask(mask)
                    fills.append((time.perf_counter() - t) * 1000)
                    if pos + 2 < len(replay_ids):
                        t = time.perf_counter()
                        matcher.fill_next_token_bitmask(mask)
                        assert matcher.accept_token(tid)
                        matcher.fill_next_token_bitmask(mask)
                        assert matcher.accept_token(replay_ids[pos + 1])
                        matcher.fill_next_token_bitmask(mask)
                        r = time.perf_counter()
                        matcher.rollback(2)
                        rollbacks.append((time.perf_counter() - r) * 1000)
                        chains.append((time.perf_counter() - t) * 1000)
                    t = time.perf_counter()
                    assert matcher.accept_token(tid)
                    accepts.append((time.perf_counter() - t) * 1000)
                case = {
                    "style": style,
                    "maxLength": upper,
                    "workload": workload,
                    "run": run,
                    "compile_ms": compile_ms,
                    "warm_compile_ms": warm_compile_ms,
                    "fill": summary(fills),
                    "accept": summary(accepts),
                    "rollback": summary(rollbacks),
                    "chain": summary(chains),
                    "raw_fill_ms": fills,
                    "raw_chain_ms": chains,
                    "raw_accept_ms": accepts,
                    "raw_rollback_ms": rollbacks,
                }
                result["cases"].append(case)
                print(
                    json.dumps(
                        {
                            k: case[k]
                            for k in ("style", "maxLength", "workload", "run", "fill", "chain")
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )

print(json.dumps(result, ensure_ascii=True))
if any(not c.get("pass", True) for c in result["cases"]):
    sys.exit(1)
