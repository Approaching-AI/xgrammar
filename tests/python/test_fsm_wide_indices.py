"""Regression for rule IDs and FSM auxiliary indices that exceed signed int16."""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest
import torch

import xgrammar as xgr


@pytest.fixture(scope="module")
def large_substring():
    # Keep every suffix reachable, so optimization cannot remove the high rule IDs.
    count = 32768
    text = ("abc012 " * (count // 7 + 1))[:count]
    rules = [
        (
            f'p{i} ::= {json.dumps(char)} (p{i + 1} | "")'
            if i + 1 < count
            else f"p{i} ::= {json.dumps(char)}"
        )
        for i, char in enumerate(text)
    ]
    source = (
        "root ::= substring\nsubstring ::= "
        + " | ".join(f"p{i}" for i in range(count))
        + "\n"
        + "\n".join(rules)
    )
    return source, text


def tokenizer_info():
    return xgr.TokenizerInfo(
        [b"a", b"b", b"c", b"0", b"1", b"2", b" ", b"Z", b"<eos>"], stop_token_ids=[8]
    )


def check_substring_masks(compiled, text):
    matcher = xgr.GrammarMatcher(compiled, max_rollback_tokens=32)
    mask = xgr.allocate_token_bitmask(1, 9)
    prefix = ""
    for token in [0, 1, 2, 3, 4, 5, 6]:
        matcher.fill_next_token_bitmask(mask)
        expected = {i for i, char in enumerate("abc012 Z") if prefix + char in text}
        if prefix:
            expected.add(8)
        actual = {i for i in range(9) if int(mask[0, 0]) & (1 << i)}
        assert actual == expected
        saved = mask.clone()
        assert matcher.accept_token(token)
        matcher.rollback(1)
        matcher.fill_next_token_bitmask(mask)
        assert torch.equal(mask, saved)
        assert matcher.accept_token(token)
        prefix += "abc012 "[token]
    fork = matcher.fork()
    assert fork.accept_token(8) and fork.is_terminated()
    assert not matcher.is_terminated()
    assert not matcher.accept_token(7)
    matcher.reset()
    assert matcher.accept_string("012 abc")


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_large_rule_compile_masks(large_substring, cache_enabled):
    source, text = large_substring
    info = tokenizer_info()
    compiler = xgr.GrammarCompiler(info, max_threads=1, cache_enabled=cache_enabled)
    grammar = xgr.Grammar.from_ebnf(source)
    # Serialize the uncompiled grammar: the existing compiled-grammar JSON format
    # repeats its shared FSM once per rule, making this particular graph quadratic.
    serialized = grammar.serialize_json()
    assert len(json.loads(serialized)["rules"]) > 32767
    recovered = xgr.Grammar.deserialize_json(serialized)
    compiled = compiler.compile_grammar(recovered)
    check_substring_masks(compiled, text)


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_large_auxiliary_index_compiled_roundtrip(cache_enabled):
    count = 32768
    info = xgr.TokenizerInfo([b"a"] * count + [b"b", b"<eos>"], stop_token_ids=[count + 1])
    source = "root ::= Token(" + ",".join(map(str, range(count))) + f") Token({count})"
    compiler = xgr.GrammarCompiler(info, max_threads=1, cache_enabled=cache_enabled)
    compiled = compiler.compile_grammar(xgr.Grammar.from_ebnf(source))
    serialized = compiled.serialize_json()
    recovered = xgr.CompiledGrammar.deserialize_json(serialized, info)
    assert recovered.serialize_json() == serialized
    for context in [compiled, recovered]:
        matcher = xgr.GrammarMatcher(context, max_rollback_tokens=4)
        mask = xgr.allocate_token_bitmask(1, count + 2)
        matcher.fill_next_token_bitmask(mask)
        assert not int(mask[0, count // 32]) & (1 << (count % 32))
        assert matcher.accept_token(count - 1)
        matcher.fill_next_token_bitmask(mask)
        assert int(mask[0, count // 32]) & (1 << (count % 32))
        assert not int(mask[0, 0]) & 1
        saved = mask.clone()
        assert matcher.accept_token(count) and matcher.is_completed()
        matcher.rollback(1)
        matcher.fill_next_token_bitmask(mask)
        assert torch.equal(mask, saved)


def test_large_structural_tag_compile(large_substring):
    source, _ = large_substring
    tag = {
        "type": "structural_tag",
        "format": {
            "type": "tag",
            "begin": "<x>",
            "end": "</x>",
            "content": {"type": "grammar", "grammar": source},
        },
    }
    compiler = xgr.GrammarCompiler(tokenizer_info(), max_threads=1, cache_enabled=True)
    compiled = compiler.compile_structural_tag(tag)
    matcher = xgr.GrammarMatcher(compiled)
    assert matcher.accept_string("<x>abc012 </x>")
    assert matcher.is_completed()
    matcher.reset()
    assert not matcher.accept_string("<x>Z</x>")


def test_concurrent_large_compilation(large_substring):
    source, text = large_substring
    grammar = xgr.Grammar.from_ebnf(source)
    compiler = xgr.GrammarCompiler(tokenizer_info(), max_threads=2, cache_enabled=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        contexts = list(pool.map(lambda _: compiler.compile_grammar(grammar), range(4)))
    for compiled in contexts:
        check_substring_masks(compiled, text)


@pytest.mark.parametrize("kind", ["grammar", "tokenizer", "compiled"])
def test_previous_internal_serialization_rejected(kind):
    info = tokenizer_info()
    grammar = xgr.Grammar.from_ebnf('root ::= "a"')
    compiled = xgr.GrammarCompiler(info, max_threads=1).compile_grammar(grammar)
    objects = {"grammar": grammar, "tokenizer": info, "compiled": compiled}
    data = json.loads(objects[kind].serialize_json())
    data["__VERSION__"] = "v13-maxlength-20260907-minlength-20260909"
    with pytest.raises(RuntimeError, match="version"):
        if kind == "compiled":
            xgr.CompiledGrammar.deserialize_json(json.dumps(data), info)
        else:
            type(objects[kind]).deserialize_json(json.dumps(data))


def test_invalid_compile_does_not_poison_next_request():
    compiler = xgr.GrammarCompiler(tokenizer_info(), max_threads=2, cache_enabled=True)
    invalid = {
        "type": "structural_tag",
        "format": {
            "type": "json_schema",
            "json_schema": {"type": "object", "additionalProperties": {"$ref": "#/$defs/missing"}},
        },
    }
    with pytest.raises(RuntimeError):
        compiler.compile_structural_tag(invalid)
    compiled = compiler.compile_json_schema({"type": "string", "minLength": 200})
    matcher = xgr.GrammarMatcher(compiled)
    assert not matcher.accept_string(json.dumps("a" * 199))
    matcher.reset()
    assert matcher.accept_string(json.dumps("a" * 200)) and matcher.is_completed()
