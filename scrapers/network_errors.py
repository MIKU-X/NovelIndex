"""Recognize browser errors that repeated category requests cannot resolve."""


class NetworkUnavailable(RuntimeError):
    """Stop the platform when the browser cannot access the network."""


def network_failure(exc: Exception) -> NetworkUnavailable | None:
    message = str(exc)
    if "ERR_NETWORK_ACCESS_DENIED" in message:
        return NetworkUnavailable(
            "网络访问被拒（ERR_NETWORK_ACCESS_DENIED）；已停止当前平台抓取，"
            "请检查运行环境的网络权限、防火墙或代理策略后再运行"
        )
    if "ERR_INTERNET_DISCONNECTED" in message:
        return NetworkUnavailable(
            "网络断开（ERR_INTERNET_DISCONNECTED）；已停止当前平台抓取，请恢复网络后再运行"
        )
    return None
