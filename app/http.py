"""HTTP session shared by directory discovery and streamed downloads."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_session(ca_bundle: str | None = None) -> requests.Session:
    """Retry transient HTTP failures and keep HTTPS certificate checks enabled."""
    session = requests.Session()
    session.headers["User-Agent"] = "MOEX-Data-Collector/0.1.0"
    session.verify = ca_bundle if ca_bundle is not None else True
    retries = Retry(
        total=3, connect=3, read=0, status=3, backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    return session
