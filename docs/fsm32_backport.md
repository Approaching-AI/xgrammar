# FSM index-width fix on the minLength backport

Version: `0.2.1+maxlength.20260907.minlength.20260909.fsm32.20260913`.
Parent: `0259b7d5e4549ec60f3b25d08b93dfdfed93f323`, the minLength follow-up on
`48d44de` (maxLength). This is not a main-branch upgrade.

## Defect and scope

The inherited v0.2.1 FSM stores rule references and auxiliary offsets in signed
16-bit edge fields. IDs above 32767 are narrowed before traversal or hashing;
65536 also aliases zero. The same issue affects repeated-rule access, auxiliary
offset relocation, DFA conversion and temporary edge-label containers.

This backport follows the 32-bit representation used by upstream
[XGrammar #652](https://github.com/mlc-ai/xgrammar/pull/652). It widens the entire
edge/index path and removes the narrowing cast in token-tag dispatch. Capacity
checks precede auxiliary-data growth and merging. It does not replace a failing
hash with a placeholder, disable caching, loosen a schema, or change the existing
minLength/maxLength lowering algorithms.

The old minLength package reproduces a `per_rule_fsm_hashes[ref_rule_id].has_value()`
failure on a synthetic 32770-rule grammar. The fixed package compiles and matches
it. This is evidence for the defect; it is not proof that an unavailable production
grammar has exactly the same cause.

## Validation

- `tests/cpp/test_fsm_wide_indices.cc`: 32767/32768 and 65535/65536 boundaries,
  direct and repeated references, token/exclude-token auxiliary data, merging,
  DFA/minimization/equivalent-state transforms and edge serialization.
- `tests/python/test_fsm_wide_indices.py`: large reachable rule graphs, cached
  and uncached compilation, full masks against an independent substring oracle,
  rollback/fork/reset, structural-tag compilation, concurrent shared-compiler
  use, large auxiliary-index compiled roundtrips, and failed-then-valid requests.
- Existing Python CPU regressions and native tests remain part of validation;
  minLength/maxLength, Unicode and serialization checks are not removed.
- `scripts/benchmark_fsm_width.py` compares fixed synthetic prefixes with a supplied
  K3 tokenizer. Use separate baseline/candidate processes and compare all grammar,
  token and full-vocabulary mask hashes. No production schema or tokenizer is bundled.

Run the CPU suite with the installed package selected explicitly:

```sh
HF_HUB_OFFLINE=1 OMP_NUM_THREADS=1 python -m pytest -q tests/python -m 'not hf_token_required'
```

## Compatibility and resource boundaries

The serialization identifier is
`v13-maxlength-20260907-minlength-20260909-fsm32-20260913`. Old serialized Grammar,
TokenizerInfo and CompiledGrammar objects are rejected and must be regenerated.
Do not rewrite the old version field to bypass validation: old objects may already
contain truncated IDs. The Python call interface remains unchanged.

`sizeof(FSMEdge)` grows from 8 to 16 bytes with its existing alignment. This changes
the internal native ABI and increases FSM storage. Rebuild/install the entire
package; do not mix old and new native objects or replace only a loaded library.
Finite memory capacity still applies even though the signed-16-bit limit is gone.

The inherited JSON serializer can repeat a shared compiled FSM for every rule.
The suffix-graph reproducer therefore has quadratic serialized size. Both parent
and candidate show this growth; full JSON export of the 32768-position graph is
not an acceptance test. Large uncompiled-grammar roundtrips, bounded compiled
roundtrips with wide auxiliary indices, and the existing compiled serialization
suite cover compatibility without that unbounded diagnostic allocation. This
change does not redesign the serializer or claim to solve its scaling limitation.

Runtime cache objects are not the same operation as exporting this entire JSON.
Leave compiler caching enabled when validating the index-width fix.
