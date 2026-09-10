"""冒烟测试：0902 P2-2 修复——ai_client 业务级失败分流（离线，fake openai，不调真实 API）。

背景：AI_FAILOVER_ON_EMPTY=1（默认）下单 endpoint 业务级失败（空内容/validate 抛异常）
曾被升级为 NoAvailableEndpointError，触发业务层"立即降级规则摘要"，架空外层 3-5 次
温度递增重试（一次瞬时空响应 = 整批降级）。修复后按失败级别分流：
  - 业务级失败（空内容/validate 失败）→ 抛原始异常（保留业务层温度重试）
  - 基础设施级失败（429/鉴权/超时，已标记冷却）→ NoAvailableEndpointError（立即降级）

验证点：
  1. 单 endpoint + 空内容（业务级）→ 原始 ValueError，而非 NoAvailableEndpointError
  2. 单 endpoint + validate 失败（业务级）→ 原始异常
  3. 单 endpoint + 429（基础设施级）→ NoAvailableEndpointError（立即降级语义不变）
  4. 正常内容 → 正常返回
用法：python -X utf8 smoke_ai_client.py
"""
import sys
import types
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))


# ---- fake openai 模块（离线测试，不发起任何真实请求）----
class _Msg:
    def __init__(self, content):
        self.content = content
        self.reasoning_content = None


class _Choice:
    def __init__(self, msg):
        self.message = msg


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(_Msg(content))]


class _RateLimitError(Exception):
    pass


class _AuthenticationError(Exception):
    pass


class _PermissionDeniedError(Exception):
    pass


class _APITimeoutError(Exception):
    pass


class _APIConnectionError(Exception):
    pass


class _APIStatusError(Exception):
    pass


MODE = {"behavior": "empty"}  # empty / 429 / ok / bad_json


def _fake_create(**kw):
    b = MODE["behavior"]
    if b == "empty":
        return _Resp("")
    if b == "429":
        raise _RateLimitError("429 rate limit exceeded")
    if b == "ok":
        return _Resp("正常内容")
    if b == "bad_json":
        return _Resp("这不是JSON")
    raise AssertionError(f"未知 behavior: {b}")


class _Completions:
    create = staticmethod(_fake_create)


class _Chat:
    completions = _Completions()


class FakeOpenAI:
    def __init__(self, **kw):
        pass

    chat = _Chat()


_fake = types.ModuleType("openai")
_fake.OpenAI = FakeOpenAI
_fake.RateLimitError = _RateLimitError
_fake.AuthenticationError = _AuthenticationError
_fake.PermissionDeniedError = _PermissionDeniedError
_fake.APITimeoutError = _APITimeoutError
_fake.APIConnectionError = _APIConnectionError
_fake.APIStatusError = _APIStatusError
sys.modules["openai"] = _fake

from ai_client import AIClient, Endpoint, NoAvailableEndpointError  # noqa: E402


def _client():
    return AIClient([Endpoint(id="1", base_url="https://example.invalid", api_key="fixture-key", model="m")])


def case1_empty_content():
    """单 endpoint 空内容（业务级）→ 应抛原始 ValueError（保留业务层重试）"""
    MODE["behavior"] = "empty"
    try:
        _client().chat_create([{"role": "user", "content": "hi"}],
                              max_tokens=10, temperature=0.5)
        return "FAIL: 未抛异常"
    except NoAvailableEndpointError:
        return "FAIL: 被升级为 NoAvailableEndpointError（架空业务层温度重试）"
    except ValueError as e:
        return f"OK: 保留原始 ValueError（{e}）"


def case2_validate_fail():
    """单 endpoint validate 失败（业务级）→ 应抛原始异常"""
    MODE["behavior"] = "bad_json"

    def _validate(c):
        if "{" not in c:
            raise ValueError("JSON 解析失败")

    try:
        _client().chat_create([{"role": "user", "content": "hi"}], max_tokens=10,
                              temperature=0.5, validate=_validate)
        return "FAIL: 未抛异常"
    except NoAvailableEndpointError:
        return "FAIL: 被升级为 NoAvailableEndpointError"
    except ValueError as e:
        return f"OK: 保留原始 ValueError（{e}）"


def case3_rate_limit():
    """单 endpoint 429（基础设施级）→ 应抛 NoAvailableEndpointError（立即降级语义不变）"""
    MODE["behavior"] = "429"
    try:
        _client().chat_create([{"role": "user", "content": "hi"}],
                              max_tokens=10, temperature=0.5)
        return "FAIL: 未抛异常"
    except NoAvailableEndpointError:
        return "OK: NoAvailableEndpointError（立即降级语义保留）"
    except Exception as e:
        return f"FAIL: 异常类型错误 {type(e).__name__}: {e}"


def case4_ok():
    """正常内容 → 正常返回"""
    MODE["behavior"] = "ok"
    out = _client().chat_create([{"role": "user", "content": "hi"}],
                                max_tokens=10, temperature=0.5)
    return f"OK: 返回 {out!r}" if out == "正常内容" else f"FAIL: 返回 {out!r}"


if __name__ == "__main__":
    print("1. 空内容（业务级）      :", case1_empty_content())
    print("2. validate失败（业务级）:", case2_validate_fail())
    print("3. 429（基础设施级）     :", case3_rate_limit())
    print("4. 正常调用              :", case4_ok())
