# 架構與資料流

更新：2026-09-14。維護起點為 0.8.1 Alpha 2，無新增大型依賴。

`run.py` 建立 Qt、資料鎖與 `Window`；`core.Storage` 負責 SQLite 文件載入／驗證／保存。`media.Catalog` 處理模型、圖片與來源索引。核心組合、工作流與來源規則維持純 Python，Qt 僅在介面、工作器與控制器層。

目前桌面載入使用 `Storage.load_current` → `state_loading.prepare_state`；JSON 匯入與快照恢復使用相同純資料轉換。原始 `Storage.load` 保留供唯讀檢查。手動稿入口與回呼身分界線見 `STATE_OPERATIONS.md`，相容分支見 `DATA_COMPATIBILITY.md`。

文字資料流：素材／選擇 → `composition`、`output_order` → `multi_output.compile_output` → 自動文字／手動稿 → `bound_texts` → 工作流快照。未綁定文字欄保留工作流原值，重複綁定由 `multi_output.bind` 拒絕。

畫布操作由 `TextCanvas.commit` 複製候選狀態、驗證後寫入，並記錄 undo／redo。`multi_output` 管理歸屬、連線、輸出選取與綁定；`multi_canvas` 和 `canvas_functions` 提供 Qt 呈現。

生成資料流：執行操作 → `generation_runner.GenerationRunner.run` → 凍結 graph、Prompt 與圖片快照 → 每次提交 → 以執行識別碼核對回呼 → `generation_jobs`／最近圖片。畫面刷新不得觸發 run；切换來源不能改寫已排隊快照。

CivitAI 資料流：設定輸入快照 → `civitai.CivitAIClient` → `normalize_version` → `civitai_assets` 下載計畫／收據 → 檔案校驗 → `Catalog` 登記來源。個人名稱、Notes、Trigger 與預覽覆寫保留。搜尋有獨立工作器，圖片使用最多三個 Qt 非同步請求，模型檔案使用檔案工作器。

保存與刷新由 `Window.changed(scope)` → `changes.ChangeCoordinator` 協調，保留 350 ms 延遲保存。同一事件循環的 Prompt 通知合併為一次文字刷新與一次 ComfyUI 同步；布局、模型資訊、一般設定和生成狀態不走 Prompt 同步。關閉網路立即停止圖片請求，關閉視窗清空待刷新工作。

布局交易仍即時更新幾何，讓內縮／擴大後的位置进入原有 undo 紀錄；`layout_only` 只排除外層位置、大小和文字面板比例，不排除畫布成員、連線或輸出解析度。未儲存位置的舊卡片由 `remember_layout_defaults` 保留首次拖動前的位置，復原無須重編文字。

ComfyUI 擴充只依賴打包的純 Python 共用模組，不匯入 PySide6。新增共用模組匯入時必須同步檢查 `build_comfyui.py` 清單。
