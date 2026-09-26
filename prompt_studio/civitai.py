"""CivitAI asset lookup and downloads, independent of Qt.

The desktop UI owns all writes.  Network responses are normalized before they
reach the catalog so future CivitAI response additions do not become required
application fields.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import uuid
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.request import HTTPRedirectHandler, build_opener


API_BASE = "https://civitai.red/api/v1"
DOWNLOAD_HOSTS = {"civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red"}
PREVIEW_HOSTS = {"image.civitai.com", "imagecache.civitai.com"}
MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}
SOURCE_FIELDS=('civitai','civitai_status','civitai_model_id','civitai_version_id','base_model','creator','verification','source_type')
HASH_FIELDS=('sha256','hash_size','hash_mtime')


class CivitAIError(Exception):
    def __init__(self, message, *, status=None, kind="network"):
        super().__init__(message)
        self.status = status
        self.kind = kind


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _hashes(record):
    hashes = record.get("hashes", {}) if isinstance(record, dict) else {}
    return {str(k).upper(): str(v).upper() for k, v in hashes.items()
            if isinstance(k, str) and isinstance(v, str) and v}


def sha256_file(path, cancel=None, progress=None, expected=None):
    """Hash a model without loading it into memory and reject mid-read changes."""
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if expected and (before.st_size != expected.get("size") or before.st_mtime_ns != expected.get("mtime")):
        raise ValueError("模型自上次掃描後已變更，請重新掃描後再辨識。")
    digest = hashlib.sha256(); read = 0
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            if _cancelled(cancel):
                raise ValueError("已取消 CivitAI 辨識，先前模型資料保留。")
            digest.update(block); read += len(block)
            if progress: progress(read, before.st_size)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("模型在計算雜湊期間發生變更，未保存辨識結果。")
    return digest.hexdigest().upper()


def version_file(version, sha256=""):
    files = version.get("files", []) if isinstance(version, dict) else []
    files = [item for item in files if isinstance(item, dict)]
    wanted = sha256.upper()
    if wanted:
        match = next((item for item in files if _hashes(item).get("SHA256") == wanted), None)
        if match: return match
    return next((item for item in files if item.get("primary") is True), files[0] if files else {})


def normalize_version(version, sha256="", parent=None):
    """Return the stable subset used by Prompt Studio's local asset mapping."""
    if not isinstance(version, dict):
        raise CivitAIError("CivitAI 回傳的模型版本格式無效。", kind="response")
    parent = parent if isinstance(parent, dict) else {}
    model = version.get("model") if isinstance(version.get("model"), dict) else {}
    file = version_file(version, sha256)
    hashes = _hashes(file)
    ident = _integer(version.get("id"))
    model_id = _integer(version.get("modelId")) or _integer(parent.get("id"))
    if ident is None or model_id is None:
        raise CivitAIError("CivitAI 回傳缺少模型或版本 ID。", kind="response")
    trained = version.get("trainedWords", [])
    trained = list(dict.fromkeys(v.strip() for v in trained if isinstance(v, str) and v.strip())) if isinstance(trained, list) else []
    stats = version.get("stats") if isinstance(version.get("stats"), dict) else {}
    images = version.get("images") if isinstance(version.get("images"), list) else []
    previews = []
    for image in images[:20]:
        if not isinstance(image, dict) or not _text(image.get("url")): continue
        previews.append(dict(url=_text(image["url"]), width=_integer(image.get("width")),
                             height=_integer(image.get("height")), nsfw=image.get("nsfw"),
                             nsfw_level=image.get("nsfwLevel")))
    download = _text(file.get("downloadUrl")) or _text(version.get("downloadUrl"))
    creator = parent.get("creator") if isinstance(parent.get("creator"), dict) else {}
    tags = parent.get("tags") if isinstance(parent.get("tags"), list) else []
    parent_stats = parent.get("stats") if isinstance(parent.get("stats"), dict) else {}
    return dict(provider="civitai", model_id=model_id, version_id=ident,
                model_name=_text(parent.get("name")) or _text(model.get("name")) or _text(version.get("modelName")),
                version_name=_text(version.get("name")) or _text(version.get("versionName")),
                model_type=_text(parent.get("type")) or _text(model.get("type")), base_model=_text(version.get("baseModel")),
                base_model_type=_text(version.get("baseModelType")), air=_text(version.get("air")),
                trained_words=trained, sha256=hashes.get("SHA256") or sha256.upper(), hashes=hashes,
                published_at=_text(version.get('publishedAt')),
                file_id=_integer(file.get("id")), file_name=_text(file.get("name")),
                size_kb=file.get("sizeKB") if isinstance(file.get("sizeKB"), (int, float)) else None,
                download_url=download, url=f"https://civitai.red/models/{model_id}?modelVersionId={ident}",
                creator=_text(creator.get("username")) or _text(model.get("creator")),
                tags=list(dict.fromkeys(v.strip() for v in tags if isinstance(v,str) and v.strip())),
                availability=_text(version.get("availability")),
                stats={k: v for k, v in stats.items() if isinstance(k, str) and isinstance(v, (int, float))},
                model_stats={k: v for k, v in parent_stats.items() if isinstance(k,str) and isinstance(v,(int,float))},
                previews=previews)


