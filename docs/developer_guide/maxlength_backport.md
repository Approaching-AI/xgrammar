# Internal 0.2.1 maxLength backport

Base: `5b4e9ce9e72524037ae24ecd831b9b6604d2eb48` (official v0.2.1).
Package: `0.2.1+maxlength.20260907`. This is an internal candidate, not an
official XGrammar release. The 0.2.5/main patch is not an ancestor.

## Implementation and hunk audit

Only the existing finite string length branch of the JSON and XML converters
changes. Pattern/format precedence, escaping, non-string constraints and
unbounded strings retain 0.2.1 behavior. This does not fix older JSON Schema
conformance limitations, including the bounded JSON string escape language.

| Files / hunks | Why required |
| --- | --- |
| `json_schema_converter.cc`, `json_schema_converter_ext.cc` | Replace only finite string repetition lowering with a budgeted character-star rule. |
| `ebnf_script_creator.h` | Emit a uniquely named internal budget rule and return its reference. |
| `grammar_impl.h` | Persist min/max codepoint limits on the string rule, including reflection. |
| `grammar_builder.h/.cc` | Set limits when constructing or copying a rule. |
| `grammar_parser.h/.cc`, `grammar_printer.cc` | Round-trip the internal `[min_chars=N,max_chars=M]` annotation. Reject it on any body except a single character-class star. |
| `grammar_functor.h` | Preserve budgets through the existing generic grammar copy. |
| `grammar_functor.cc`: SubGrammarAdder, StructureNormalizer, DeadCodeEliminator | Preserve budgets when renaming or rebuilding rules. |
| `grammar_functor.cc`: RuleInliner | Keep the budgeted rule instance as a counting boundary. |
| `grammar_functor.cc`: AllowEmptyRuleAnalyzer (two hunks) | A positive minimum makes an otherwise nullable character star nonempty. |
| `earley_parser.h/.cc` | Count UTF-8 leading bytes for the terminal string rule using its existing `repeat_count`; enforce upper bound on input and minimum on completion. State history, rollback, fork and parse-state equality already preserve this field. |
| `grammar_compiler.cc`: cache generator | Precompute position masks without the initial rule instance's current character count. Other budgeted rules entered while probing must still enforce their limits. |
| `grammar_compiler.cc`: cache namespace | Salt rule-level cache keys for budgeted grammars with their budget-bearing grammar fingerprint, because a parent's mask may depend on a child's limits. The original FSM hash algorithm is unchanged; grammar-level caching remains enabled. Cross-grammar reuse across distinct budget grammars is deliberately more conservative. |
| `grammar_matcher.cc` | Use cached masks in the string interior; near a budget boundary, reuse statically accepted tokens whose codepoint count fits, and precisely check possible crossings/uncertain tokens with common-prefix reuse. Before the minimum, use exact checks. Union contributions of all live states. Token codepoint counts are derived once per budgeted matcher; no such table is built for unbudgeted grammars. |
| `support/json_serializer.h` | Identify the changed Rule layout as `v13-maxlength-20260907`. Reject original v13 serialized data explicitly. |
| `pyproject.toml` | Internal package version only. Runtime/build dependency declarations are unchanged. |
| Tests and scripts | Compile regression, full-mask comparisons, Unicode/state restoration, serialization and directly measured three-node performance. |

The four protected files (`regex_converter.cc`, `fsm_builder.cc`, `fsm.h`,
`fsm.cc`) must be byte-identical to v0.2.1. `RuleRefGraphFinder`,
`GrammarFSMBuilderImpl`, and `GrammarFSMHasherImpl` must also be byte-identical.
Run `python scripts/audit_maxlength_backport.py` to enforce these boundaries.

## Reproducing the checks

Run all versions in separate processes with identical installed runtime
dependencies. Unpack a wheel into its own directory, pass that directory as
`--package-root`, and on macOS set `DYLD_LIBRARY_PATH` to its `xgrammar/`
directory. The checker removes the previous main editable import hook and
records the actual loaded Python/native-library paths.

