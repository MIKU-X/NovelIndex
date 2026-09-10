"""AI 调用基础设施层：多 endpoint 池 + round-robin 轮转 + 冷却 + 故障转移。

业务层（build_latest.py 的 generate_ai_summaries / enrich_market_summary_with_ai /
build_creation_suggestions）保留各自的外部重试循环（空内容 / JSON 解析 / 温度递增 /
降级规则摘要），仅把"创建单 client + client.chat.completions.create(...)"替换为
"ai_client.chat_create(...)"。本模块只负责"单次 chat_create 调用内遍历可用 endpoint
直到成功或全失败"，故障转移状态（游标、冷却）为单进程内实例级状态，不持久化。

配置（.env，优先级：编号配置 > 旧单配置）：
  方式一（旧，向后兼容）：API_BASE_URL / API_KEY / API_MODEL
  方式二（多 endpoint）：API_1_BASE_URL / API_1_KEY / API_1_MODEL，API_2_* ...
    同供应商写多编号 = 分摊配额；不同供应商 = 故障转移。
  AI_FAILOVER_ON_TIMEOUT=1（默认）：超时/连接错误也切换；=0 仅 429/401/403 切换
  AI_FAILOVER_COOLDOWN_429=60  AI_FAILOVER_COOLDOWN_TIMEOUT=30  AI_FAILOVER_COOLDOWN_AUTH=300
  AI_PRIMARY_ONLY=1：主从模式，始终优先 API_1（主 endpoint），仅当主失败/冷却才
    切到后续备份，成功后不轮换（贴合"首选 X"诉求）；默认 0 = round-robin 均匀分摊。
  AI_FAILOVER_ON_EMPTY=1（默认）：业务级失败（空内容/validate 回调抛异常）也切换 endpoint；
    =0 仅在业务层重试不触发故障转移（向后兼容旧行为）。遍历完仍无成功时按失败级别分流
    （P2-0902 修复）：业务级失败抛原始异常（保留业务层温度递增重试——此前单 endpoint
    一次瞬时空响应即被升级为 NoAvailableEndpointError 立即降级，架空重试）；
    基础设施级失败（429/鉴权/超时，已标记冷却）才抛 NoAvailableEndpointError。
  AI_FAILOVER_COOLDOWN_EMPTY=0：业务级失败冷却秒数（默认 0=不冷却）。多 endpoint 主从
    模式建议设 30-60，避免主 endpoint 系统性业务级失败时每次重试都先撞注定失败的主 endpoint。
  AI_ENABLE_THINKING=1：强制下发 extra_body.enable_thinking=False（即使模型名不在已知
    reasoning 名单）；AI_DISABLE_THINKING=1：强制不下发（优先级最高，规避 kimi 等网关
    对未知参数 400）。默认仅对已知 reasoning 模型名（glm/deepseek/r1/reasoner/kimi）下发。
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.parse import urlsplit

try:
    from .secret_redaction import mask_secrets
except ImportError:  # Direct script imports used by the command-line builders.
    from secret_redaction import mask_secrets


@dataclass
class Endpoint:
    """单个 API endpoint 配置。id 用于日志与冷却 key。"""

    id: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str


# B10: 已知 reasoning 模型名特征（小写匹配）。默认仅对这些模型下发
# extra_body.enable_thinking=False，其余模型不发送。注意：kimi 系网关（如 opencode）
# 实测拒绝该未知参数（400 Extra inputs not permitted），故不在默认名单；如需强制关闭
# kimi 的思考链，用 AI_ENABLE_THINKING=1。
_REASONING_MODEL_HINTS = ("glm", "deepseek", "r1", "reasoner")


def valid_endpoint_url(value):
    """Remote API keys require TLS; plain HTTP is supported only on loopback."""
    try:
        url = urlsplit(value)
        if not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            return False
        if url.scheme == 'https':
            return True
        if url.scheme != 'http':
            return False
        if url.hostname == 'localhost':
            return True
        return ip_address(url.hostname).is_loopback
    except ValueError:
        return False


class NoAvailableEndpointError(RuntimeError):
    """P1-2: 本轮 chat_create 遍历后无任何可用 endpoint（全部冷却/已试过/未配置）。

    与普通调用失败区分：普通失败（如 403/超时）是"某个 endpoint 失败"，重试或
    切 endpoint 有意义；而"无可用 endpoint"是系统性不可用（如全部 endpoint 冷却中、
    配额用尽），重试只会继续空转。业务层捕获到本异常应**立即降级规则摘要**，
    不再走重试循环，避免 9 榜 × deadline 纯空转。
    P2-0902: 业务级失败（空内容/validate 抛异常）遍历完**不抛本异常**——那不是
    "endpoint 不可用"而是"本次内容不合格"，抛原始异常保留业务层温度重试。
    """


def _env_float(name: str, default: float) -> float:
    """N49: 读取浮点型环境变量，非法值（如 abc）安全回退默认值，
    避免一处 .env 配置手误导致 AIClient 初始化崩溃、整榜 AI 流程失效。"""
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        # 纯 ASCII 前缀（与下方 [FAILOVER] 同一约定）：本模块可能被未 reconfigure
        # stdout 的上下文（GBK 控制台）调用，emoji/中文直接打印会 UnicodeEncodeError
        print(f"[WARN] env {name} 不是合法数字，使用默认值 {default}")
        return default


def load_ai_endpoints() -> list[Endpoint]:
    """加载 endpoint 池，兼容旧单配置回退。

    优先级：
    1) 编号配置 API_1_*/API_2_*...（按编号升序，三件套齐全才入池，部分配置警告跳过）
    2) 回退：旧 API_BASE_URL/API_KEY/API_MODEL（打包为单个 id="legacy" 的 endpoint）

    编号配置一旦存在任意完整项，不再回退旧配置（避免新旧混用导致 model/base_url 错配）。
    返回空列表表示未配置 AI（调用方走规则摘要降级）。
    """
    endpoints: list[Endpoint] = []
    # 1) 扫描编号配置 API_1_.. API_9_..（上限 9 足够）
    for i in range(1, 10):
        base = os.environ.get(f"API_{i}_BASE_URL", "").strip()
        key = os.environ.get(f"API_{i}_KEY", "").strip()
        model = os.environ.get(f"API_{i}_MODEL", "").strip()
        if base and key and model:
            endpoints.append(Endpoint(id=str(i), base_url=base, api_key=key, model=model))
        elif base or key or model:
            # 部分配置：打印警告但跳过（不阻断，沿用项目"宁可降级不崩溃"风格）
            print(f"⚠️  API_{i}_* 配置不完整（需 BASE_URL/KEY/MODEL 三件套），已跳过")
    # 2) 回退旧配置（仅当无任何编号配置时）
    if not endpoints:
        base = os.environ.get("API_BASE_URL", "").strip()
        key = os.environ.get("API_KEY", "").strip()
        model = os.environ.get("API_MODEL", "").strip()
        if base and key and model:
            endpoints.append(Endpoint(id="legacy", base_url=base, api_key=key, model=model))
    valid = []
    for ep in endpoints:
        if valid_endpoint_url(ep.base_url):
            valid.append(ep)
        else:
            print(f'[WARN] endpoint#{ep.id} URL 不安全，已跳过：远程地址需要 HTTPS，且不能带凭据、查询串或片段')
    return valid


def _should_failover(exc: Exception, failover_on_timeout: bool) -> bool:
    """判断异常是否应触发 endpoint 切换。

    - 429/鉴权失败：必切
    - 超时/连接错误：受 AI_FAILOVER_ON_TIMEOUT 开关控制
    - 其他 APIStatusError（5xx 等）：切（基础设施层问题，换 endpoint 合理）
    - 中转站非标准异常：用字符串/状态码兜底
    - ValueError（空内容）/JSON 解析异常：不切（业务层重试处理，换 endpoint 无益）
    """
    # openai 异常类在调用处惰性导入后通过 isinstance 判断；这里用动态获取兼容惰性 import
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError:
        # openai 未安装时无法做精细判断，保守起见对非业务异常都尝试切换
        APIConnectionError = APIStatusError = APITimeoutError = type(None)
        AuthenticationError = PermissionDeniedError = RateLimitError = type(None)

    # 1) 明确的鉴权/配额类：必切
    if isinstance(exc, (RateLimitError, AuthenticationError, PermissionDeniedError)):
        return True
    # 2) 超时/连接错误：受开关控制（APITimeoutError 是 APIConnectionError 子类，放一起）
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return failover_on_timeout
    # 3) 其他 APIStatusError（5xx 等）：切
    if isinstance(exc, APIStatusError):
        return True
    # 4) 中转站兜底：某些中转会把 429 包成普通 Exception 或返回非标准 JSON
    msg = str(exc).lower()
    if any(k in msg for k in ("429", "rate limit", "quota", "配额", "余额不足")):
        return True
    if any(k in msg for k in ("401", "unauthorized", "invalid api key", "鉴权")):
        return True
    if any(k in msg for k in ("403", "forbidden", "禁止")):
        return True
    # 5) ValueError（空内容）/JSON 解析失败等业务异常：不切
    return False


class AIClient:
    """OpenAI 兼容 endpoint 池：round-robin 轮转 + 冷却 + 故障转移。

    单进程内状态（_current 索引、_cooldown 时间戳），不持久化。业务层重试循环
    由调用方保留，本类只负责"单次 chat_create 调用内遍历 endpoint 直到成功或全失败"。
    """

    def __init__(self, endpoints: list[Endpoint]):
        if not endpoints:
            raise ValueError("AIClient 需要至少一个 endpoint")
        if any(not valid_endpoint_url(ep.base_url) for ep in endpoints):
            raise ValueError('API URL requires HTTPS (or loopback HTTP), without credentials, query or fragment')
        self._secrets = tuple(ep.api_key for ep in endpoints)
        self._endpoints = endpoints
        self._current = 0  # round-robin 游标，记录下次起始 endpoint 索引
        self._cooldown: dict[str, float] = {}  # endpoint.id -> 冷却到期 monotonic 时间戳
        # 可调参数（环境变量）
        self._failover_on_timeout = os.environ.get("AI_FAILOVER_ON_TIMEOUT", "1") == "1"
        self._cooldown_429 = _env_float("AI_FAILOVER_COOLDOWN_429", 60)
        self._cooldown_timeout = _env_float("AI_FAILOVER_COOLDOWN_TIMEOUT", 30)
        self._cooldown_auth = _env_float("AI_FAILOVER_COOLDOWN_AUTH", 300)
        # AI_PRIMARY_ONLY=1：主从模式，始终优先主 endpoint（索引 0），仅当主失败/冷却
        # 才切备份，成功后不轮换；默认 0 = round-robin 均匀分摊。
        self._primary_only = os.environ.get("AI_PRIMARY_ONLY", "0") == "1"
        # AI_FAILOVER_ON_EMPTY=1（默认）：业务级失败（空内容/validate 抛异常）也切 endpoint；
        # =0 仅业务层重试不触发故障转移。单 endpoint 用户无下一个可切，行为与现状一致。
        self._failover_on_empty = os.environ.get("AI_FAILOVER_ON_EMPTY", "1") == "1"
        # AI_FAILOVER_COOLDOWN_EMPTY=0（默认）：业务级失败冷却秒数，0=不冷却。
        # 多 endpoint 主从模式建议设 30-60，避免主系统性业务级失败时每次重试都先撞主。
        self._cooldown_empty = _env_float("AI_FAILOVER_COOLDOWN_EMPTY", 0)
        # B10: enable_thinking 控制——AI_DISABLE_THINKING=1 强制不下发（优先级最高，
        # 规避 kimi 等网关 400）；AI_ENABLE_THINKING=1 强制下发；默认按模型名判定。
        self._thinking_force_on = os.environ.get("AI_ENABLE_THINKING") == "1"
        self._thinking_force_off = os.environ.get("AI_DISABLE_THINKING") == "1"
        # P2-3(0826): OpenAI client 按 endpoint.id 缓存复用——原实现每次 chat_create
        # 都新建 client（httpx 连接池反复握手）。per-request 超时经 create(timeout=)
        # 传入，缓存不影响超时语义；endpoint 配置进程内不变，缓存无失效问题。
        self._clients: dict = {}

    def _select(self, exclude: set[str]) -> tuple[int, Endpoint] | None:
        """找下一个未冷却、未排除的 endpoint。

        返回 (索引, endpoint)，遍历一圈无可用则 None。

        - round-robin 模式（默认）：从 _current 游标起轮转，成功后推进游标，
          实现同供应商多 key 均匀分摊配额。
        - 主从模式（AI_PRIMARY_ONLY=1）：始终从索引 0（主 endpoint）起按顺序找，
          优先用主，仅当主在 exclude 或冷却中才落到备份；不依赖 _current 游标。
        """
        now = time.monotonic()
        n = len(self._endpoints)
        start = 0 if self._primary_only else self._current
        for offset in range(n):
            idx = (start + offset) % n
            ep = self._endpoints[idx]
            if ep.id in exclude:
                continue
            if self._cooldown.get(ep.id, 0.0) > now:
                continue  # 仍在冷却
            return idx, ep
        return None

    def _mark_cooldown(self, ep: Endpoint, exc: Exception) -> None:
        """根据异常类型设置冷却时长。"""
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                PermissionDeniedError,
                RateLimitError,
            )
        except ImportError:
            return  # openai 未安装，无法判断类型，不设冷却
        if isinstance(exc, RateLimitError):
            self._cooldown[ep.id] = time.monotonic() + self._cooldown_429
        elif isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            self._cooldown[ep.id] = time.monotonic() + self._cooldown_auth
        elif isinstance(exc, (APITimeoutError, APIConnectionError)):
            self._cooldown[ep.id] = time.monotonic() + self._cooldown_timeout

    def chat_create(self, messages: list[dict], *,
                    max_tokens: int, temperature: float,
                    timeout: float = 120.0,
                    validate: Callable[[str], None] | None = None) -> str:
        """单次调用，内部故障转移。返回 content 字符串。

        遍历可用 endpoint：成功即返回；失败按类型决定是否切下一个。
        全失败时按失败级别分流（P2-0902）：业务级失败抛原始异常（业务层温度重试
        循环处理）；基础设施级失败（429/鉴权/超时等，已标记冷却）抛
        NoAvailableEndpointError（业务层立即降级）。成功后推进 _current 游标，
        保证同供应商多 key round-robin 均匀分摊配额。

        validate 回调：业务层可传一个内容校验函数（如 parse_json_object）。
        拿到非空 content 后调 validate(content)，不抛异常=合格返回；抛异常=业务级失败，
        按 AI_FAILOVER_ON_EMPTY 决定是否切下一个 endpoint（默认切）。这让"空内容/JSON
        解析失败"等业务级失败也能触发故障转移，而非只在业务层同 endpoint 反复试。
        """
        try:
            from openai import OpenAI
        except ImportError:
            print("⚠️  openai 库未安装，AI 调用不可用。pip install openai")
            raise

        last_exc: Exception | None = None
        last_exc_business = False  # P2-0902: last_exc 是否业务级失败（决定遍历完抛原异常还是 NoAvailableEndpointError）
        tried: set[str] = set()
        n = len(self._endpoints)
        while True:
            picked = self._select(exclude=tried)
            if picked is None:
                # P1-2: 全部 endpoint 都不可用（冷却或已试过）是系统性信号——本轮已遍历
                # 整个池子且无一个成功，重试只会继续空转。抛 NoAvailableEndpointError
                # 让业务层立即降级规则摘要（跳过冷却等待），避免 9 榜 × deadline 纯空转。
                # P2-0902: 业务级失败（空内容/validate 失败）例外——那是"本次内容不合格"
                # 而非"endpoint 不可用"，升级 NoAvailableEndpointError 会触发业务层
                # 立即降级、架空外层 3-5 次温度递增重试（单 endpoint 一次瞬时空响应
                # = 整批降级）。改抛原始异常，保留业务层重试。
                if last_exc is not None:
                    if last_exc_business:
                        raise ValueError(mask_secrets(last_exc, secrets=self._secrets)) from None
                    # Suppress the raw SDK cause; its traceback can echo credentials.
                    raise NoAvailableEndpointError(
                        f"无可用 AI endpoint（全部冷却或已试过，最后错误: {mask_secrets(last_exc, secrets=self._secrets)}）"
                    ) from None
                raise NoAvailableEndpointError("无可用 AI endpoint（全部在冷却中或未配置）")
            idx, ep = picked
            tried.add(ep.id)
            # phase 标志位区分异常来源：api 阶段（调用/空内容）vs validate 阶段（内容校验）
            phase = "api"
            try:
                # P2-3(0826): client 按 endpoint.id 缓存复用（原实现每次调用新建，
                # httpx 连接池反复握手）；超时语义由下方 create(timeout=) 保证
                client = self._clients.get(ep.id)
                if client is None:
                    client = OpenAI(api_key=ep.api_key, base_url=ep.base_url, timeout=timeout)
                    self._clients[ep.id] = client
                # reasoning 模型（GLM/DeepSeek-R1 等）默认把回答放 reasoning_content、
                # content 为空，且思考链会吃光 max_tokens 导致 finish_reason=length 无正文。
                # extra_body.enable_thinking=False 让 GLM 系跳过思考直接作答；经 extra_body
                # 传以绕过 openai SDK 的参数白名单。
                # B10: 默认仅对已知 reasoning 模型名下发，避免 kimi 等网关对未知参数 400；
                # AI_DISABLE_THINKING=1 强制不下发，AI_ENABLE_THINKING=1 强制下发。
                _extra_body = None
                if self._thinking_force_off:
                    _extra_body = None
                elif self._thinking_force_on or any(
                    h in (ep.model or "").lower() for h in _REASONING_MODEL_HINTS
                ):
                    _extra_body = {"enable_thinking": False}
                _create_kwargs = {"extra_body": _extra_body} if _extra_body else {}
                resp = client.chat.completions.create(
                    model=ep.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    **_create_kwargs,
                )
                msg = resp.choices[0].message
                content = msg.content
                # content 为空时回退读 reasoning_content（部分中转/模型即便 enable_thinking=False
                # 仍可能把正文放这里），双保险避免误判"空内容"触发故障转移。
                if (not content or not str(content).strip()) and getattr(msg, "reasoning_content", None):
                    content = msg.reasoning_content
                if not content or not str(content).strip():
                    raise ValueError("API 返回空内容")
                # validate 阶段：校验内容是否合格（如能否解析为 JSON）
                phase = "validate"
                if validate is not None:
                    validate(str(content).strip())
                # 成功：round-robin 模式推进游标到下一个，保证多 key 均匀分摊；
                # 主从模式不推进（保持主 endpoint 优先，下次仍先用主）。
                if not self._primary_only:
                    self._current = (idx + 1) % n
                return str(content).strip()
            except Exception as e:
                last_exc = e
                # 业务级失败：validate 阶段抛异常，或 api 阶段的空内容 ValueError
                business_fail = (phase == "validate") or (
                    isinstance(e, ValueError) and "API 返回空内容" in str(e)
                )
                last_exc_business = business_fail
                if business_fail and self._failover_on_empty:
                    if self._cooldown_empty > 0:
                        self._cooldown[ep.id] = time.monotonic() + self._cooldown_empty
                    # INFO: print 用纯 ASCII 前缀，避免未 reconfigure stdout 的上下文 GBK 编码崩溃
                    # N32: 异常文本经 mask_secrets 脱敏后再打印，防 key/URL 泄入日志
                    print(f"    [FAILOVER] endpoint#{ep.id} 故障转移(业务级): "
                          f"{type(e).__name__}: {mask_secrets(e, secrets=self._secrets)}")
                    continue  # 取下一个 endpoint
                if _should_failover(e, self._failover_on_timeout):
                    self._mark_cooldown(ep, e)
                    # N32: 基础设施异常（鉴权/连接）message 可能含完整 key 或带 key 的
                    # 请求 URL，打印前脱敏
                    print(f"    [FAILOVER] endpoint#{ep.id} 故障转移: "
                          f"{type(e).__name__}: {mask_secrets(e, secrets=self._secrets)}")
                    continue  # 取下一个 endpoint
                # 不该切换（如超时但开关关、或业务级失败但开关关）：直接抛回业务层
                error_type = ValueError if business_fail else RuntimeError
                raise error_type(mask_secrets(e, secrets=self._secrets)) from None