class CivitAIClient:
    def __init__(self, token="", *, api_base=API_BASE, timeout=20, max_response=32 * 1024 * 1024,cancel=None):
        self.token = token.strip() if isinstance(token, str) else ""
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout; self.max_response = max_response
        self.cancel=cancel

    def _request(self, path, params=None, body=None, *, authenticated=True):
        if _cancelled(self.cancel): raise ValueError('已取消 CivitAI 連線。')
        url = self.api_base + "/" + path.lstrip("/")
        if params: url += "?" + urlencode(params, doseq=True)
        headers = {"Accept": "application/json", "User-Agent": "PromptStudio/0.81-Alpha"}
        if self.token and authenticated: headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8"); headers["Content-Type"] = "application/json"
        try:
            with _open_api(Request(url, data=data, headers=headers), timeout=self.timeout) as response:
                raw = response.read(self.max_response + 1)
                if _cancelled(self.cancel): raise ValueError('已取消 CivitAI 連線。')
                if len(raw) > self.max_response:
                    raise CivitAIError("CivitAI 回應過大，已停止讀取。", kind="response")
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            messages = {401: "CivitAI Token 無效或已失效。", 403: "目前帳號沒有存取這個資源的權限。",
                        404: "CivitAI 找不到相符資源。", 429: "CivitAI 暫時限制查詢頻率，請稍後再試。"}
            raise CivitAIError(messages.get(exc.code, f"CivitAI 回傳 HTTP {exc.code}。"),
                               status=exc.code, kind="http") from None
        except CivitAIError:
            raise
        except (URLError, TimeoutError, OSError):
            raise CivitAIError("目前無法連線 CivitAI；本地素材仍可照常使用。") from None
        except (UnicodeError, json.JSONDecodeError):
            raise CivitAIError("CivitAI 回傳了無法讀取的資料。", kind="response") from None

    def test_connection(self):
        if self.token:
            result = self._request("me")
            return dict(connected=True, authenticated=True, username=_text(result.get("username")) if isinstance(result, dict) else "")
        self._request("models", {"limit": 1, "types": "LORA"}, authenticated=False)
        return dict(connected=True, authenticated=False, username="")

    def version(self, version_id):
        if type(version_id) is not int or version_id <= 0: raise ValueError("CivitAI 版本 ID 無效。")
        return self._request(f"model-versions/{version_id}")

    def model(self, model_id):
        if type(model_id) is not int or model_id <= 0: raise ValueError("CivitAI 模型 ID 無效。")
        return self._request(f"models/{model_id}", authenticated=False)

    def models_by_ids(self, model_ids, cancel=None):
        values=list(dict.fromkeys(v for v in model_ids if type(v) is int and v>0)); found=[]
        for start in range(0,len(values),100):
            if _cancelled(cancel): raise ValueError("已取消 CivitAI 辨識，先前模型資料保留。")
            response=self._request("models",{"ids":','.join(str(v) for v in values[start:start+100]),"limit":100},authenticated=False)
            if not isinstance(response,dict) or not isinstance(response.get('items'),list):
                raise CivitAIError("CivitAI 模型資料格式無效。",kind="response")
            found.extend(v for v in response['items'] if isinstance(v,dict))
        return found

    def version_by_hash(self, sha256):
        value = str(sha256).upper()
        if not re.fullmatch(r"[0-9A-F]{64}", value): raise ValueError("SHA256 格式無效。")
        return self._request("model-versions/by-hash/" + value, authenticated=False)

    def versions_by_hash(self, hashes, cancel=None):
        values = list(dict.fromkeys(str(v).upper() for v in hashes))
        if any(not re.fullmatch(r"[0-9A-F]{64}", value) for value in values): raise ValueError("SHA256 格式無效。")
        found = []
        for start in range(0, len(values), 100):
            if _cancelled(cancel): raise ValueError("已取消 CivitAI 辨識，先前模型資料保留。")
            response = self._request("model-versions/by-hash", body=values[start:start + 100], authenticated=False)
            if not isinstance(response, list): raise CivitAIError("CivitAI 批次辨識格式無效。", kind="response")
            found.extend(item for item in response if isinstance(item, dict))
        return found

    def search_models(self, query="", *, types=("LORA",), base_models=(), sort="Most Downloaded",
                      period="AllTime", limit=20, cursor="", nsfw=False):
        if not 1 <= int(limit) <= 100: raise ValueError("CivitAI 每頁數量必須介於 1–100。")
        params = {"limit": int(limit), "sort": sort, "period": period, "nsfw": str(bool(nsfw)).lower()}
        if query.strip(): params["query"] = query.strip()
        if types: params["types"] = list(types)
        if base_models: params["baseModels"] = list(base_models)
        if cursor: params["cursor"] = cursor
        result = self._request("models", params)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise CivitAIError("CivitAI 搜尋結果格式無效。", kind="response")
        return result


