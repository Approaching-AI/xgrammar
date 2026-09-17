/* Copyright (c) 2026 by Contributors */
#ifndef XGRAMMAR_KIMI_K3_STRING_H_
#define XGRAMMAR_KIMI_K3_STRING_H_

#include <xgrammar/grammar.h>

#include <string>

namespace xgrammar {
// Raw XTML string values without character exclusions. The root grammar's
// atomic-token policy separates protocol token IDs from ordinary string data.
Grammar KimiK3StringGrammar(const std::string& schema);
}  // namespace xgrammar
#endif