```sh
python scripts/check_maxlength_backport.py --package-root /path/to/unpacked-wheel --mode compile
python scripts/check_maxlength_backport.py --package-root /path/to/unpacked-wheel --mode semantics
python scripts/check_maxlength_backport.py --package-root /path/to/unpacked-wheel --mode stateful
python scripts/check_maxlength_backport.py --package-root /path/to/unpacked-wheel --mode large-mask
python scripts/check_maxlength_backport.py --package-root /path/to/unpacked-wheel --mode performance --rounds 5
```

The large-vocabulary checks require the locally cached Kimi-K3 tokenizer at
revision `f831ab66814297da540d832a5235f8e904f29d06`. Performance output includes
raw per-operation measurements; three-node latency directly times
fill/accept/fill/accept/fill/rollback(2), rather than multiplying percentiles.
Compare the official wheel, unmodified source build, and patched source build
for correctness; use the two source builds for patch performance attribution.

## Compatibility and rollout boundary

The public Python API, 0.2.1 regex implementation, FSM construction, hash
scheduling, submodule revisions and dependency declarations are preserved.
Serialized v13 grammars/tokenizer metadata/compiled grammars are incompatible
with this internal format: regenerate them, do not reuse persisted old caches.

Linux SGLang service integration and the original production grammar replay
remain separate gates. No shared GPU, production service or dependency lock is
changed by the local checks. The production log contains only a truncated
grammar; request `9673e362b03c4781a1880e94a4a70f15` / hash prefix
`dcabaeb7f963` needs its full original grammar for exact replay.

## Local validation, 2026-09-07

Apple M5 / macOS 26.6.2 arm64, Python 3.12.13, torch 2.13.0,
apache-tvm-ffi 0.1.13.post3, transformers 5.16.1; both source builds use
the same 0.2.1 RelWithDebInfo CMake configuration and compiler. Dependency
declarations in the source package remain unchanged. Every compiler is tested
with cache enabled and max_threads=1.

- G0: official wheel, baseline source wheel and candidate source wheel agree on
  all 24 compilation cases, including large pattern repetitions and rejected lookaheads.
- G1: all three builds agree on 248 length/prefix full-mask cases, 19 stateful
  cases and 16 whole Kimi-vocabulary masks (163840 entries per mask).
- Existing relevant regressions: baseline 1381 + 137 passed; candidate 1396 +
  137 passed, including new gates and intentional grammar/serialization snapshot updates.
- G2: five replays per style/workload/treatment, with raw measurements retained.
  Experiment p99 is calculated from pooled measurements, not percentile multiplication.
- An initial unbounded JSON tail difference was investigated with six alternating
  baseline/candidate processes (ABBAAB), five replays each. Both versions have 15
  samples per control scenario; none of the median or p99 comparisons exceed
  both the 10% and 1 microsecond sustained-overhead thresholds.
- One 64-sample JSON boundary replay had a 10.009 ms maximum/p99 fill and
  20.085 ms maximum/p99 three-node fragment. It remains in the raw data; pooled
  p99s across 320 fills / 310 fragments are 2.842 / 8.172 ms.

There is a measured tradeoff: JSON maxLength=512 near-boundary filling is slower
than 0.2.1 (median of replay p99s: about 0.148 to 2.797 ms). Both absolute
latency targets are met, but this candidate does not promise improvement for
every maxLength/prefix combination. Qwen XML near-boundary p99 improves from
about 17.17 to 3.05 ms. Large-bound string interiors improve substantially.

The first implementation's all-token boundary scanning missed the performance
target and was replaced by codepoint-based filtering. An additional tail-rule
test caught a mask/accept mismatch when one token entered and exceeded a
bounded string; constraining cache-budget relaxation to its initial rule fixed
it. Both investigations and intermediate raw results are retained locally.

Linux SGLang integration and exact production replay have NOT passed: no
authorized target Linux service or complete production grammar was available.
