# CivitAI 欄位來源與限制（0.8.1 Alpha）

查證日期：2026-09-13。以下依 CivitAI 官方公開程式及本輪未登入的真實回應；來源可能調整，介面保留缺值處理。

| 顯示 | 使用的回應資料 | 範圍／缺值 |
| --- | --- | --- |
| Type | 搜尋模型 `type`；版本詳情 `model.type` | 來源類型，與本機類型分開。 |
| 作者 | 搜尋模型 `creator.username` | 模型層級，未提供則留空提示。 |
| Stats | 所選版本 `stats.downloadCount`、存在時的 `generationCount` | 標示所選版本；不從模型總計補值。 |
| Reviews | 所選版本 `stats.rating/ratingCount/thumbsUpCount/thumbsDownCount` 中實際存在的數值 | 各自標示意義；不存在的評價不推算。 |
| Published | 所選版本 `publishedAt` | 不以 `createdAt` 代替。 |
| Base Model | 所選版本 `baseModel` | 保存及篩選，不自動建子目錄。 |
| Usage Tips | 本次核對的公開版本回應沒有提供 strength 建議 | 顯示未提供，不讀 HTML 描述推測。 |
| Hash | 所選 `files[]` 的 `hashes` | 附檔名；下載只核對該檔案的官方 SHA256。 |
| Trigger Words | 所選版本 `trainedWords` | 保存來源副本，與個人 Trigger 分開。 |
| AIR | 版本詳情 `air` | 直接使用來源值，不自行組字串。 |
| 安裝狀態 | 本機資產中的來源版本／檔案 ID，配合實際檔案大小及修改時間 | 不依模型名稱判斷；需要完整重新校驗時重算 SHA256。 |

搜尋使用 `metadata.nextCursor` 載入下一批；不直接跟隨任意 nextPage 網址。列表只讀取可見的 96px 圖片，選取模型後讀取 450px 預覽。沿用官方圖片服務的尺寸參數，快取限 100 張／128 MiB。

本次真實測試使用公開版本 9208、檔案 8955（約 24 KB），完成下載、官方 SHA256 核對及本機資產登記；未使用 Token、未載入模型權重。測試資料及來源欄位清單見交付驗收紀錄。

來源：[搜尋接口](https://github.com/civitai/civitai/blob/main/src/pages/api/v1/models/index.ts)、[版本詳情接口](https://github.com/civitai/civitai/blob/main/src/pages/api/v1/model-versions/%5Bid%5D.ts)、[圖片尺寸參數](https://github.com/civitai/civitai/blob/main/src/client-utils/edge-url.ts)。

真實登入、受限模型與完整大檔下載仍需另外驗證。網站統計與作者資料可能缺少或隱藏，缺值不代表零。
