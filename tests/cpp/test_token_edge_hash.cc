#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "fsm.h"
#include "grammar_functor.h"
#include "grammar_impl.h"
#include "xgrammar/grammar.h"

using namespace xgrammar;

namespace {
uint64_t TokenHash(
    std::vector<int32_t> tokens, bool exclude, int offset, bool partial, bool reverse = false
) {
  auto grammar =
      Grammar::FromEBNF(partial ? "root ::= other \"x\"\nother ::= \"y\"" : "root ::= \"x\"");
  FSM fsm(5);
  fsm.SetEdgeAuxData(std::vector<int32_t>(offset, 0));
  int start = partial ? 1 : 0;
  if (partial) fsm.AddRuleEdge(0, 1, 1);
  auto add_tokens = [&]() {
    if (exclude)
      fsm.AddExcludeTokenEdge(start, 2, tokens);
    else
      fsm.AddTokenEdge(start, 2, tokens);
  };
  if (reverse) fsm.AddTokenEdge(start, 3, {42});
  add_tokens();
  if (!reverse) fsm.AddTokenEdge(start, 3, {42});
  fsm.AddEdge(2, 4, 'a', 'a');
  fsm.AddEdge(3, 4, 'z', 'z');
  grammar->complete_fsm = fsm.ToCompact();
  grammar->per_rule_fsms.resize(grammar->NumRules());
  std::vector<bool> accepting_states(5, false);
  accepting_states[4] = true;
  grammar->per_rule_fsms[0] = CompactFSMWithStartEndWithSize(
      CompactFSMWithStartEnd(grammar->complete_fsm, 0, accepting_states),
      partial ? 5 : 4,
      partial ? 5 : 4
  );
  // The opaque child leaves root eligible only for IsPartialHashable().
  if (partial) grammar->per_rule_fsms[1] = std::nullopt;
  GrammarFSMHasher::Apply(&grammar);
  EXPECT_TRUE(grammar->per_rule_fsm_hashes[0].has_value());
  return grammar->per_rule_fsm_hashes[0].value();
}
}  // namespace

TEST(TokenEdgeHash, FullAndPartialHashesUseTokenValues) {
  for (bool partial : {false, true}) {
    for (bool exclude : {false, true}) {
      EXPECT_NE(TokenHash({1}, exclude, 0, partial), TokenHash({2}, exclude, 0, partial));
      EXPECT_NE(TokenHash({1}, exclude, 0, partial), TokenHash({1, 2}, exclude, 0, partial));
      EXPECT_EQ(
          TokenHash({2, 1, 1}, exclude, 0, partial), TokenHash({1, 2}, exclude, 100, partial, true)
      );
    }
    EXPECT_NE(TokenHash({1}, false, 0, partial), TokenHash({1}, true, 0, partial));
  }
}
