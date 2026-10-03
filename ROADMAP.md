# 開發方向

[回首頁](README.md) · 更新：2026-10-03（Asia/Taipei）

## 已公開

- [0.8.1 Alpha 2 UI Repair 1](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.1-alpha.2.ui-repair.1)：來源、ComfyUI 擴充與校驗檔；Canvas、CivitAI 及該版介面修正。
- 早期 main 提供模組化提示詞、手動稿、素材管理與 ComfyUI 整合，保留公開提交紀錄。

## 已公開的0.8.2預覽版

0.8.2 Alpha 1 Repair 5 的產品來源固定為 `5c621ec`，使用者人工校驗已通過；公開狀態以 [Repair 5 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.2-alpha.1.repair.5) 為準。包括 Canvas／CLIP 綁定、明確文字來源、原生單工作流提交、圖片輸入及整批預覽。詳見 [更新紀錄](CHANGELOG.md) 與 [QA 範圍](docs/validation/082_REPAIR5.md)。

## 0.8.4 預覽版

Stage生成、線性跨工作流及本輪圖片傳遞、分層預排程、種子與流程恢復。固定ea7df89，發布以 [Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.4-alpha.1) 為準。操作見 [發布說明](docs/RELEASE_NOTES_084.md)。0.8.3的單工作流限制不直接套用本版。

## 0.8.5 預覽版

在 Stage 內編輯節點參數，讓每筆等待任務保存自己的設定，並改善參數面板、模型下拉及種子控制。操作見[Stage 參數指南](docs/STAGE_PARAMETERS.md)，下載及重要問題見[發布說明](docs/RELEASE_NOTES_085.md)。

## 本次 0.8.6 預覽版

共同導覽與配色、六節點起始流程、媒體檢視與匯出整理、Manager 套件管理、配套擴充安裝及單筆排程操作。用途與問題處理見[發布說明](docs/RELEASE_NOTES_086.md)。

## 仍未納入

關頁執行、AI轉譯、條件分支、循環、影音、跨服務派送及全局視覺重整。切換工作流後若一直載入，保存工作流並重新整理 ComfyUI 網頁；具體處理見[疑難排解](docs/TROUBLESHOOTING.md)。未列明的擴展沒有日期承諾。

## 中期與探索

Trigger／History／Recipe、進階 Canvas、區域生成與圖片編排、更完整的 Manager 相容、局部控制及本機 AI 等方向仍需逐項定義與驗收。品牌中的微積分是組織思路的隱喻，不保證生成品質或空間精準控制。

更遠期的社群整合、影片與 3D 流程屬探索，沒有在本版完成，也不承諾開源申請結果。歡迎在 [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) 提供具體使用情境。
