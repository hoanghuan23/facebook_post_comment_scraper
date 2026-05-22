import os
import requests
from dotenv import load_dotenv

load_dotenv()

STATIC_PORT_MIN = os.getenv('STATIC_PROXY_PORT_MIN', '').strip()
STATIC_PORT_MAX = os.getenv('STATIC_PROXY_PORT_MAX', '').strip()


def _replace_trailing_port(proxy_url: str, port: int) -> str:
    """Replace only the last :port part of a proxy URL if present."""
    head, sep, tail = proxy_url.rpartition(':')
    if not sep or not tail.isdigit():
        return proxy_url
    return f"{head}:{port}"


def _build_proxy_dict(proxy_url: str):
    return {'http': proxy_url, 'https': proxy_url}


def _configured_static_port_range():
    if not STATIC_PORT_MIN or not STATIC_PORT_MAX:
        return None
    try:
        return int(STATIC_PORT_MIN), int(STATIC_PORT_MAX)
    except ValueError:
        return None


def _proxy_url_from_dict(proxies):
    if not proxies:
        return ''
    return (proxies.get('https') or proxies.get('http') or '').strip()


def rotate_static_proxy():
    """
    Return the configured static proxy for cookie-based sessions.

    By default this does not rewrite the port. Some providers issue one exact
    host:port pair, and changing the port can turn a valid proxy into a dead one.
    If a provider explicitly supports rotating static ports, configure
    STATIC_PROXY_PORT_MIN and STATIC_PROXY_PORT_MAX in .env.

    Returns None if STATIC_PROXY (or fallback PROXY) is not configured.
    """
    proxy_base = os.getenv('STATIC_PROXY', '').strip() or os.getenv('PROXY', '').strip()
    if not proxy_base:
        return None

    port_range = _configured_static_port_range()
    if port_range:
        import random

        port = random.randint(port_range[0], port_range[1])
        proxy_url = _replace_trailing_port(proxy_base, port)
        print(f"  Static proxy rotated -> new port {port}  ({proxy_url})")
        return _build_proxy_dict(proxy_url)

    proxy_url = proxy_base
    print(f"  Static proxy retry -> keeping configured proxy ({proxy_url})")
    return _build_proxy_dict(proxy_url)


def rotate_proxy_for_retry(current_proxies=None, has_cookies=False):
    """
    Pick the retry proxy without switching proxy modes.

    has_cookies=False uses the rotating proxy endpoint as-is. With ProXoay-style
    local endpoints such as http://10.0.2.15:10000, the provider handles IP
    rotation behind that endpoint; changing to STATIC_PROXY here is wrong.

    has_cookies=True uses the static proxy path.
    """
    if has_cookies:
        return rotate_static_proxy()

    current_proxy = _proxy_url_from_dict(current_proxies)
    if current_proxy:
        print(f"  Rotating proxy retry -> keeping configured endpoint ({current_proxy})")
        return current_proxies

    rotating_proxy = os.getenv('ROTATING_PROXY', '').strip() or os.getenv('PROXY', '').strip()
    if not rotating_proxy:
        return None

    print(f"  Rotating proxy retry -> using configured endpoint ({rotating_proxy})")
    return _build_proxy_dict(rotating_proxy)


def is_proxy_infra_error(exc=None, status_code=None) -> bool:
    """
    True when the *proxy itself* is broken / unreachable / rejected the conn.
    HTTP 407 = proxy auth required (credentials wrong or expired)
    ProxyError / tunnel / connection refused = proxy host down or port dead
    """
    if status_code == 407:
        return True
    if exc is not None:
        if isinstance(exc, (requests.exceptions.ProxyError,
                            requests.exceptions.ConnectionError)):
            return True
        msg = str(exc).lower()
        if any(k in msg for k in ('proxy', '407', 'tunnel', 'connection refused',
                                   'cannot connect to proxy', 'eof occurred')):
            return True
    return False


def is_ip_blocked(status_code=None, response_text=None) -> bool:
    """
    True when Facebook itself rejected the request due to the outgoing IP.
    403 = IP banned / geo-blocked
    429 = rate-limited / too many requests from this IP
    503 = service unavailable (often a soft IP block or overload)
    Facebook sometimes also returns 200 with a checkpoint/login-wall body.
    """
    if status_code in (403, 429, 503):
        return True
    if response_text:
        txt = response_text[:500].lower()
        if any(k in txt for k in ('checkpoint', 'login_required',
                                   'you must log in', 'blocked')):
            return True
    return False


# Keep old name as alias so callers we haven't updated yet still work
is_proxy_error = is_proxy_infra_error


def select_proxy(has_cookies: bool):
    """
    Return a requests-compatible proxy dict, choosing the mode based on
    whether a cookie session is active.

    has_cookies=True  → static proxy from STATIC_PROXY (country can already be
                        embedded in username, e.g. __cr.fr), used as-is first.
                        Port is kept as configured unless STATIC_PROXY_PORT_MIN
                        and STATIC_PROXY_PORT_MAX are explicitly configured.
    has_cookies=False → rotating proxy from ROTATING_PROXY as-is.

    Backward compatibility:
    - If ROTATING_PROXY is missing, falls back to PROXY for rotating mode.
    - If STATIC_PROXY is missing, falls back to PROXY for static mode.
    """
    rotating_proxy = os.getenv('ROTATING_PROXY', '').strip() or os.getenv('PROXY', '').strip()
    static_proxy = os.getenv('STATIC_PROXY', '').strip() or os.getenv('PROXY', '').strip()

    if has_cookies:
        if not static_proxy:
            print("⚠️  No STATIC_PROXY configured — requests will be made without a proxy")
            return None

        proxy_url = static_proxy
        # print("🔒 Proxy mode : STATIC  (cookie-based session, fixed IP)")
        # print("   Initial port: using STATIC_PROXY as configured")
        print(f"   Proxy URL   : {proxy_url}")
        return _build_proxy_dict(proxy_url)

    if not rotating_proxy:
        print("⚠️  No PROXY configured — requests will be made without a proxy")
        return None

    print("🔄 Proxy mode : ROTATING  (no cookies, rotating IP per request)")
    print(f"   Proxy URL   : {rotating_proxy}")
    return _build_proxy_dict(rotating_proxy)
