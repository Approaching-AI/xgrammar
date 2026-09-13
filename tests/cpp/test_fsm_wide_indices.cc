#include <gtest/gtest.h>

#include "fsm.h"
#include "support/json_serializer.h"

using namespace xgrammar;

TEST(XGrammarWideFSMTest, RuleReferencesDoNotNarrow) {
  for (int32_t rule_id : {32767, 32768, 65535, 65536, 100000}) {
    SCOPED_TRACE(rule_id);
    FSM fsm(2);
    fsm.AddRuleEdge(0, 1, rule_id);
    EXPECT_EQ(fsm.GetEdges(0)[0].GetRefRuleId(), rule_id);
    EXPECT_EQ(fsm.GetNextState(0, rule_id, FSMEdge::kRuleRef), 1);
    auto compact = fsm.ToCompact();
    std::vector<int> targets;
    compact.GetNextStates(0, rule_id, FSMEdge::kRuleRef, &targets);
    EXPECT_EQ(targets, std::vector<int>({1}));
    std::unordered_set<int> advanced;
    compact.Advance({0}, rule_id, &advanced, FSMEdge::kRuleRef);
    EXPECT_EQ(advanced, std::unordered_set<int>({1}));
  }
}

TEST(XGrammarWideFSMTest, RepeatedRuleReferencesDoNotNarrow) {
  for (int32_t rule_id : {32767, 32768, 65535, 65536, 100000}) {
    SCOPED_TRACE(rule_id);
    FSM fsm(2);
    fsm.AddRepeatEdge(0, 1, rule_id, 2, 200);
    auto info = fsm.GetRepeatEdgeInfo(fsm.GetEdges(0)[0].GetAuxIndex());
    EXPECT_EQ(info.RuleId(), rule_id);
    EXPECT_EQ(info.Lower(), 2);
    EXPECT_EQ(info.Upper(), 200);
    auto compact = fsm.ToCompact();
    EXPECT_EQ(compact.GetRepeatEdgeInfo(compact.GetEdges(0)[0].GetAuxIndex()).RuleId(), rule_id);
  }
}

TEST(XGrammarWideFSMTest, AuxiliaryIndicesSurviveBuildMergeAndSerialization) {
  for (int32_t offset : {32767, 32768, 65535, 65536}) {
    SCOPED_TRACE(offset);
    FSM fsm(4);
    fsm.SetEdgeAuxData(std::vector<int32_t>(offset, 0));
    fsm.AddRepeatEdge(0, 1, 70000, 2, -1);
    fsm.AddTokenEdge(1, 2, {70001, 100000});
    fsm.AddExcludeTokenEdge(2, 3, {70002});
    ASSERT_EQ(fsm.GetEdges(0)[0].GetAuxIndex(), offset);
    ASSERT_EQ(fsm.GetEdges(1)[0].GetAuxIndex(), offset + 3);
    ASSERT_EQ(fsm.GetEdges(2)[0].GetAuxIndex(), offset + 6);
    EXPECT_EQ(fsm.GetRepeatEdgeInfo(offset).RuleId(), 70000);
    EXPECT_TRUE(fsm.GetTokenEdgeInfo(offset + 3).Contains(100000));
    EXPECT_FALSE(fsm.GetExcludeTokenEdgeInfo(offset + 6).Accepts(70002));

    FSM merged(1);
    merged.SetEdgeAuxData(std::vector<int32_t>(offset, 0));
    std::vector<int> mapping;
    merged.AddFSM(fsm, &mapping);
    ASSERT_EQ(merged.GetEdges(mapping[0])[0].GetAuxIndex(), 2 * offset);
    ASSERT_EQ(merged.GetEdges(mapping[1])[0].GetAuxIndex(), 2 * offset + 3);
    ASSERT_EQ(merged.GetEdges(mapping[2])[0].GetAuxIndex(), 2 * offset + 6);
    auto compact = merged.ToCompact();
    EXPECT_EQ(compact.GetRepeatEdgeInfo(2 * offset).RuleId(), 70000);
    EXPECT_TRUE(compact.GetTokenEdgeInfo(2 * offset + 3).Contains(70001));
    EXPECT_FALSE(compact.GetExcludeTokenEdgeInfo(2 * offset + 6).Accepts(70002));

    const auto edge = compact.GetEdges(mapping[2])[0];
    auto json = AutoSerializeJSONValue(edge);
    FSMEdge restored;
    ASSERT_FALSE(AutoDeserializeJSONValue(&restored, json).has_value());
    EXPECT_EQ(restored, edge);
    EXPECT_EQ(restored.GetAuxIndex(), 2 * offset + 6);
  }
}

TEST(XGrammarWideFSMTest, OptimizationsKeepWideLabelsDistinct) {
  for (int32_t low : {0, 32768}) {
    FSM fsm(4);
    fsm.AddEdge(0, 1, 'a', 'a');
    fsm.AddEdge(0, 2, 'b', 'b');
    fsm.AddRuleEdge(1, 3, low);
    fsm.AddRuleEdge(2, 3, low + 65536);
    FSMWithStartEnd source(fsm, 0, {false, false, false, true});
    auto dfa = source.ToDFA().Unwrap();
    auto minimized = source.MinimizeDFA().Unwrap();
    auto merged = source.MergeEquivalentStates();
    for (const auto& optimized : {source, dfa, minimized, merged}) {
      const auto& graph = optimized.GetFsm();
      int after_a = graph.GetNextState(optimized.GetStart(), 'a');
      int after_b = graph.GetNextState(optimized.GetStart(), 'b');
      ASSERT_NE(after_a, FSM::kNoNextState);
      ASSERT_NE(after_b, FSM::kNoNextState);
      EXPECT_NE(graph.GetNextState(after_a, low, FSMEdge::kRuleRef), FSM::kNoNextState);
      EXPECT_EQ(graph.GetNextState(after_a, low + 65536, FSMEdge::kRuleRef), FSM::kNoNextState);
      EXPECT_NE(graph.GetNextState(after_b, low + 65536, FSMEdge::kRuleRef), FSM::kNoNextState);
      EXPECT_EQ(graph.GetNextState(after_b, low, FSMEdge::kRuleRef), FSM::kNoNextState);
    }
  }
}
