import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cliphunter.config import Settings
from cliphunter.llm.budget import Budget, BudgetExceeded, cost_usd, estimate_video_cost
from cliphunter.llm.cache import DiskCache, cache_key
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.prompts import load_prompt
from cliphunter.llm.schemas import JudgeResponse, RefineResponse, ScanResponse


def settings(tmp: Path, **kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp, gemini_api_key="k-secret-123", **kw)


class FakeSDK:
    def __init__(self, text: str = '{"notes":null,"clips":[]}', **usage: int) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text
        self._usage = {
            "total_input_tokens": 100_000,
            "total_output_tokens": 500,
            "total_thought_tokens": 1500,
            "total_tool_use_tokens": 0,
            **usage,
        }
        self.interactions = SimpleNamespace(create=self._create)

    def _create(self, **body: Any) -> Any:
        self.calls.append(body)
        return SimpleNamespace(output_text=self._text, usage=SimpleNamespace(**self._usage))


def test_cost_formula() -> None:
    s = Settings(_env_file=None)
    # 1M in * 0.75 + 1M out * 3.75
    assert cost_usd(1_000_000, 1_000_000, s) == pytest.approx(4.5)
    assert estimate_video_cost(600, "low", s, expected_out_tokens=0) == pytest.approx(0.045)


def test_budget_guard() -> None:
    b = Budget(1.0)
    b.add(0.8)
    b.check(0.2)
    with pytest.raises(BudgetExceeded):
        b.check(0.21)


def test_cache_key_is_order_independent_and_sensitive() -> None:
    assert cache_key(a=1, b=2) == cache_key(b=2, a=1)
    assert cache_key(a=1) != cache_key(a=2)


def test_call_accounts_usage_and_caches(tmp_path: Path) -> None:
    s = settings(tmp_path)
    sdk = FakeSDK()
    client = GeminiVideoClient(s, sdk=sdk)
    kw: dict[str, Any] = {
        "stage": "t",
        "mode": "file",
        "video": {"type": "video", "uri": "files/x"},
        "prompt": "p",
        "system": "sys",
        "schema": ScanResponse,
    }
    r1 = client.call(**kw)
    assert not r1.cache_hit
    # thinking tokens billed as output: (100000*0.75 + 2000*3.75) / 1e6
    assert r1.cost_usd == pytest.approx(0.0825)
    assert client.budget.spent_usd == pytest.approx(0.0825)
    body = sdk.calls[0]
    assert body["model"] == "gemini-3.8-flash"
    assert body["input"][-1]["type"] == "text"  # text after video
    assert body["system_instruction"] == "sys"
    assert body["response_format"]["mime_type"] == "application/json"
    assert body["generation_config"] == {"thinking_level": "medium"}

    r2 = client.call(**kw)
    assert r2.cache_hit and r2.cost_usd == 0.0
    assert len(sdk.calls) == 1  # second call never reached the API
    assert client.budget.spent_usd == pytest.approx(0.0825)

    lines = [json.loads(x) for x in client.log_path.read_text(encoding="utf-8").splitlines()]
    assert [x["cache_hit"] for x in lines] == [False, True]
    assert {"model", "mode", "tokens_in", "tokens_out", "cost_usd", "latency_ms"} <= set(lines[0])
    assert "k-secret-123" not in client.log_path.read_text(encoding="utf-8")


def test_budget_blocks_before_api_call(tmp_path: Path) -> None:
    s = settings(tmp_path)
    sdk = FakeSDK()
    client = GeminiVideoClient(s, budget=Budget(0.01), sdk=sdk)
    with pytest.raises(BudgetExceeded):
        client.call(
            stage="t", mode="file", video={"type": "video", "uri": "u"}, prompt="p",
            estimated_cost_usd=0.5,
        )  # fmt: skip
    assert sdk.calls == []


def test_error_is_logged(tmp_path: Path) -> None:
    s = settings(tmp_path)

    class Boom:
        interactions = SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(ValueError("x")))

    client = GeminiVideoClient(s, sdk=Boom())
    with pytest.raises(ValueError):
        client.call(stage="t", mode="m", video={"type": "video", "uri": "u"}, prompt="p")
    rec = json.loads(client.log_path.read_text(encoding="utf-8").splitlines()[0])
    assert "ValueError" in rec["error"]


def test_cache_is_persistent(tmp_path: Path) -> None:
    c = DiskCache(tmp_path / "c")
    c.put("k", {"text": "t", "usage": {}})
    assert DiskCache(tmp_path / "c").get("k") == {"text": "t", "usage": {}}
    assert c.get("missing") is None


@pytest.mark.parametrize("name", ["scan", "refine", "judge"])
def test_prompts_have_version(name: str) -> None:
    p = load_prompt(name)
    assert p.version == 1
    assert len(p.body) > 100


def test_prompt_without_version_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("no version\nbody", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt("x", tmp_path)


def test_schemas_validate_and_reject() -> None:
    clip = {
        "start": "01:00", "end": "01:30", "confidence_timestamps": "high",
        "category": "funny", "working_title": "t", "hook_text": None, "visual_hook": "v",
        "what_happens": "w", "visual_evidence": "e", "audio_evidence": "a", "why_viral": "y",
        "ending_text": None, "title_options": ["a"], "score": 7,
    }  # fmt: skip
    ScanResponse.model_validate({"notes": None, "clips": [clip]})
    with pytest.raises(ValueError):
        ScanResponse.model_validate({"notes": None, "clips": [{**clip, "score": 11}]})
    with pytest.raises(ValueError):
        ScanResponse.model_validate({"notes": None, "clips": [{**clip, "category": "bad"}]})
    RefineResponse.model_validate(
        {"verdict": "drop", "start": None, "end": None, "score": 2, "reason": "r"}
    )
    with pytest.raises(ValueError):
        JudgeResponse.model_validate({"items": [{"clip_index": 0}]})
