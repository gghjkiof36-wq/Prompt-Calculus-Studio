# 開發說明

[貢獻指引](../CONTRIBUTING.md) · [文件索引](README.md)

## 環境

在 Windows 64 位元上使用 Python 3.12：

```powershell
py -3.12 setup_dependencies.py
py -3.12 run.py
```

測試時使用獨立資料目錄。不要把 `PROMPT_STUDIO_DATA` 指向日常使用的資料庫。網頁擴充測試另需 Node.js。

## 測試

在專案根目錄執行：

```powershell
py -3.12 -m unittest discover -s tests -p "test*.py"
node --test tests/comfy_binding.test.mjs
```

GUI 測試與 Windows 顯示效果仍需人工確認。以 `live_` 開頭的腳本涉及真實 ComfyUI，執行前閱讀腳本並準備獨立測試環境；不要把它們當成普通離線測試。

## 結構

- `prompt_studio/`：桌面程式、共用組合核心與資料存取。
- `comfyui_prompt_studio/`：ComfyUI 後端擴充與網頁介面。
- `tests/`：行為、整合與畫面測試。
- `docs/`：訪客使用文件；本頁提供開發入口。

## 建立 ComfyUI 擴充包

```powershell
New-Item -ItemType Directory -Force release
py -3.12 build_comfyui.py
```

輸出為 `release/PromptStudio-ComfyUI.zip`。建置會將共用核心一起加入，使用者安裝此包後不需為擴充安裝 Qt。

## 建立 Windows 程式

既有建置流程使用專案內的 PyInstaller：

```powershell
py -3.12 -m pip install --prefix .builder PyInstaller==6.22.2
py -3.12 build_windows.py --stage-only
```

候選輸出位於 `build/package/PromptStudio`。`--stage-only` 避免替換日常使用的封裝版；一般建置會更新 `release/PromptStudio`。

## 發布前

- 確認程式、文件與版本標籤對應同一組原始碼，完成必要測試。
- 從乾淨建置輸出準備附件，排除個人 `data`、模型、圖片、日誌與本機路徑設定。
- 隨包附上 LICENSE、操作說明、必要第三方聲明與對應原始碼。
- 確認 Windows 包包含 docs 及其圖片；ComfyUI 包需附 LICENSE，不能只依賴 README 中的連結。
- 用新資料夾測試解壓啟動、擴充安裝及文件連結。
- 再建立 Release，附版本摘要、檔案用途及已知限制。

目前建置腳本的文件打包清單仍需配合新版文件檢查；原始碼文件更新不代表舊封裝包已更新。

## 既有開發紀錄

根目錄的 `IMPLEMENTATION_NOTES.md`、`DESIGN.md`、`NEXT_UI.md`、`UI_UPDATE.md`、`TAG_AUTOCOMPLETE.md` 與 `QA_*.md` 保留歷史設計及測試脈絡。部分內容已被後續版本取代，請以對應版本程式與使用指南核對。
