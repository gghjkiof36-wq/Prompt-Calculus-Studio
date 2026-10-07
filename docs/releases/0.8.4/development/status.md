# 0.8.4 Alpha 1 發布狀態（2026-09-30）

功能基準 `ea7df89dc714fc83c7e11ca32e5c7655619ec8a0`（本次公開來源另含命名校正，見來源對照），Stage為PCS生成入口，支援明確線性Stage流程與本次圖片結果傳遞、資料及Stage分層預排程。沒有有效Stage接入時只套用輸入；修改文字不即時覆盖原生欄位。PCS、ComfyUI服務和原生網頁均須保持開啟。

使用者最後回報正常通過；開發固定來源228項Python／Qt、263項JavaScript與三項流程複驗另列。Windows完成本機離屏全新啟動／重開／搬移及三工作區副本檢查；未將此擴成使用者EXE、新機或GPU全面驗收。原建置DELIVERY與後續使用者驗收記錄分開保留。

偶發停滯事件缺少階段證據，唯一根因未證實；已處理可重現等待並補有界診斷，未知提交不自動重送。原生載入永久不返回或第三方入口隔離不明時須重新整理；自由下游套用失敗保留結果但没有專用重試套用入口。操作與界線見[Stage說明](stage.md)、[QA摘要](../validation/alpha.1.md)。

公開交付本次來源與擴充，Windows第三方材料未齐、暂不公開，見[散布核對](../third-party.md)。本輪06只做來源／套件／敏感內容／上傳完整性，08獨立安全檢查另記；沒有重跑所有產品或真GPU。來源對照見[SOURCE_PROVENANCE.md](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/blob/v0.8.4-alpha.1/docs/SOURCE_PROVENANCE.md)。
