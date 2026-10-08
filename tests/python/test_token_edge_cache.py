"""Cross-grammar masks must distinguish token values, not auxiliary offsets."""

import json

import pytest

import xgrammar as xgr


VOCAB = [b"string", b"number", b"<|open|>", b"<|close|>", b"<eos>"]
INFO = xgr.TokenizerInfo(VOCAB, stop_token_ids=[4])


def assert_mask_matches_accept(matcher):
    mask = xgr.allocate_token_bitmask(1, len(VOCAB))
    matcher.fill_next_token_bitmask(mask)
    before = mask.clone()
    for token in range(len(VOCAB)):
        allowed = bool((int(mask[0, token // 32]) >> (token % 32)) & 1)
        accepted = matcher.accept_token(token)
        if accepted:
            matcher.rollback(1)
        assert allowed == accepted, (token, allowed, accepted)
    matcher.fill_next_token_bitmask(mask)
    assert mask.equal(before), "accept/rollback changed the next-token mask"


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind", ["token", "exclude_token"])
def test_cross_grammar_token_masks(kind, reverse, cache, atomic):
    compiler = xgr.GrammarCompiler(INFO, cache_enabled=cache, max_threads=1)
    for token in ([1, 0] if reverse else [0, 1]):
        body = (
            {"type": kind, "token": token}
            if kind == "token"
            else {"type": kind, "exclude_tokens": [token]}
        )
        compiled = compiler.compile_structural_tag(
            {
                "type": "structural_tag",
                "atomic_tokens": [2, 3] if atomic else [],
                "format": body,
            }
        )
        matcher = xgr.GrammarMatcher(compiled)
        assert_mask_matches_accept(matcher)
        assert_mask_matches_accept(matcher.fork())


@pytest.mark.parametrize("reverse", [False, True])
def test_protocol_sequence_shared_compiler(reverse):
    compiler = xgr.GrammarCompiler(INFO)
    for value in ([1, 0] if reverse else [0, 1]):
        compiled = compiler.compile_structural_tag(
            {
                "type": "structural_tag",
                "atomic_tokens": [2, 3],
                "format": {
                    "type": "sequence",
                    "elements": [{"type": "token", "token": t} for t in [2, value, 3]],
                },
            }
        )
        # A serialized artifact must retain the corrected masks too.
        compiled = xgr.CompiledGrammar.deserialize_json(compiled.serialize_json(), INFO)
        matcher = xgr.GrammarMatcher(compiled)
        for token in [2, value, 3]:
            assert_mask_matches_accept(matcher)
            assert matcher.accept_token(token)
        assert matcher.is_completed()
        matcher.rollback(3)
        assert_mask_matches_accept(matcher)


def test_old_compiled_masks_are_invalidated():
    compiled = xgr.GrammarCompiler(INFO).compile_regex("string")
    artifact = json.loads(compiled.serialize_json())
    artifact["__VERSION__"] = (
        "v13-maxlength-20260907-minlength-20260909-fsm32-20260913-k3tokens-20260917"
    )
    with pytest.raises(xgr.DeserializeVersionError):
        xgr.CompiledGrammar.deserialize_json(json.dumps(artifact), INFO)
