"""Read a few public tag records to check the proposed autocomplete integration.

This is a feasibility probe, not a dictionary downloader or a desktop app.
Only Python's standard library is needed. It makes at most five small requests,
without authentication, image downloads, or disabled TLS verification.
"""

import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = "https://danbooru.donmai.us"
TAG_FIELDS = "id,name,category,post_count,is_deprecated"


def get_records(path, params):
    url = f"{BASE}{path}?{urlencode(params)}"
    request = Request(url, headers={
        "User-Agent": "PromptStudio-Feasibility/0.1",
        "Accept": "application/json",
    })
    with urlopen(request, timeout=15) as response:
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("Unexpectedly large response; no further data read.")
        records = json.loads(body)
        if not isinstance(records, list):
            raise ValueError("The response was not a record list.")
        return {"url": url, "status": response.status, "records": records}


def main():
    results = {"purpose": "small read-only feasibility probe", "results": []}
    checks = [
        ("simple_prefix", "/tags.json", {"search[name_matches]": "simple*", "search[order]": "count", "limit": 5, "only": TAG_FIELDS}),
        ("ap_prefix", "/tags.json", {"search[name_matches]": "ap*", "search[order]": "count", "limit": 5, "only": TAG_FIELDS}),
        ("artist_prefix", "/tags.json", {"search[name_matches]": "ap*", "search[category]": 1, "search[order]": "count", "limit": 5, "only": TAG_FIELDS}),
        ("active_aliases", "/tag_aliases.json", {"search[status]": "active", "limit": 3, "only": "id,antecedent_name,consequent_name,status"}),
        ("tag_page", "/tags.json", {"search[order]": "date", "limit": 3, "only": TAG_FIELDS}),
    ]
    for index, (name, path, params) in enumerate(checks):
        if index:
            time.sleep(1.1)
        try:
            result = {"check": name, **get_records(path, params)}
            results["results"].append(result)
            print(json.dumps(result, ensure_ascii=True), flush=True)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            result = {"check": name, "error_type": type(exc).__name__, "error": str(exc)}
            results["results"].append(result)
            print(json.dumps(result, ensure_ascii=True), flush=True)
            break
    output = Path(__file__).with_name("danbooru-probe-result.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
