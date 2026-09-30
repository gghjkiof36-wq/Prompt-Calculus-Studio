# 更新紀錄

日期使用 Asia/Taipei（UTC+8）。公開可下載內容以對應 Release 為準；下列開發紀錄不等於同名版本全部發布。

## 0.831 Alpha 1｜2026-09-30

[本次Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.831-alpha.1)；產品／binary來源ea7df89。Stage生成入口、跨工作流圖片及分層預排程；修復種子生命週期、等待收尾、排程狀態與有界診斷。未接Stage只套用輸入，按執行才更新原生欄位。

原生loader永久pending仍需刷新；未知不重送。本次發布來源與同版擴充，Windows 執行檔暫不提供下載，待執行環境的散布材料補齊後另行處理。使用者人工驗收與EXE本機離屏自驗分列，見 [發布說明](docs/RELEASE_NOTES_0831.md) 及 [QA範圍](docs/validation/0831_STAGE.md)。舊版紀錄僅適用各自版本。

## 0.83 Alpha 1｜2026-09-28

[本次預覽版](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.83-alpha.1)，固定產品來源 `986da442f605512d30b9c3ef9005114a6a175825`；公開提交與附件以Release記錄為準。提供來源、配套擴充與校驗檔，無EXE／runtime。

- 未接預排程，每次點擊直接提交原生佇列；次數3提交3次，使用點擊當時的文字／圖片。
- 接預排程且空閒直接執行，忙碌才保存接入內容，完整完成後接續；未接入欄位派送時取值。
- 圖片來源、圖片輸入及整批供應，預排程最多十項並於完成後補入。
- 取消只針對指定PCS工作；PCS暫停、關閉或舊紀錄不鎖原生Run。
- 底欄精簡為次數、執行、取消、任務數；舊普通等待不重播，舊預排程保留紀錄。

使用者已回報更新後正常使用；開發自驗與人工回報分列於 [QA摘要](docs/validation/083_DIRECT.md)。關頁執行、跨工作流自動串接及AI文字轉譯不在本輪範圍。下列0.82及更早紀錄僅適用各自版本。

## 0.82 Alpha 1 Repair 5｜2026-09-27 公開 Alpha 預覽版

[Release 入口](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5)。固定產品來源 `5c621ecfdd4b30f8ade31dd6446dc9830871259a`；公開整合提交與附件由該頁追溯。本次提供來源、ComfyUI 擴充及校驗檔，不提供 EXE。

- 整合清單／Canvas、模型與圖片管理；CLIP 明確選擇 PCS 或 ComfyUI 手動文字。
- 原生提交前套用並核對綁定文字和 LoadImage，保留網頁未綁定參數。
- 整批並排／網格預覽，點圖只放大；最新完整批次優先於延後送達的舊縮圖。
- 未接線預覽清空，最近生成獨立保留；舊擴充協定不符時提示更新。
- 整理清單操作、工作流選擇與重連等既有修復；CivitAI 例外文字去敏不代表真實 401 已排除。
- 限制：網頁必開、單工作流每次一次；跨流程、多輪、無網頁背景及整批逐張下游尚未完成。

使用者已確認人工校驗通過，本輪依指示不新增產品驗證；不擴張為所有環境通過。見 [QA 摘要](docs/validation/082_REPAIR5.md)。

## 0.81 Alpha 2 UI Repair 1｜2026-09-14 04:07:49 UTC+8

[已公開預覽版](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1)（GitHub published_at：2026-09-13T20:07:49Z）。提供來源 ZIP、ComfyUI ZIP、SHA256SUMS.txt，無 Windows EXE。此版含 Canvas、CivitAI 搜尋／下載及 UI Repair 1 修正；下載與啟動以該頁說明為準。

## 2026-09-15：公開首頁維護

提交 [048a3ac](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/commit/048a3ac0a49e698db5ec85b80b31a9964e67fd10) 更新 PCS 品牌、0.81 下載入口並區分當時尚未公開的 Repair4。

## 早期公開紀錄

以下保留原公開 main 的記錄，原「未發布」僅描述当時文件整理狀態，不代表目前沒有 Release。


此檔記錄公開儲存庫的變更。過去 README 使用過 0.4.x、0.5 等開發編號，公開原始碼也包含其後的改動；這些編號目前沒有對應的 GitHub Release，不作為可下載版本承諾。

## 未發布：對外文件整理

- 重寫首頁、原始碼安裝與 ComfyUI 操作說明。
- 補上 AGPL-3.0-only 授權、貢獻、資料與隱私及問題排查文件。
- 補上公開程式的範例介面截圖。
- 分開說明公開功能與本機 Alpha 開發方向。
- 本次不修改應用程式、擴充或打包程式。

## 2026-09-12：首次公開原始碼

對應提交 [`f170c46`](https://github.com/gghjkiof36-wq/modular-prompt-manager/commit/f170c46e75acdf8e1dbd96e8d9680b7425ec103e)。

公開內容包含模組化 Prompt、輸出排序、手動稿、權重與排除 Tag、工作區、模型與圖片管理，以及 ComfyUI 側邊欄綁定、桌面控制、最近生成與模組快照。

這次公開提供原始碼，沒有發布安裝包。本機 Alpha 的後續功能不包含在此提交中，請見 [Roadmap](ROADMAP.md)。
