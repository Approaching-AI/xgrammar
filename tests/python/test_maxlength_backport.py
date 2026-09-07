"""Regression gates for the internal 0.2.1 maxLength backport."""

import json

import pytest
import torch

import xgrammar as xgr


@pytest.mark.parametrize("style", ["json", "qwen_xml"])
@pytest.mark.parametrize("pattern", ["^a{128}$", "^a{129}$", "^a{129,200}$", "^(ab){129,200}$"])
def test_default_cache_large_regex(style, pattern):
    compiler = xgr.GrammarCompiler(xgr.TokenizerInfo(["a", "b", '"']), max_threads=1)
    compiler.compile_structural_tag(
        {
            "type": "structural_tag",
            "format": {
                "type": "json_schema",
                "style": style,
                "json_schema": {"type": "string", "pattern": pattern},
            },
        }
    )


def test_budget_reentry_masks_and_roundtrip():
    vocab = ["<a>", "</a>", "你", "😀", "a", "aa", "a</a><a>", "</a><a>aa", "<stop>"]
    ti = xgr.TokenizerInfo(vocab, stop_token_ids=[8])
    compiler = xgr.GrammarCompiler(ti, max_threads=1)
    tag = {
        "type": "structural_tag",
        "format": {
            "type": "star",
            "content": {
                "type": "tag",
                "begin": "<a>",
                "end": "</a>",
                "content": {
                    "type": "json_schema",
                    "style": "qwen_xml",
                    "json_schema": {"type": "string", "minLength": 1, "maxLength": 2},
                },
            },
        },
    }
    grammar = xgr.Grammar.from_structural_tag(tag)
    builds = [compiler.compile_grammar(grammar), compiler.compile_grammar(str(grammar))]
    builds.append(xgr.CompiledGrammar.deserialize_json(builds[0].serialize_json(), ti))
    masks = []
    for compiled in builds:
        matcher = xgr.GrammarMatcher(compiled)
        assert matcher.accept_string("<a>你好</a><a>😀")
        mask = xgr.allocate_token_bitmask(1, len(vocab))
        matcher.fill_next_token_bitmask(mask)
        for i in range(len(vocab)):
            assert bool((int(mask[0, i // 32]) >> (i % 32)) & 1) == matcher.fork().accept_token(i)
        assert matcher.accept_token(4)
        assert matcher.accept_token(1)
        matcher.rollback(2)
        after = mask.clone()
        matcher.fill_next_token_bitmask(after)
        torch.testing.assert_close(mask, after)
        masks.append(mask)
    for mask in masks[1:]:
        torch.testing.assert_close(masks[0], mask)


def test_reject_old_serialization_version():
    data = json.loads(
        xgr.Grammar.from_json_schema({"type": "string", "maxLength": 2}).serialize_json()
    )
    data["__VERSION__"] = "v13"
    with pytest.raises(xgr.DeserializeVersionError):
        xgr.Grammar.deserialize_json(json.dumps(data))


@pytest.mark.parametrize("prefix", ["", "prefix"])
def test_root_and_tail_reference_budget(prefix):
    vocab = ["a", "ab", "abc", "prefixabc", "prefixab", "<stop>"]
    ti = xgr.TokenizerInfo(vocab, stop_token_ids=[5])
    compiler = xgr.GrammarCompiler(ti, max_threads=1)
    grammar = f'root ::= "{prefix}" bounded\nbounded[min_chars=1,max_chars=2] ::= [a-z]*'
    matcher = xgr.GrammarMatcher(compiler.compile_grammar(grammar))
    mask = xgr.allocate_token_bitmask(1, len(vocab))
    matcher.fill_next_token_bitmask(mask)
    for i in range(len(vocab)):
        assert bool((int(mask[0, i // 32]) >> (i % 32)) & 1) == matcher.fork().accept_token(i)


@pytest.mark.parametrize("expr", ['"a"', "other", "TagDispatch()"])
def test_budget_rejects_non_character_star(expr):
    with pytest.raises(RuntimeError, match="single character-class star"):
        xgr.Grammar.from_ebnf(f'root[min_chars=0,max_chars=2] ::= {expr}\nother ::= "a"')


@pytest.mark.parametrize("limits", ["-1,2", "你,2", "0,2147483648", "3,2"])
def test_budget_rejects_invalid_limits(limits):
    lower, upper = limits.split(",")
    with pytest.raises(RuntimeError):
        xgr.Grammar.from_ebnf(f"root[min_chars={lower},max_chars={upper}] ::= [a-z]*")
