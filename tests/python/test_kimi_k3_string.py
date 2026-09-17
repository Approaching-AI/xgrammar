"""K3 control-ID isolation: byte-identical ordinary tokens remain string data."""

import pytest
import xgrammar as xgr
from xgrammar.structural_tag import (
    StructuralTag,
    SequenceFormat,
    TokenFormat,
    JSONSchemaFormat,
    ConstStringFormat,
)

SPECIALS = ["[BOS]", "<|open|>", "<|close|>", "<|sep|>", "<osagent_mode>", "[PAD]"]
VOCAB = [bytes([i]) for i in range(256)] + [s.encode() for s in SPECIALS]
INFO = xgr.TokenizerInfo(VOCAB, stop_token_ids=[256])
OPEN, CLOSE, SEP = 257, 258, 259


def tag(schema):
    return StructuralTag(
        atomic_token_ranges=[("[BOS]", "[PAD]")],
        format=SequenceFormat(
            elements=[
                TokenFormat(token="<|open|>"),
                ConstStringFormat(value='argument key="v" type="string"'),
                TokenFormat(token="<|sep|>"),
                JSONSchemaFormat(json_schema=schema, style="kimi_k3_string"),
                TokenFormat(token="<|close|>"),
                ConstStringFormat(value="argument"),
                TokenFormat(token="<|sep|>"),
            ]
        ),
    )


def compile_tag(schema, *, roundtrip=False, cache=True):
    compiler = xgr.GrammarCompiler(INFO, cache_enabled=cache)
    grammar = compiler.compile_structural_tag(tag(schema)).grammar
    grammar = xgr.Grammar.deserialize_json(grammar.serialize_json())
    compiled = compiler.compile_grammar(grammar)  # must not lose token policy via EBNF cache
    if roundtrip:
        compiled = xgr.CompiledGrammar.deserialize_json(compiled.serialize_json(), INFO)
    return compiled


PREFIX = [OPEN, *b'argument key="v" type="string"', SEP]
SUFFIX = [CLOSE, *b"argument", SEP]


def accepts(compiled, ids):
    matcher = xgr.GrammarMatcher(compiled)
    return all(matcher.accept_token(t) for t in ids) and matcher.is_completed()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "string", "maxLength": 300},
        {"type": "string", "minLength": 1},
        {"type": "string", "pattern": "[\\s\\S]*"},
    ],
)
@pytest.mark.parametrize("roundtrip", [False, True])
def test_identity_not_spelling(schema, roundtrip):
    compiled = compile_tag(schema, roundtrip=roundtrip)
    for text in ["a", "<", "<|open|>", "<|close|>argument<|sep|>", "</parameter>", "你好🙂"]:
        assert accepts(compiled, PREFIX + list(text.encode()) + SUFFIX), text
    # Dedicated controls cannot be consumed as raw string content.
    for control in range(256, 262):
        assert not accepts(compiled, PREFIX + [ord("a"), control, ord("b")] + SUFFIX)


@pytest.mark.parametrize(
    "schema,good,bad",
    [
        (
            {"type": "string", "minLength": 2, "maxLength": 3},
            ["你好", "<|", "abc"],
            ["", "a", "abcd"],
        ),
        ({"type": "string", "maxLength": 0}, [""], ["a"]),
        ({"type": "string", "enum": ["<|open|>", "hi"]}, ["hi", "<|open|>"], ["x"]),
        ({"type": "string", "const": "<|close|>"}, ["<|close|>"], ["close"]),
        ({"type": "string", "pattern": "a[<|b]*z"}, ["az", "a<|z"], ["xz"]),
    ],
)
def test_schema_preserved(schema, good, bad):
    compiled = compile_tag(schema)
    for text in good:
        assert accepts(compiled, PREFIX + list(text.encode()) + SUFFIX)
    for text in bad:
        assert not accepts(compiled, PREFIX + list(text.encode()) + SUFFIX)


def test_masks_accept_rollback_and_close_branch():
    compiled = compile_tag({"type": "string", "minLength": 2, "maxLength": 3}, roundtrip=True)
    matcher = xgr.GrammarMatcher(compiled, max_rollback_tokens=2048)
    mask = xgr.allocate_token_bitmask(1, len(VOCAB))
    for t in PREFIX + list("你好".encode()) + SUFFIX:
        matcher.fill_next_token_bitmask(mask)
        for token in range(len(VOCAB)):
            allowed = bool((int(mask[0, token // 32]) >> (token % 32)) & 1)
            accepted = matcher.accept_token(token)
            if accepted:
                matcher.rollback(1)
            assert accepted == allowed, token
        assert matcher.accept_token(t)
    assert matcher.is_completed()
    assert matcher.accept_token(256)  # EOS is still allowed at grammar completion.
    matcher.rollback(1)
    assert matcher.is_completed()


def test_no_control_bypass_through_any_tokens():
    payload = {
        "type": "structural_tag",
        "atomic_token_ranges": [["[BOS]", "[PAD]"]],
        "format": {
            "type": "sequence",
            "elements": [{"type": "any_tokens"}, {"type": "token", "token": "<|close|>"}],
        },
    }
    compiled = xgr.GrammarCompiler(INFO).compile_structural_tag(payload)
    assert accepts(compiled, list(b"<|open|>") + [CLOSE])
    assert not accepts(compiled, [OPEN, CLOSE])


def test_policy_opt_in_only():
    compiler = xgr.GrammarCompiler(INFO)
    grammar = compiler.compile_grammar("root ::= [^]*")
    assert accepts(grammar, [OPEN, CLOSE, SEP])
