# Kimi K3 token-aware string arguments

`JSONSchemaFormat(style="kimi_k3_string")` describes an unquoted string value.
It preserves the existing raw-string schema converter's supported pattern,
enum, const, format and Unicode character-length constraints, but removes
Qwen's `</parameter>` delimiter exclusion. There is **no character blacklist**:
ordinary tokens may spell `<|close|>argument<|sep|>` as literal data.

## Token identity policy

Opt in at the StructuralTag root:

```python
StructuralTag(
    atomic_token_ranges=[("[BOS]", "[PAD]")],
    format=SequenceFormat(elements=[
        TokenFormat(token="<|open|>"),
        ConstStringFormat(value='argument key="message" type="string"'),
        TokenFormat(token="<|sep|>"),
        JSONSchemaFormat(
            json_schema={"type": "string", "maxLength": 1000},
            style="kimi_k3_string",
        ),
        TokenFormat(token="<|close|>"),
        ConstStringFormat(value="argument"),
        TokenFormat(token="<|sep|>"),
    ]),
)
```

`atomic_tokens` accepts individual IDs or vocabulary spellings;
`atomic_token_ranges` accepts inclusive endpoint pairs, also resolved against
the actual vocabulary. K3's reserved block is contiguous from BOS through PAD
(256 slots in Kimi-K3-cdd2e49a). This model-specific layout assumption belongs
to the SGLang K3 builder, not the generic XGrammar matcher. HF all_special_ids
is not sufficient: open/close/sep have special=False in this checkpoint.

These IDs can traverse only explicit positive token edges. They cannot traverse
byte/text edges or wildcard/exclude-token edges. Stop tokens retain normal EOS
semantics: accepted only at a completed grammar. With an empty policy, existing
models retain their previous behavior.

The matcher mask and accept path enforce the same policy. In particular, accepting
the dedicated close token cannot retain a competing raw-string branch. The ordinary
BPE spelling remains data and cannot satisfy an explicit token delimiter.

## Integration and compatibility

SGLang lowers protocol literals (not schema enum/const values) to TokenFormat
sequences, including the argument/call boundaries and loose level-1 fallback.
The outer reasoning FSM retains its existing stage ownership.

All fixed protocol fragments are encoded with the model tokenizer: ordinary
labels such as `tools`, `call`, `argument`, `think`, `response`, and `message`
also require their canonical token sequences, not merely byte-identical text.
This includes closing tags and fixed attribute syntax. Dynamic `index` values
remain positive-integer grammar matches. Tool names and schema parameter names are separate
escaped literal-data nodes: marker spellings inside them must use ordinary
tokens and are never lowered to special-token terminals. The detector passes
the model tokenizer to all three K3 structural-tag builders; building without
it fails explicitly instead of weakening the protocol constraints.

The tool-channel splitter protects ordinary-token marker spellings before
passing text to the legacy detector. The detector restores the literal data
and raw source spans for non-streaming, streaming, logprobs and internal_content.
Without this parser change, a text-only delimiter search would misparse literals.

Grammar JSON and compiled artifact serialization retain the atomic policy.
Compilation of a Grammar object with this policy bypasses the EBNF round-trip
cache; structural-tag caching remains available. EBNF text alone cannot encode
this policy and must not be used to export these grammars. Text-only jump-forward
is disabled for policy-bearing grammars because re-tokenization loses identity.

Deploy the SGLang changes and the k3tokens XGrammar wheel together on P and D,
including tokenizer compilation workers. Old compiled artifacts must be
recompiled; both the package and serialization versions are bumped. Do not
silently fall back to qwen_xml.
CPU regression tests do not establish production GPU decode throughput.

## Token-edge cache fix (tokenhash.1)

Rule-level cache hashes include the actual normalized token sets for Token and
ExcludeToken edges, not their auxiliary-storage offsets. This prevents two
different protocol-token sequences from sharing incompatible masks. Both full
and partial FSM hashes use the same token-edge hashing and semantic ordering.

The package and serialization versions are bumped because previously compiled
artifacts may contain incorrect masks. Restart compiler/matcher processes and
recompile old artifacts when deploying; changing the hash cannot repair masks
already held by a live matcher. The cache remains enabled.
