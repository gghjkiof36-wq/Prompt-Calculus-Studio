# 開發指南

[文件首頁](../README.md) · 適用：0.8.6 Alpha 1 主分支

依[開始使用](../guide/getting-started.md)建立 `.venv`。開發與測試使用獨立資料位置：

```powershell
$env:PROMPT_STUDIO_DATA = Join-Path $PWD 'qa/dev-data'
.\.venv\Scripts\python.exe run.py --v0.8.6-alpha
```

## 架構與相容性

- [架構與資料流](architecture.md)
- [Stage 模塊、接點與執行邊界](stage-architecture.md)
- [資料保存、參數與相容性](data-compatibility.md)
- [技術債與處理條件](technical-debt.md)
- [建置與來源包](building.md)

主程式與擴充原始碼分別位於 `prompt_calculus_studio/`、`comfyui_prompt_calculus_studio/`；`build_comfyui.py` 複製無 Qt 的共享核心建立擴充。版本與啟動旗標集中於 `prompt_calculus_studio/releases.py`。

## 執行檢查

從專案根目錄執行與改動相關的測試，例如：

```powershell
.\.venv\Scripts\python.exe tests/test_comfy_integration.py
.\.venv\Scripts\python.exe tests/test_maintenance_package.py
node --test tests/comfy_binding.test.mjs
```

Node.js 需另外安裝。`tests/` 保存可執行回歸與 fixtures；各版環境、結果和未驗項目見[版本紀錄](../releases/README.md)，同一份回歸可以沿用於後續版本。

已淘汰提案與舊操作見[歷史資料](../archive/README.md)。
