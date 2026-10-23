# 開發指南

[文件首頁](../README.md) · 參考基準：0.8.9 Beta2

本入口以公開來源 `5aa00c146281d96dd9d12f45ae020cfbe6e9575e` 為基準；其功能來源為 `6f03c1658dbea4ee89bed4b91c21f0c57a9dc12a`。後續開發先核對目標版本，歷史設計的階段狀態不代表目前功能狀態。

## 從需求找到接點

| 要做的事 | 先讀 |
| --- | --- |
| 新功能應放哪裡、如何保存與復原、背景結果寫回誰 | [架構設計參考](architecture-reference.md) |
| 新增選單、外框、表單或其他畫面，維持跨頁一致性 | [UI 元件與互動原則](ui-patterns.md) |
| 查文件交易、工作回執、服務封套與 Stage 來源共用細節 | [已實作的四項擴充接點](extension-boundaries-20261023.md) |
| 判斷 Beta2 已確認到哪裡 | [本版驗證與限制](../releases/0.8.9/validation/beta.2.md) |

## 開發環境

依[開始使用](../guide/getting-started.md)建立 `.venv`。開發與測試使用獨立資料位置：

```powershell
$env:PROMPT_STUDIO_DATA = Join-Path $PWD 'qa/dev-data'
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.2
```

## 架構與相容性

- [架構與資料流](architecture.md)（保留 0.8.6 說明；目前接點先讀上述參考）
- [本機文件與多視圖決策](local-workspace/ADR-001.md)（歷史決策；階段狀態按原日期解讀）
- [任務保存與執行歸屬](local-workspace/execution-owners.md)
- [Stage 模塊、接點與執行邊界](stage-architecture.md)
- [應用程式配色基準](application-color-baseline.md)
- [Canvas 外觀與共用元件基準](canvas-visual-baseline.md)（專屬操作仍適用，配色遷移狀態保留為歷史）
- [資料保存、參數與相容性](data-compatibility.md)
- [技術債與處理條件](technical-debt.md)
- [建置與來源包](building.md)
- [0.8.9 Beta2 功能與發布](../releases/0.8.9/beta.2.md)
- [AI 與 skill 接入契約](087-ai-skill-contracts.md)（規劃，未提供模型執行）

主程式與擴充原始碼分別位於 `prompt_calculus_studio/`、`comfyui_prompt_calculus_studio/`；`build_comfyui.py` 複製無 Qt 的共享核心建立擴充。版本與啟動旗標集中於 `prompt_calculus_studio/releases.py`。

## 執行檢查

從專案根目錄執行與改動相關的測試，例如：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_comfy_integration.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_maintenance_package.py'
node --test tests/comfy_binding.test.mjs
```

Node.js 需另外安裝。Qt 離屏檢查使用 `QT_QPA_PLATFORM=offscreen` 及獨立資料；原生輸入、視窗外框和實機 DPI 另走相應操作流程。`tests/` 保存可執行回歸與 fixtures；結果須記來源版本、環境與實際範圍，不能只從測試檔名推定適用版本。文件修改核對連結、來源與差異即可，無須因此重建產品。

## 隨功能維護

03 維護技術事實、選擇理由及改動範圍；07 維護文件組織、導航與版本對照；01 保留已確認的產品意圖與 UI 用途。這是維護分工，不增加每次改動的固定簽核流程。

內容交易、owner、非同步回覆、共享依賴或共用 UI 行為改變時，同次交付更新相關段落及受影響連結，註明基準、理由、驗證與限制。未影響的地圖不必重畫。Git 保存正式技術文件；交接入口只連結固定文件版本、進度與證據，不再複製一份長期規格。一般取捨直接記在相關段落；重大跨系統選擇才按需新增決策紀錄。

已淘汰提案與舊操作見[歷史資料](../archive/README.md)。
