"""Regression coverage for large lower bounds on unbounded repetitions."""

import json

import pytest
import torch

import xgrammar as xgr
from xgrammar.testing import _is_grammar_accept_string


@pytest.mark.parametrize("minimum", [128, 129, 200, 513])
@pytest.mark.parametrize(
    "expression,unit", [('"a"', "a"), ('[a中🙂]', "🙂"), ('("ab" | "c")', "ab")]
)
def test_unbounded_lower_bound_language(minimum, expression, unit):
    grammar = xgr.Grammar.from_ebnf(
        f'root ::= "<" ({expression}){{{minimum},}} ">"'
    )
    for count in [0, minimum - 1, minimum, minimum + 1, minimum + 300]:
        assert _is_grammar_accept_string(grammar, "<" + unit * count + ">") == (
            count >= minimum
        )


@pytest.mark.parametrize("expression", ['("a" | "aa")', '("a" | "")'])
def test_ambiguous_and_nullable_unbounded_repetition(expression):
    grammar = xgr.Grammar.from_ebnf(f'root ::= {expression}{{200,}} "!"')
    nullable = '""' in expression
    for count in [0, 199, 200, 201, 400]:
        assert _is_grammar_accept_string(grammar, "a" * count + "!") == (
            nullable or count >= 200
        )
    assert not _is_grammar_accept_string(grammar, "b!")


@pytest.mark.parametrize("style", ["json", "qwen_xml"])
@pytest.mark.parametrize("character", ["a", "中", "🙂"])
def test_minlength_only_structural_tag(style, character):
    grammar = xgr.Grammar.from_structural_tag(
        {
            "type": "structural_tag",
            "format": {
                "type": "tag",
                "begin": "<x>",
                "end": "</x>",
                "content": {
                    "type": "json_schema",
                    "style": style,
                    "json_schema": {"type": "string", "minLength": 200},
                },
            },
        }
    )
    for count in [199, 200, 201, 1000]:
        value = character * count
        if style == "json":
            value = json.dumps(value, ensure_ascii=False)
        assert _is_grammar_accept_string(grammar, "<x>" + value + "</x>") == (
            count >= 200
        )


@pytest.mark.parametrize("container", ["object", "array"])
def test_nested_minlength_only(container):
    leaf = {"type": "string", "minLength": 200}
    schema = (
        {"type": "object", "properties": {"text": leaf}, "required": ["text"]}
        if container == "object"
        else {"type": "array", "items": leaf, "minItems": 1}
    )
    grammar = xgr.Grammar.from_json_schema(schema)
    for count in [199, 200, 201]:
        value = {"text": "中" * count} if container == "object" else ["中" * count]
        assert _is_grammar_accept_string(grammar, json.dumps(value, ensure_ascii=False)) == (
            count >= 200
        )


@pytest.mark.parametrize("minimum", [129, 200])
def test_large_array_minimum(minimum):
    grammar = xgr.Grammar.from_json_schema(
        {"type": "array", "items": {"type": "integer", "enum": [1]}, "minItems": minimum}
    )
    for count in [minimum - 1, minimum, minimum + 1, minimum + 20]:
        assert _is_grammar_accept_string(grammar, json.dumps([1] * count)) == (
            count >= minimum
        )


def test_regex_minimum_not_weakened():
    grammar = xgr.Grammar.from_regex(r"^a{200,}$")
    for count in [199, 200, 201, 1000]:
        assert _is_grammar_accept_string(grammar, "a" * count) == (count >= 200)


@pytest.mark.parametrize("minimum", [129, 200, 513])
@pytest.mark.parametrize("cache_enabled", [False, True])
def test_masks_match_explicit_prefix_with_rollback(minimum, cache_enabled):
    # A literal required prefix is an independent oracle for the same language.
    unit = "[a中🙂]"
    grammar = xgr.Grammar.from_ebnf(f'root ::= "<" {unit}{{{minimum},}} ">"')
    oracle = xgr.Grammar.from_ebnf(
        'root ::= "<" ' + " ".join([unit] * minimum) + f' {unit}* ">"'
    )
    vocab = [bytes([i]) for i in range(256)] + ["中".encode(), "🙂".encode(), b"aaaa", b"<eos>"]
    stop_id = len(vocab) - 1
    info = xgr.TokenizerInfo(vocab, stop_token_ids=[stop_id])
    compiler = xgr.GrammarCompiler(info, max_threads=1, cache_enabled=cache_enabled)
    contexts = [compiler.compile_grammar(g) for g in [grammar, oracle]]
    masks = [xgr.allocate_token_bitmask(1, len(vocab)) for _ in contexts]
    for count in [0, minimum - 1, minimum, minimum + 1, minimum + 500]:
        matchers = [xgr.GrammarMatcher(ctx, max_rollback_tokens=32) for ctx in contexts]
        for matcher in matchers:
            assert matcher.accept_string("<" + "a" * count)
        for matcher, mask in zip(matchers, masks):
            matcher.fill_next_token_bitmask(mask)
        assert torch.equal(*masks)
        before = masks[0].clone()
        # Include a split UTF-8 character and a multi-byte, single-token character.
        tokens = list("中".encode()) + [257, ord("a")]
        for token in tokens:
            for matcher, mask in zip(matchers, masks):
                assert matcher.accept_token(token)
                matcher.fill_next_token_bitmask(mask)
            assert torch.equal(*masks)
        for matcher, mask in zip(matchers, masks):
            matcher.rollback(len(tokens))
            matcher.fill_next_token_bitmask(mask)
        assert torch.equal(*masks)
        assert torch.equal(masks[0], before)
        for matcher in matchers:
            assert matcher.accept_token(ord(">")) == (count >= minimum)


def test_old_maxlength_serialization_is_rejected():
    grammar = xgr.Grammar.from_ebnf('root ::= "a"{200,}')
    info = xgr.TokenizerInfo([b"a", b"<eos>"], stop_token_ids=[1])
    compiled = xgr.GrammarCompiler(info).compile_grammar(grammar)
    for obj, deserialize in [
        (grammar, xgr.Grammar.deserialize_json),
        (info, xgr.TokenizerInfo.deserialize_json),
        (compiled, lambda data: xgr.CompiledGrammar.deserialize_json(data, info)),
    ]:
        data = json.loads(obj.serialize_json())
        data["__VERSION__"] = "v13-maxlength-20260907"
        with pytest.raises(xgr.DeserializeVersionError):
            deserialize(json.dumps(data))
