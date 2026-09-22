# Approaching-AI K3 backports

This fork preserves the official XGrammar v0.2.1 baseline and adds the following
patches, in order. The upstream `main` branch is retained; the default branch for
our modified source is `codex/xgrammar-021-fsm-int32-fix`.

| Checkpoint | Change |
| --- | --- |
| `5b4e9ce9e72524037ae24ecd831b9b6604d2eb48` | Official v0.2.1 baseline |
| `e976116` → `48d44de` | Finite maxLength character budgets and validation |
| `0259b7d5e4549ec60f3b25d08b93dfdfed93f323` | Retire large unbounded repetition prefix states for minLength |
| `6ce98307c7e82fed2f8cc8f643efb39edbe2e7e8` | Preserve 32-bit rule and auxiliary indices throughout FSM construction and traversal |
| `codex/xgrammar-021-worker-crash-hardening` | Native process safety, large-grammar scaling, and bounded XML whitespace state |

The package version is
`0.2.1+maxlength.20260907.minlength.20260909.fsm32.20260913.hardening.20260922`.
Later documentation-only publication commits do not change the tested runtime code.

## Worker crash hardening checkpoint

This checkpoint backports the process-safety portions of upstream #691, #702,
#704, #869, and #879 without merging upstream `main` wholesale:

- reject token/vocabulary inconsistencies before compiler worker threads index native arrays;
- rebuild derived tokenizer indices after JSON deserialization and preserve embedded NUL bytes;
- propagate the first task exception from `ThreadPool` to its caller instead of terminating;
- share identical repetition expansions and use sparse FSM end states to bound large-schema cost;
- remove ambiguous whitespace repetitions around unconstrained XML strings and guard every
  `Compact2DArray` append before an int32 CSR offset can overflow;
- reject minimum string/array counts beyond int32 and treat larger maxima as unbounded.

The fixed subprocess regressions changed from SIGSEGV, SIGBUS, and SIGABRT to successful
completion or catchable `RuntimeError`. On an isolated macOS CPU build, 16k and 50k flat-property
schemas compiled in 1.48 s / 9.61 s with peak RSS of about 541 MiB / 1.08 GiB. A GLM-style
unconstrained XML string accepted 66,560 spaces in 3 ms with two active parser states before and
after. The targeted non-HF suite passed 576 tests; Linux sanitizer and production K3 tokenizer
validation remain rollout gates.

## Source and installation

```sh
git clone --recurse-submodules --branch codex/xgrammar-021-fsm-int32-fix https://github.com/Approaching-AI/xgrammar.git
cd xgrammar
# Use an isolated environment with the matching runtime and build dependencies.
CMAKE_BUILD_PARALLEL_LEVEL=2 python -m pip install --no-build-isolation --no-deps .
```

Build dependencies include a C++17 compiler, CMake, Ninja, scikit-build-core and
apache-tvm-ffi. Linux validation used CPython 3.12 and apache-tvm-ffi 0.1.11.
Installing `xgrammar` from upstream PyPI does not select this internal patch stack.
Keep deployment dependencies pinned and validate in the actual target image.

## Validation recorded for the FSM32 checkpoint

- A synthetic grammar with 32770 source rules reproduces the old
  `per_rule_fsm_hashes[ref_rule_id].has_value()` failure on macOS and Linux.
- The final Linux source-package wheel passed **2161 CPU tests**;
  57 GPU/MLX-related tests were skipped and 618 HF-token-gated tests deselected.
- **51 native C++ tests** passed Linux AddressSanitizer, UndefinedBehaviorSanitizer
  and LeakSanitizer checks.
- Eight fixed K3 states had identical grammar, token IDs and complete
  163840-entry vocabulary mask hashes before and after the FSM32 fix.
- Measured fill medians changed by approximately -0.2% to +2.9%; three-node
  medians increased by up to 3.8%. Estimated compiled-cache size increased by
  approximately 0.3% to 10.4% in those cases. These are bounded CPU observations,
  not a guarantee about production latency or memory use.

Tests and synthetic benchmark tools are included in this branch. Production
requests, tokenizer assets, credentials and private runtime logs are not included.
The exact production grammar associated with the reported crash was unavailable;
the reproduced capacity defect must not be confused with an exclusive incident
root-cause determination or a completed production rollout.

## Upgrade boundaries

The internal serialization identifier is
`v13-maxlength-20260907-minlength-20260909-fsm32-20260913`.
Regenerate old XGrammar Grammar, TokenizerInfo and CompiledGrammar serialized data;
do not rewrite version fields to reuse potentially truncated indices. This does
not require deleting model weights, model tokenizer files or KV caches.

FSMEdge's internal native layout grows from 8 to 16 bytes. Rebuild/install the
whole package and verify the libraries loaded by running processes during an
approved rollout. Do not mix old and new native objects or hot-overwrite a loaded
library. Compiler caching and the prior min/maxLength semantics remain enabled.

The hardening checkpoint does not change serialized field layout, so the FSM32
serialization identifier remains unchanged. Validation and compilation behavior do
change; deploy the matching package version atomically on tokenizer and scheduler workers.

The inherited serializer can produce quadratic output when exporting certain
large shared compiled FSM graphs. That separate scaling limitation is unchanged.
See [FSM32 technical notes](fsm32_backport.md) for scope and regression coverage;
the [maxLength-only audit](developer_guide/maxlength_backport.md) is historical.
