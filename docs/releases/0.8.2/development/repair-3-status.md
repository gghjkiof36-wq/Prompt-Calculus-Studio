# 0.8.2 Alpha 1 Repair 3：即時文字、網頁圖片與清單操作

2026-09-15，基於 3af99f0。已綁定文字改為從目前桌面保存狀態同步，無須先執行；已排隊任務與歷史仍保留凍結內容。ComfyUI 1.43.18 圖片透過原生 executed 事件更新顯示 store。Prompt 輸出清單支援 Del／Backspace 與右鍵刪除目前項目，右鍵座標包含 GraphicsView 縮放與平移。實作 bd65c33、7a1dd84，詳見 [082_REPAIR3.md](../repairs/repair-3.md)。

本組 81 項 Python、27 項 JavaScript 回歸通過；已讀取現機靜態前端並離線執行其圖片事件 handler。桌面／擴充包與 EXE 離屏證據見 build/082_DELIVERY.json。未安裝到使用者 ComfyUI、操作可見桌面或執行 GPU；實機網頁顯示、多 DPI 與獨立 Review／QA 仍待確認。主倉庫未合併。
