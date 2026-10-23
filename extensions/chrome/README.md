# PCS 對話轉移

Chrome 116 以上的 Manifest V3 頁內擴充，直接載入本目錄即可使用，不需 npm 或建置工具。桌面接收端由同一交付包的 PCS 連接器提供。

## 載入與使用

1. 在 Chrome 的「擴充功能」開啟開發人員模式，選「載入未封裝項目」，選擇這個 `chrome` 目錄。
2. 開啟 ChatGPT 對話，在右上角「更多／分享」左側的「PCS」選擇「選取全部」或「選取訊息」。選取模式下，點訊息內容或右側勾選框即可選取；再點一次取消。選好後從右上 PCS 選單按「儲存到 PCS」或下載。
3. 只處理一則訊息時，也可點該則原生複製／分享列旁的「PCS」。移至或點選「儲存到 PCS」，在展開的子選單點目的資料夾即可儲存。沒有子資料夾時，點「儲存到 PCS」即可存至目前位置。完成後可開啟文件、顯示資料夾，或在「最近保存」查找。

選單的「下載 JSON」及「下載 Markdown」可直接取得檔案；JSON 使用 PCS Capture v1，能交給同版 PCS 的檔案匯入功能。傳送到桌面需先安裝交付包的 PCS 連接器並開啟支援此功能的 PCS。

## 實作與資料範圍

- `capture.mjs` 在使用者進入選取或按重新讀取時，讀目前 ChatGPT 頁面已載入的 user/assistant 訊息。不讀其他分頁、不找最近對話、不呼叫網站內部 API、不讀 cookies 或網站儲存空間。
- 已載入範圍是目前 DOM 的觀測結果，不宣稱完整歷史或完整分支。換分頁、導航或傳送前比對發現內容變動時要求重新讀取；串流中不接受快照。
- JSON 保存選定訊息的文字與來源資訊，Markdown 是相同選定內容的可讀投影。可見的程式碼、標題、列表、GFM 表格、資料卡、可存取指標數值及 KaTeX 公式做結構轉換；普通引用連結保留。普通按鈕及有明示標記的工具活動／推理區不納入正文。
- 圖片、圖表、附件及嵌入內容使用選定訊息下的可選 `resources:[{kind,label,url}]`，每訊息最多 32 項、合計 128 項。`kind` 是 `citation/image/chart/attachment/embed`，`label` 最多 300 Unicode 碼點，`url` 是安全 http(s) 參照或 `null`。Markdown 下載將資產投影一次為具名連結；無可用外部 URL 時連到原對話。Canvas、iframe 及圖形未提供可讀值時不編造資料、不擷取 hover 狀態，不下載附件實體或任意 fetch 簽名 URL。帶存取權杖、credentials、明顯私有位址的資產 URL 置空。
- 站方 message ID 優先；缺少時使用 `capture-local-<index>-<content digest>`，不是冒充站方原始 ID。未選訊息正文不進 Native Messaging 或下載檔。
- 本機儲存僅保留最近 10 次操作的標題、instance ID、operation ID、狀態與保存路徑，供查詢與開啟文件；不持久保存對話正文。最近操作記錄可隨移除擴充清除。

## 桌面協定

固定擴充 ID：`migmpicacfjoipmfcgdjocgdbplgldan`。Native host：`com.promptcalculus.studio`。`manifest.json` 的 `key` 是公開 SPKI 資料，讓未封裝擴充 ID 固定；不是認證金鑰，也不能單獨證明套件來源。

請求使用 `command` 和唯一 `request_id`；保存操作帶 `instance_id` 與 `operation_id`。先 `hello` 取得 PCS 視窗及 `save_destination_v1` 能力，單一視窗直接使用，多視窗由使用者選擇。`destinations` 取得已接入位置及其子資料夾；目錄以服務端發出的 opaque ID 識別。點擊儲存目的地後才傳送正文，滑鼠移入只展開清單；`begin` 同時帶選定的 `destination_id`。傳輸為 `begin` → 順序 `chunk` → `finish`，16 MiB UTF-8 上限、每塊 192 KiB 原始 bytes、整體 SHA-256。

`begin` 固定本次目錄身分，`finish` 驗證來源後排程既有 PCS writer，無須切到桌面再確認。選單自動查詢同一操作的 `status`，只有 `committed` 顯示已儲存；完成不自動開文件或整理畫面。失聯不重送原操作，可查詢既有結果。`cancel`、`open`、`reveal` 均由桌面依自身操作回執處理，擴充不能要求開啟任意路徑。舊版客戶端未帶目的地時，PCS 保留小型位置確認視窗，沒有整篇對話預覽或完成後整理入口。

## 參考來源與授權

這份擴充是 PCS 專案原創實作，依專案根目錄 `LICENSE`（AGPL-3.0）提供。沒有打包、執行或移植以下專案的第三方程式碼。