def identify_models(rows, client, cancel=None, progress=None):
    """Hash scanned rows, perform batched lookup, and return enriched copies."""
    enriched = []
    for index, original in enumerate(rows):
        if _cancelled(cancel): raise ValueError("已取消 CivitAI 辨識，先前模型資料保留。")
        row = dict(original)
        for key in SOURCE_FIELDS: row.pop(key,None)
        current=Path(row['path']).stat()
        if (current.st_size,current.st_mtime_ns)!=(row['size'],row['mtime']):
            raise ValueError('模型自上次掃描後已變更，請重新掃描後再辨識。')
        reusable = row.get("sha256") and row.get("hash_size") == row.get("size") and row.get("hash_mtime") == row.get("mtime")
        if not reusable:
            row["sha256"] = sha256_file(row["path"], cancel, expected=row)
            row["hash_size"], row["hash_mtime"] = row["size"], row["mtime"]
        enriched.append(row)
        if progress: progress("hash", index + 1, len(rows))
    versions = client.versions_by_hash([row["sha256"] for row in enriched], cancel)
    parents={}
    if hasattr(client,'models_by_ids'):
        parents={item.get('id'):item for item in client.models_by_ids([v.get('modelId') for v in versions],cancel)
                 if type(item.get('id')) is int}
    by_hash = {}
    for version in versions:
        for file in version.get("files", []) if isinstance(version.get("files"), list) else []:
            digest = _hashes(file).get("SHA256") if isinstance(file, dict) else None
            if digest: by_hash[digest] = version
    for index, row in enumerate(enriched):
        if version := by_hash.get(row["sha256"]):
            row["civitai"] = normalize_version(version, row["sha256"],parents.get(version.get('modelId')))
            row.update(base_model=row["civitai"]["base_model"], creator=row["civitai"]["creator"],
                       civitai_model_id=row["civitai"]["model_id"], civitai_version_id=row["civitai"]["version_id"],verification='verified')
        row["civitai_status"] = "matched" if "civitai" in row else "not_found"
        if progress: progress("lookup", index + 1, len(enriched))
    return enriched


def safe_filename(value):
    name = Path(str(value)).name.strip().rstrip(". ")
    if not name or name in (".", "..") or any(c in name for c in '<>:"/\\|?*'):
        raise ValueError("CivitAI 檔名無效。")
    if Path(name).suffix.lower() not in MODEL_EXTENSIONS:
        raise ValueError("CivitAI 下載項目不是支援的模型格式。")
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('模型檔名使用了 Windows 保留名稱。')
    return name


