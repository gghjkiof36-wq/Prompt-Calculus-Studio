# 架構與資料流

適用：0.8.6 Alpha 1 主分支。主程式套件是 `prompt_calculus_studio`，擴充原始碼是 `comfyui_prompt_calculus_studio`。

## 資料與畫布

`run.py` 建立 Qt、資料鎖與 `Window`。`core.Storage` 管理 SQLite 文件；`Storage.load_current` 經 `state_loading.prepare_state` 準備載入副本。JSON 匯入和快照恢復共用載入邊界。模型與圖片索引由 `media.Catalog` 管理。

素材／選擇經 `composition`、`output_order` 與 `multi_output.compile_output` 編成文字；手動稿獨立保存。`TextCanvas.commit` 驗證候選狀態並建立 Undo／Redo；畫面刷新不提交生成。保存與同步由 `Window.changed` 和 `changes.ChangeCoordinator` 協調。

## Stage 與 ComfyUI

現行生成入口由 Stage 安排。`stage_model` 編譯線性流程，`stage_context` 提供本輪輸入與結果上下文，`stage_store` 保存執行紀錄，`stage_runner.StageRunner` 派送並核對回執；既有 `generation_runner`／`native_submission` 負責原生提交與完成追蹤。沒有有效 Stage 接入時只套用輸入。

Canvas 配置、`stage_journal` 與圖片快照各有責任；恢復配置不建立待送工作。詳細規則見 [Stage 架構](stage-architecture.md)與[資料相容性](data-compatibility.md)。

ComfyUI 擴充提供原生網頁接入、佇列與結果查詢。API、節點識別與保存欄位保留既有 `prompt_studio` 名稱，來源套件改名不遷移使用者資料。`releases.py:SHARED_MODULES` 列出打包共用核心，共享模組可在無 Qt 環境匯入與處理資料；桌面圖片光柵化於呼叫時載入 Qt，擴充服務不使用該分支。

## 外部資產

CivitAI 查詢經 `civitai` 正規化，再由 `civitai_assets` 建立下載計畫與收據，校驗後交 `Catalog` 登記。使用者的名稱、備註、觸發詞與預覽覆寫獨立保存。

0.8.1 維護起點的詳細歷程見[歷史架構紀錄](../archive/0.8.1/architecture-2026-09-14.md)。
