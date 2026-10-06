from __future__ import annotations

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.benchmark.locks import BENCHMARK_ID_VERSION, BenchmarkIdentityInput


def benchmark_identity(identity_input: BenchmarkIdentityInput) -> str:
    payload = identity_input.model_dump(mode="python")
    payload["benchmark_id_version"] = BENCHMARK_ID_VERSION
    return f"spy-v2p2-{sha256_json(payload)[:24]}"
