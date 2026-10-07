"""Tiny HTTP helper built on the standard library, with retries."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "job-digest/1.0 (personal daily job alert)"


def get(url: str, params: dict | None = None, headers: dict | None = None,
        timeout: int = 30, retries: int = 3) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    req_headers.update(headers or {})
    req = urllib.request.Request(url, headers=req_headers)

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # Retry only on rate limits and server errors.
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")


def get_json(url: str, params: dict | None = None, headers: dict | None = None,
             **kwargs) -> dict:
    return json.loads(get(url, params, headers, **kwargs))
