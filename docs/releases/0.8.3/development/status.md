# 0.8.3 Alpha 1 發布狀態（2026-09-28）

固定產品來源 `986da442f605512d30b9c3ef9005114a6a175825`。未接預排程時，每次點擊直接提交 ComfyUI 原生佇列；接上預排程時，空閒立即執行，忙碌才保存接入內容。PCS、服務與網頁須保持開啟，不支援關頁執行或跨工作流自動串接。底欄保留次數、執行、取消與任務數。

使用者已回報正常使用。既有開發檢查150項Python／Qt與75項JavaScript通過；另有16項選定離線／offscreen檢查通過，不合併加總。靜態抽查留下兩項尚未動態重現的條件風險：原生提交持續不返回時Run可能等待；較早圖片失敗後再次點擊可能重複選圖。留後續版本處理，詳見[QA摘要](../validation/alpha.1.md)。

本次提供來源、ComfyUI擴充和校驗檔，沒有EXE或第三方runtime。發布只核對來源、包、敏感內容排除與上傳完整性，沒有新增GPU、安裝或全套產品驗收。使用方法見[入門指南](../../../guide/getting-started.md)，來源及包對照見[SOURCE_PROVENANCE.md](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/blob/v0.8.3-alpha.1/docs/SOURCE_PROVENANCE.md)。
