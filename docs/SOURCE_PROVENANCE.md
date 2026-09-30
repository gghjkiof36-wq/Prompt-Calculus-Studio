# 0.831 Alpha 1 發布來源對照

產品固定 `ea7df89dc714fc83c7e11ca32e5c7655619ec8a0`，公開基準main `238aef37b3d2b4b02b73bcc73a182ee9883b2590`。本次整合固定產品樹與公開文件，保留既有公開歷史及Release/tag；沒有把全部本機祖先提交推到公開Git，本機產品SHA可能無法在公開倉庫解析。下載以 `v0.831-alpha.1` tag及附件BUILD_INFO／manifest為準。

- `prompt_studio/`、`comfyui_prompt_studio/`、根目錄執行／建置／部署腳本與固定產品逐位元組一致。`tests/`仅verify_v083.py將本機絕對資料路徑改為本checkout的qa/083/data，未改斷言或執行測試；其餘相同。
- 原交付source／ComfyUI ZIP的SHA256及375份來源換行正規化對Git核對一致。公開包重新收集Git blobs與公開文件，與原候選ZIP hash不同；不公開原候選啟動器／DELIVERY／驗證私有資料。
- 文件交付固定提交 `a4f8b33f22a6920a3497ee9b881cacb584a7c3ff` 的12份文件已整合。公開使用指南、驗收摘要與Release notes来自本版文件；發布狀態、建置及第三方實際稽核另由發布整合補齊。歷史候選的待驗／限制不當本版現況。
- 来源ZIP用原有 `package_documents.source_paths` Git白名單，根直接包含run.py。排除個人資料、模型、憑證、Git歷史、qa、vendor、第三方runtime及研究素材；歷史PNG截圖只在Git倉庫保留，不影響来源啟動。
- 擴充以 `comfyui_prompt_calculus_studio/` 為根，由固定擴充及 `releases.SHARED_MODULES` 的22個共用核心組成，附公開文件、相同來源ZIP與runtime來源映射。內部node/data/route識別沿用，不追溯改舊版。
- `SOURCE_MANIFEST.json`描述來源，`PACKAGE_MANIFEST.json`描述交付；`BUILD_INFO.json`分列公開git_head與固定product_git_head，擴充記內嵌source_sha256。包不附EXE/runtime，沒有binary建置聲明。
- 原本機Windows candidate的binary_git_head為ea7df89；EXE沒有由本次公開文件提交重建。原ZIP／EXE hash及58個實際runtime二進位、第三方材料缺口見[THIRD_PARTY_0831.md](THIRD_PARTY_0831.md)。Windows未作公開附件，待來源／通知／重建替換核對補齊；本機可用EXE不代表全部公開散布材料齊備。

程式標題保留固定產品「v0.831 種子與流程恢復修復候選（0930）」；公开Alpha prerelease不改程式版本字串。使用者功能驗收、本機離屏EXE啟動、228 Python／263 JS與三項流程複驗分列；08獨立檢查及本次來源／包／上傳核對不擴寫成真GPU、新機、全情境或全部升級／回退通過。操作與未驗範圍見[QA摘要](validation/0831_STAGE.md)。

舊0.83來源及公開提交、附件SHA仍由舊tag／Release和歷史SOURCE_PROVENANCE可追溯；不覆蓋舊包或標籤。
