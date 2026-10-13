# 開發方向

[回首頁](README.md) · 更新：2026-10-13（Asia/Taipei）

## 目前公開：0.8.7 Alpha 1

0.8.7 Alpha 1 於 2026-10-13 發布，提供主程式來源版、同版 ComfyUI 擴充與校驗檔。這版把素材管理與畫布編輯串在一起：

- **Prompt Editor**：搜尋、分類、釘選與編輯文字素材，支援 tags、一般文字及 Markdown；以一層資料夾整理分類。
- **Editor 與 Canvas 共用分類瀏覽**：兩頁同步展開／收合狀態，挑選素材加入畫布時建立獨立副本。
- **畫布編輯**：原位權重輸入、框選與多選、Prompt 來源排序，以及四個常用模組的新增頁與實際卡片預覽。
- **保存與切換**：保留編輯草稿與文字歷史，修正切換工作區後延遲排序可能改到其他工作區的問題。

[下載](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.7-alpha.1) · [發布說明](docs/releases/0.8.7/alpha.1.md) · [使用指南](docs/guide/README.md)

## 目前待改善

- 圖片來源卡的預覽尚無法點擊開啟原圖，目前可從檔案位置用圖片檢視器開啟。
- 窄視窗搭配大字級時，模組新增頁可能重疊，目前可放大視窗或降低字級。
- 新素材加入畫布仍需先在 Editor 保存，再到 Canvas 挑選；素材分類目前從分類及素材編輯入口管理，尚未提供跨分類拖放。

完整問題與處理方式見[本版已知問題](docs/releases/0.8.7/alpha.1.md#已知問題)及[疑難排解](docs/guide/troubleshooting.md)。

## 後續方向

關閉 ComfyUI 網頁後繼續執行、AI 轉譯、條件分支、循環、影音、跨服務派送及全局視覺重整仍屬後續方向。現在生成時需保持 ComfyUI 工作流網頁開啟。

更完整的觸發條件、歷史回溯與流程範本、進階 Canvas、區域生成與圖片編排、Manager 相容、局部控制及本機 AI 等中期方向，仍需逐項定義與驗收。

更遠期的社群整合、影片與 3D 流程屬探索。歡迎在 [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) 提供具體使用情境。

## 過往版本

0.8.1～0.8.6 的更新、下載及驗證紀錄集中在[版本總覽](docs/releases/README.md)；主要變更見[更新紀錄](CHANGELOG.md)。
