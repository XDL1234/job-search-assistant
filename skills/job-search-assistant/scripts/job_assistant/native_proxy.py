"""只供 Native 启动 Codex 子进程时读取当前用户代理，不写入应用数据。"""
import os
import urllib.request
from urllib.parse import urlsplit


def _proxy_additions(environ, discovered):
    configured = {key.upper() for key in environ}
    additions = {}
    # ALL_PROXY 的显式空值同样表示调用方已作选择，不用系统代理覆盖。
    if 'ALL_PROXY' not in configured:
        for kind in ('http', 'https'):
            name = kind.upper() + '_PROXY'
            value = discovered.get(kind)
            if name in configured or not isinstance(value, str) or not value:
                continue
            if len(value) > 8192 or any(char in value for char in '\r\n\0'):
                continue
            try:
                parsed = urlsplit(value)
                valid = (parsed.scheme in ('http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h')
                         and bool(parsed.hostname) and parsed.port != 0)
            except ValueError:
                valid = False
            if valid:
                additions[name] = value
    bypass = discovered.get('no')
    if ('NO_PROXY' not in configured and isinstance(bypass, str) and bypass
            and len(bypass) <= 8192 and not any(char in bypass for char in '\r\n\0')):
        additions['NO_PROXY'] = bypass
    return additions


def codex_proxy_environment():
    # Windows getproxies() 会被仅有 NO_PROXY 的环境短路，直接复用 stdlib
    # 的当前用户注册表读取器，按协议填补缺项；不解析 PAC、不发网络请求。
    reader = getattr(urllib.request, 'getproxies_registry', urllib.request.getproxies)
    return _proxy_additions(os.environ, reader())