- [pionxzh/chatgpt-exporter，固定 commit 8404ad9](https://github.com/pionxzh/chatgpt-exporter/tree/8404ad9c6fbee732b2c37759550fd3f02d1543f7)（[MIT](https://github.com/pionxzh/chatgpt-exporter/blob/8404ad9c6fbee732b2c37759550fd3f02d1543f7/LICENSE)）：參考原件與 Markdown 投影分離、[目前分支路徑](https://github.com/pionxzh/chatgpt-exporter/blob/8404ad9c6fbee732b2c37759550fd3f02d1543f7/src/api.ts#L985-L1140)、程式碼／公式／表格保真測例。本擴充不沿用其最近對話 fallback、網站 session 或內部 endpoint。
- [Chrome Native Messaging sample，固定 commit 1bdfa5a](https://github.com/GoogleChrome/chrome-extensions-samples/tree/1bdfa5a730396cc4615d1e32d7c7152a92a6babe/api-samples/nativeMessaging)：只參考 `connectNative` port、回覆和斷線概念；root LICENSE 為 Apache-2.0，舊 sample 檔頭標 BSD-style，未複用該原碼。
- 官方 [Content Scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)、[Scripting](https://developer.chrome.com/docs/extensions/reference/api/scripting)、[Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) 文件。

## 有界驗證

在 checkout root 執行：

```text
node --test tests/connector_chrome_core.test.mjs tests/connector_chrome_background.test.mjs
```

可選的 `connector_chrome_browser.test.mjs` 與 `connector_chrome_rich.test.mjs` 使用既有 Playwright；以 `PCS_PLAYWRIGHT_MODULE` 指向其 `index.mjs`，`PCS_CHROME_EXECUTABLE` 指向已安裝瀏覽器，`PCS_EXTENSION_EXECUTABLE` 可另外指定支援未封裝擴充載入的 Chromium 瀏覽器。品牌 Chrome 若不接受載入旗標，測試不冒充成功；本次實際擴充驗證使用隔離的 headless Edge。`PCS_SCREENSHOT_DIR` 指定頁內畫面輸出位置。

自 `0.2.2` 起，靜態 content script 是已生成的 `content.js`，由 `entry.js`、`inpage.mjs`、`dom.mjs`、`core.mjs` 打包而成。Chrome 直接在 isolated world 執行，不在頁面執行期間下載模組，也不公開 web-accessible resources。工具列補載同一檔案，再透過一般 runtime messaging 開啟 PCS 選單；重複補載不另建介面或監聽器。

PCS 控制項使用 `data-pcs-ui` host 與 Shadow DOM。全局入口定位在可見 ChatGPT titlebar 的右側原生操作組，SPA 或頁首晚到時重新定位；單則入口保留原訊息操作列的貼附選單。選取模式在訊息右側顯示勾選框，正文非互動區可切換整則選取；拖曳文字、雙擊選字、連結與原生控制項沿用頁面行為。選取狀態的限定樣式與處理器在完成選取時移除，正文及擷取指紋保持不變。沒有聊天副本、側欄或底部工具列。`background.mjs` 僅接受本擴充的 top-frame ChatGPT content sender，使用 `sender.tab.id` 讀取及傳送；不查詢目前作用中分頁。擷取函式本體將例外轉成 `capture_error`，避免 Chrome 丟失 exception 而只回空結果。跨 API 字典比較按欄位值，不依賴物件鍵順序。

測試以合成對話接管所有網站網路請求，真實載入擴充 service worker/content script，使用真正的 `chrome.scripting` 與 runtime messaging；Native host port 由合成回覆替代。涵蓋首次載入與補載、勾選、導航失效、UTF-8 分塊、下載、多 PCS、取消、狀態區別、富內容及頁內排版。新 DOM 測例另覆蓋隱藏舊聊天排除、角色單元與共用 turn 操作列。這不等同真實 ChatGPT 到桌面連接器的全流程驗收。

## 重建頁內腳本

交付目錄已帶有 `content.js`，使用擴充不需安裝建置依賴。修改來源模組後，開發者在 checkout 根目錄（或套件的 `source/`）使用 Node.js 18 以上與 pnpm 執行（本輪 pnpm 為 `11.25.0`）：

```text
pnpm --dir tools/chrome-content install --frozen-lockfile --ignore-scripts
node tools/build_chrome_content.mjs
node tools/build_chrome_content.mjs --check
```

套件的 `source/` 提供重建工具與鎖定依賴；在專案 checkout 可另執行 `node --test tests/connector_chrome_bundle.test.mjs`。開發依賴固定為 esbuild `0.25.12`，安裝在 `tools/chrome-content/node_modules`；`ESBUILD_MODULE` 可指定同版 esbuild 的 `lib/main.js` 使用既有隔離工具環境。產生器固定輸出 Chrome 116 可用的單一 IIFE，沒有外部 runtime import，`--check` 對照生成檔與目前來源。封裝沿用已生成檔案，不在建置套件時下載依賴。
