# 開發方向

[回首頁](README.md) · 更新：2026-09-27（Asia/Taipei）

## 已公開

- [0.81 Alpha 2 UI Repair 1](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1)：來源、ComfyUI 擴充與校驗檔；Canvas、CivitAI 及該版介面修正。
- 早期 main 提供模組化提示詞、手動稿、素材管理與 ComfyUI 整合，保留公開提交紀錄。

## 本次公開預覽版

0.82 Alpha 1 Repair 5 的產品來源固定為 `5c621ec`，使用者人工校驗已通過；公開狀態以 [Repair 5 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5) 為準。包括 Canvas／CLIP 綁定、明確文字來源、原生單工作流提交、圖片輸入及整批預覽。詳見 [更新紀錄](CHANGELOG.md) 與 [QA 範圍](docs/validation/082_REPAIR5.md)。

## 尚未完成，沒有發布日期承諾

- 跨工作流執行、多輪及關閉網頁後可靠背景提交。
- 多張圖片按原輸出順序逐張完成下游處理，並能選擇單張。
- 純圖片且沒有有效 CLIP 綁定的執行路徑。
- 更完整的工作流、節點、Windows 環境與新機安裝相容性證據。
- 對應本版且可公開的截圖、實際 GitHub CI 紀錄及 EXE 散布準備。

## 中期與探索

Trigger／History／Recipe、進階 Canvas、區域生成與圖片編排、Manager 整合、局部控制及本機 AI 等方向仍需逐項定義與驗收。品牌中的微積分是組織思路的隱喻，不保證生成品質或空間精準控制。

更遠期的社群整合、影片與 3D 流程屬探索，沒有在本版完成，也不承諾開源申請結果。歡迎在 [Issues](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues) 提供具體使用情境。
