/* Copyright (c) 2026 by Contributors */
#include "kimi_k3_string.h"

#include "grammar_functor.h"
#include "json_schema_converter.h"

namespace xgrammar {
namespace {
// Reuse raw-string schema handling, removing Qwen's XML delimiter exclusion.
// K3 boundaries are token terminals, not forbidden character sequences.
class K3RawString : public GrammarFunctor<> {
 protected:
  int32_t VisitTagDispatch(const GrammarExpr& expr) final {
    auto tag = base_grammar_->GetTagDispatch(expr);
    XGRAMMAR_CHECK(tag.tag_rule_pairs.empty() && !tag.loop_after_dispatch)
        << "kimi_k3_string requires a string-only schema";
    return builder_->AddCharacterClassStar({}, true);
  }
};
}  // namespace

Grammar KimiK3StringGrammar(const std::string& schema) {
  return GrammarNormalizer::Apply(
      K3RawString().Apply(Grammar::FromEBNF(QwenXMLToolCallingToEBNF(schema)))
  );
}
}  // namespace xgrammar