class _SafeAuthRedirect(HTTPRedirectHandler):
    """Keep credentials on the original HTTPS origin for both API and files."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target=urlsplit(newurl); source=urlsplit(req.full_url)
        if target.scheme!='https':raise CivitAIError('重新導向未使用 HTTPS，已停止。',kind='response')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        origin=lambda u:(u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80))
        if redirected and origin(source)!=origin(target):
            redirected.remove_header('Authorization')
        return redirected


def _open_api(request, timeout):
    return build_opener(_SafeAuthRedirect()).open(request,timeout=timeout)


def _open_download(request, timeout):
    return _open_api(request,timeout)


def download_preview(url, data_directory, cancel=None):
    if _cancelled(cancel): raise ValueError('已取消載入預覽圖。')
    parts=urlsplit(str(url))
    if parts.scheme!='https' or parts.hostname not in PREVIEW_HOSTS: raise ValueError('CivitAI 預覽網址無效。')
    relative='civitai/previews/'+hashlib.sha256(str(url).encode()).hexdigest()+'.image'
    target=Path(data_directory)/relative
    if target.is_file():
        target.touch(); return relative
    target.parent.mkdir(parents=True,exist_ok=True); partial=target.with_suffix('.partial')
    try:
        with urlopen(Request(str(url),headers={'User-Agent':'PromptStudio/0.81-Alpha'}),timeout=30) as response,partial.open('wb') as output:
            total=0
            while block:=response.read(1024*1024):
                if _cancelled(cancel): raise ValueError('已取消載入預覽圖。')
                total+=len(block)
                if total>24*1024*1024: raise ValueError('CivitAI 預覽圖超過 24 MiB，已停止讀取。')
                output.write(block)
        if _cancelled(cancel): raise ValueError('已取消載入預覽圖。')
        os.replace(partial,target)
        # Download cache only. Asset thumbnails are copied into thumbnails/.
        cached=sorted(target.parent.glob('*.image'),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0
        for index,path in enumerate(cached):
            total+=path.stat().st_size
            if index>=100 or total>128*1024*1024:
                if path!=target: path.unlink(missing_ok=True)
        return relative
    finally:
        if partial.exists(): partial.unlink()


def preview_url(url,width=450):
    """Use the public image delivery variant; never fetch originals for list icons."""
    parts=urlsplit(str(url)); pieces=parts.path.split('/')
    if parts.scheme!='https' or parts.hostname not in PREVIEW_HOSTS:raise ValueError('CivitAI 預覽網址無效。')
    width=next((n for n in (96,320,450,512,800) if n>=width),800)
    if len(pieces)<4 or '=' not in pieces[-2]:return str(url)
    pieces[-2]=f'width={width},optimized=true,anim=false'
    from urllib.parse import quote
    pieces=[quote(p,safe='%=,+:@') for p in pieces]
    return urlunsplit((parts.scheme,parts.netloc,'/'.join(pieces),parts.query,''))


def download_version(version, target_directory, token="", cancel=None, progress=None, parent=None, *, file_id=None, filename=None):
    """Download one selected model file atomically and verify its SHA256."""
    if _cancelled(cancel): raise ValueError('已取消下載，未留下不完整模型。')
    if file_id is not None:
        selected=next((f for f in version.get('files',[]) if f.get('id')==file_id),None)
        if selected is None: raise ValueError('所選檔案不屬於目前版本。')
        version={**version,'files':[selected]}
    normalized = normalize_version(version,parent=parent)
    file = version_file(version)
    url = _text(file.get("downloadUrl")) or normalized["download_url"]
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in DOWNLOAD_HOSTS:
        raise ValueError("CivitAI 下載網址無效。")
    target_directory = Path(target_directory).resolve(strict=True)
    name = safe_filename(filename or file.get("name") or normalized["file_name"])
    target = target_directory / name
    if target.exists(): raise ValueError("目標已有同名模型，未覆寫。")
    partial = target_directory / (".prompt-studio-" + uuid.uuid4().hex + ".partial")
    headers = {"User-Agent": "PromptStudio/0.81-Alpha"}
    if token.strip(): headers["Authorization"] = "Bearer " + token.strip()
    digest = hashlib.sha256(); written = 0
    expected = _hashes(file).get("SHA256", "")
    try:
        with _open_download(Request(url, headers=headers),60) as response, partial.open("xb") as output:
            content_type=response.headers.get('Content-Type','').split(';')[0].lower()
            if content_type in ('text/html','application/json','text/plain'):
                raise CivitAIError('下載回應不是模型檔案，請檢查權限或稍後重試。',kind='response')
            total = response.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            while block := response.read(4 * 1024 * 1024):
                if _cancelled(cancel): raise ValueError("已取消下載，未留下不完整模型。")
                output.write(block); digest.update(block); written += len(block)
                if progress: progress(written, total)
        actual = digest.hexdigest().upper()
        if _cancelled(cancel): raise ValueError('已取消下載，未留下不完整模型。')
        if not written or (total is not None and written!=total): raise ValueError('下載內容不完整，未加入模型資產。')
        if expected and actual != expected:
            raise CivitAIError("官方 SHA256 校驗失敗，已移除下載檔案。",kind='checksum')
        if target.exists(): raise ValueError("目標已有同名模型，未覆寫。")
        os.rename(partial, target)
        verification='verified' if expected else 'unavailable'
        normalized.update(sha256=actual,official_sha256=expected,verification=verification)
        return dict(path=str(target), sha256=actual,official_sha256=expected,verification=verification,source=normalized)
    except HTTPError as exc:
        # Remote reason phrases/URLs must not enter the durable receipt or UI.
        # A download 401 alone cannot establish that the saved token is invalid.
        messages = {
            401: 'CivitAI 下載授權未通過（HTTP 401）。請確認 Token 與模型存取權限，並重新查詢版本後再試。',
            403: 'CivitAI 拒絕下載（HTTP 403）。請確認帳號是否有此模型的下載權限。',
            404: 'CivitAI 找不到下載檔案（HTTP 404）。請重新查詢模型版本。',
            429: 'CivitAI 暫時限制下載頻率（HTTP 429），請稍後再試。',
        }
        raise CivitAIError(messages.get(exc.code, f'CivitAI 下載回傳 HTTP {exc.code}。'),
                           status=exc.code, kind='http') from None
    except (URLError, TimeoutError):
        raise CivitAIError('CivitAI 下載連線失敗，請檢查連線或稍後再試。') from None
    finally:
        if partial.exists(): partial.unlink()


def site_url(url):
    parts=urlsplit(str(url))
    if parts.hostname in DOWNLOAD_HOSTS:return urlunsplit(('https','civitai.red',parts.path,parts.query,parts.fragment))
    return str(url)

def minor_marked(record):
    if record.get('minor') is True:return True
    names=[]
    for tag in record.get('tags') or []:
        names.append(tag.get('name','') if isinstance(tag,dict) else str(tag))
    names.append(str(record.get('name','')))
    return any(re.search(r'(?i)(?:^|[^a-z])(minor|underage|child|children|loli|shota)(?:$|[^a-z])',name) for name in names)

def visible_images(images,settings):
    result=[]
    for image in images:
        if not isinstance(image,dict) or image.get('type','image')!='image':continue
        if settings.get('civitai_filter_minor',True) and minor_marked(image):continue
        if not settings.get('civitai_mature',True):
            level=image.get('nsfwLevel'); rating=image.get('nsfw')
            if isinstance(level,int) and level>2:continue
            if rating is True or (isinstance(rating,str) and rating not in ('None','Soft','PG','PG-13')):continue
        result.append(image)
    return result

def filter_content(response,settings):
    result=copy.deepcopy(response); items=[]
    for record in result.get('items',[]):
        if not isinstance(record,dict):continue
        if settings.get('civitai_filter_minor',True) and minor_marked(record):continue
        for version in record.get('modelVersions') or []:
            if isinstance(version,dict):version['images']=visible_images(version.get('images',[]),settings)
        items.append(record)
    result['items']=items; return result
