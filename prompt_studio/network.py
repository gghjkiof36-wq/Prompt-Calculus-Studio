"""Small, read-only lookup adapters. No image downloads or whole-dictionary sync."""
import html
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .core import contains_chinese


class LookupError(Exception):
    pass


def request_json(url, body=None):
    headers = {"User-Agent":"PromptStudio/0.2", "Accept":"application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise LookupError("回應過大，已停止讀取。")
            return json.loads(raw)
    except HTTPError as exc:
        if exc.code == 429:
            raise LookupError("服務暫時限流，稍後再查；既有內容不受影響。") from None
        raise LookupError(f"服務回傳 HTTP {exc.code}，請檢查連線或服務設定。") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise LookupError("目前無法完成連線，請稍後再試；仍可手動輸入。") from None


def danbooru(query, artist=False):
    if contains_chinese(query) and not artist:
        params = {"search[type]":"tag", "search[query]":query, "limit":12}
        path = "/autocomplete.json"
    else:
        name = query.casefold().replace(" ", "_").replace("*", "\\*") + "*"
        params = {"search[name_matches]":name,"search[order]":"count","search[is_deprecated]":"false",
                  "limit":12,"only":"id,name,category,post_count"}
        if artist:
            params["search[category]"] = 1
        path = "/tags.json"
    records = request_json("https://danbooru.donmai.us"+path+"?"+urlencode(params))
    if not isinstance(records, list):
        raise LookupError("標籤服務回傳了未知格式。")
    result = []
    for record in records:
        value = record.get("name") or record.get("value")
        if isinstance(value,str) and value:
            result.append(dict(value=value,category=record.get("category",0),
                               count=record.get("post_count",0),source="Danbooru"))
    return result


def google_translate(query, api_key):
    if not api_key:
        raise LookupError("Google Cloud 尚未設定金鑰；可繼續使用中文字典及 Danbooru。")
    # Key is never logged or saved with application state/cache keys.
    result = request_json("https://translation.googleapis.com/language/translate/v2?"+urlencode({"key":api_key}),
                          {"q":query,"target":"en","format":"text"})
    try:
        value = html.unescape(result["data"]["translations"][0]["translatedText"])
        if not isinstance(value,str) or not value.strip():
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError):
        raise LookupError("翻譯服務沒有回傳可用文字。") from None
    return [dict(value=value,category=None,count=0,source="Google 翻譯")]
