# Chrome 對話連接與保存

[開發指南](README.md) · [既有對話整理與可攜契約](markdown-retention.md) · [富格式相容性矩陣](chrome-content-compatibility.md)

本功能是來源基準 `44676bd25bffd620ba82a9c8882ff4487fecdfc8` 之後的本機增量，讓使用者把 ChatGPT 頁面選定內容傳入 PCS、下載檔案及查回保存位置。本文說明目前實作，不代表正式發布或真實網站全流程已驗收。首批沒有 Prompt 輸出、帳號同步或 ChatGPT 登入整合。

## 操作入口與執行歸屬

Chrome 116 以上的 Manifest V3 擴充位於 [`extensions/chrome`](../../extensions/chrome/README.md)。目前 `0.2.5` 承接原網頁選位置直存、classic 腳本載入及右上入口，加入下載紀錄與 PNG 保存。ChatGPT 原訊息的複製按鈕旁仍有 PCS 小入口；單則選單只有儲存到 PCS、下載 JSON、下載 Markdown 三個動作。右上角、原生「更多／分享」操作組前的 PCS 入口提供選取與下載紀錄；「選取全部」仍指目前 DOM 已載入範圍。

選取模式中，每則訊息右側顯示勾選框，點擊訊息正文可切換整則的選取狀態；已選內容以淡底色標示。連結、按鈕與可編輯區保留原操作，拖曳或雙擊選字不改勾選。「取消」清空選取，並移除勾選框、樣式與整則點選處理。保存後可繼續原頁操作，不因上一筆成功鎖住新操作。介面沿用原對話，沒有聊天副本、Chrome 側欄或底部工具列。

[`dom.mjs`](../../extensions/chrome/dom.mjs) 的 `conversationHeaderSlot()` 優先辨識可見的 app-shell 主頁首與右側原生操作組，再退回有明確分享／更多按鈕的既有頁首。它排除隱藏副本，並在頁首延遲出現或 SPA 替換時重新定位同一個 PCS 入口；未找到有效操作組時等待，不把入口掛到第一個 `main`。這項布局修正不變更網站權限或擷取來源。

滑鼠移至或點擊「儲存到 PCS」時，保留第一層選單並向側邊展開資料夾子選單。只有多個 PCS 時才列工作區；單一 PCS 直接列目前位置與現有資料夾，其他已接入根可從「其他位置」進入。點目的地名稱即儲存，資料夾旁獨立箭頭用於展開下一層；子層保留儲存於目前層的項目。單一 PCS 的預設位置沒有子資料夾時，點原「儲存到 PCS」直接保存至預設位置。每次從目前預設位置開始，不沿用上次的深層資料夾。滑鼠移入只讀清單，不建立保存操作、不傳正文；沒有位置下拉表單或選完位置再按儲存的額外一步。

目的地選單開啟期間固定本次選取，暫停全選、完成選取及正文／勾選切換，避免畫面與將保存的訊息不一致。關閉子選單後恢復選取操作。子選單優先向右，空間不足時向左或限於視窗內；窄頁重疊時提供「返回」，Escape／左鍵回上一層並恢復焦點，右鍵展開資料夾。

正常進度由選單查回，完成後不顯示成功 footer／通知，也不開啟 PCS 預覽或整理頁。從頁首的「下載紀錄」手動查找檔案，每筆依序提供資料夾與開啟文件圖示；單則選單不含 history。單則訊息失效後可重新讀取原訊息；若原節點已離開頁面，回到選取流程。尚未完成的操作保留原 `source_title`；換聊天時關閉舊訊息選單，避免把前一段對話的操作顯示成新頁的「此則訊息」。

JSON／Markdown 下載經 `chrome.downloads.download()` 取得真實 download ID，追蹤 `onChanged/onErased` 並依 ID 查詢完成／存在狀態。只有本擴充保存的回執且狀態為 complete、未回報不存在時，才允許使用者點擊 `downloads.open/show`；不接受任意路徑。Chrome 的存在資訊可能延遲，錯誤仍由 API 回報處理。Manifest 新增 `downloads`、`downloads.open`，沒有 `notifications`；網站範圍與固定擴充 ID 保持不變。[Chrome Downloads API](https://developer.chrome.com/docs/extensions/reference/api/downloads)

`0.2.2` 的 `manifest.json` 直接載入 [`content.js`](../../extensions/chrome/content.js) 作為 isolated content script。它由 [`entry.js`](../../extensions/chrome/entry.js)、[`inpage.mjs`](../../extensions/chrome/inpage.mjs)、[`core.mjs`](../../extensions/chrome/core.mjs) 及 [`dom.mjs`](../../extensions/chrome/dom.mjs) 在建置時打包；執行期間沒有動態模組匯入、page bridge 或 web-accessible resources。Chrome 工具列先傳一般 toggle 訊息，沒有接收端時以 `chrome.scripting.executeScript({files:['content.js']})` 補載，再傳同一訊息。入口保留單一 controller，重複補載不重複建立 UI 或監聽器。權限維持 `activeTab/scripting/storage/nativeMessaging` 及兩個 ChatGPT 網域，沒有 `sidePanel`／`side_panel`。

頁面自動載入仍受 Chrome 的網站存取設定控制。本輪「必須先點工具列擴充」的回報，已由瀏覽器選單截圖確認當時選取的是 `When you click the extension`；使用者可改為 `On chatgpt.com` 後刷新。這個設定原因與頁首掛載位置、先前的動態模組載入失敗分開記錄，不以新增注入權限或背景掃描繞過。

[`tools/build_chrome_content.mjs`](../../tools/build_chrome_content.mjs) 使用固定 esbuild `0.25.12`，依 [`tools/chrome-content/package.json`](../../tools/chrome-content/package.json) 與 lockfile 重建 Chrome 116 的單一 IIFE；`--check` 逐 byte 核對生成檔與來源。完整重建步驟在擴充 README。套件保留生成檔及對應來源／建置工具，使用者載入擴充不需要 Node 或 pnpm。

PCS 的「探索」採三張等尺寸入口卡片；「下載紀錄」開啟 [`ConnectorPage`](../../prompt_calculus_studio/connector_page.py)。紀錄按今天／昨天／日期分組，可搜尋，每筆顯示標題、檔名、時間及路徑，直接提供資料夾、開啟文件與移除紀錄圖示。傳送中顯示進度，失敗或取消保留必要狀態；成功不額外標示「已保存」。移除只隱藏終態紀錄，不刪檔、不移除重試／恢復帳本。頁首保留「匯入檔案…」與「加入 Chrome」；沒有整理對話、含混保存位置或依選取而變動的全頁底部按鈕。後端既有預設目的地值保持不變。

「加入 Chrome」使用既有 `StudioDialog`，把設定集中為兩步：

1. **連接 PCS**：執行配套 native host 的帳戶安裝程式，成功後啟用本 workspace 的接收服務。
2. **載入 Chrome 擴充**：提供「開啟擴充頁」及「開啟擴充資料夾」，由使用者在 Chrome 開發人員模式載入未封裝項目。

[`ConnectorService`](../../prompt_calculus_studio/connector_service.py) 屬於原 PCS workspace。新版在使用者於網頁點擊儲存目的地時固定來源與目的地；接收完成後以既有 writer 背景保存，完成回執由工作執行端保存。這是保存接受時點由桌面預覽按鈕移到網頁儲存動作，不是收到任意資料便接受。`hello` 宣告 `save_destination_v1`，新版選單不把舊 PCS 當成已具備此能力。

舊客戶端未帶目的地的請求仍由 [`ConnectorImportDialog`](../../prompt_calculus_studio/connector_dialog.py) 提供小型位置確認。它直接使用共用 `StudioDialog`、`ImportJob` 與同一 writer，不再繼承完整對話匯入頁；正文不渲染，讀完不改視窗大小，因此不再出現讀取後擴大卻未重新置中的偏移。成功即關閉且只刷新文件樹，不自動開啟文件或摘要整理。一般檔案匯入及使用者主動開啟的對話整理功能保持原入口。

同一 service 的背景保存依序執行，已接受的排隊與執行中操作都納入關閉保護。workspace 關閉時先收尾；關閉被既有保護拒絕時恢復連接就緒狀態。此接點獨立於 ComfyUI 連線與工作佇列。

compact 相容視窗在讀取或寫入期間收到關閉要求時先隱藏並要求取消，保留完成回呼的擁有者直到工作結束；不能因 `WA_DeleteOnClose` 提前銷毀視窗，讓已落盤的操作永遠留在 `service.writing`。關閉動作不覆蓋持久保存結果。

## 擷取範圍與富內容

[`capture.mjs`](../../extensions/chrome/capture.mjs) 只觀測目前頁面已載入、可見的 user／assistant 訊息。傳送前再次核對分頁、網址與正文／資產指紋；串流中的回覆不建立快照。它不讀其他聊天、網站儲存空間或 cookies，也不呼叫 ChatGPT 的內部 API。

`0.2.1` 同時支援舊的 `data-message-author-role` 與本輪觀測到的新 DOM 接點：

- 優先在可見的 `role="region"`、`aria-label="對話"`／`"Conversation"` 區域定位訊息，再退回可見的 `main`／`role="main"`。祖先的 `hidden`、`aria-hidden="true"`、`display:none` 或 `visibility:hidden` 會排除整個子樹，避免 SPA 保留的舊聊天混入目前頁面。
- 新訊息以 `data-chatgpt-search-unit-key`／`data-content-search-unit-key` 定位，只接受 key 結尾的 `:user`／`:assistant`；assistant 另須有 `h4[data-conversation-role="assistant"]`。中間的思考／時間區塊不視為回答。巢狀同 key 單元只保留外層；非巢狀的可見重複 key 會拒絕擷取，等待頁面穩定。
- 新舊訊息去除重疊後依 DOM 順序排列。`dom.mjs` 的 `locateConversation()` 用於原訊息入口與勾選位置；`captureLoadedConversation()` 內保留相同定位器，因 `chrome.scripting` 序列化不保留模組 closure。兩處的篩選、去重與排序必須同步修改。`anchors.index` 保留定位器的原始索引，即使空正文訊息未進 payload，也不重新壓縮索引。
- 原生複製入口以明確標籤和 `.turn-action-controls` 對應。新 DOM 的 assistant 操作列可能位於各訊息單元外側、同一 turn 的共用 group 內；此時只在同一 `data-content-search-turn-key` 與 `data-talvt-turn-state` group 內尋找唯一、未屬於其他訊息的操作列。使用者訊息的列、程式碼複製、隱藏列和相鄰 turn 不作替代；歸屬不唯一時不掛入口。

單一站方訊息 ID 可沿用；單元的 `data-chatgpt-search-message-ids` 含多個 ID 時，不任選其中一個冒充整個單元，而使用本機內容識別。角色標題、原生操作列與 `data-pcs-ui` 不進正文或資產清單。

[`background.mjs`](../../extensions/chrome/background.mjs) 只接受本擴充、ChatGPT 網域、頂層 frame 的 content-script sender；讀取與傳送都綁定 `sender.tab.id`，不再以全域 active/currentWindow 另選分頁。注入時呼叫 `captureLoadedConversation(true)`；函式本體將例外轉成可序列化的 `capture_error: {code,message}`，background 再回傳操作錯誤。擷取函式不依賴注入後會遺失的外層 closure，且排除標記為 `data-pcs-ui` 的頁內控制項。這些是來源綁定與錯誤傳遞契約，並非真實網站 DOM 已完整支援的證明。

訊息內若有可見的 `data-dil-render-failed`，表示目前網站 DOM 呈現的是回覆載入失敗狀態；擷取會拒絕此次快照，不把錯誤提示保存成 assistant 回答。這項判斷依明示 DOM 標記，不以「無法載入這則回覆」字串比對一般正文。它與擴充執行例外是不同原因，兩者都經 `capture_error` 回傳；錯誤 envelope 只負責保留原因，不會修復站方內容載入。

頁內選取同時核對 URL、tab、內容指紋與目前操作世代；送出前固定選取快照，等待 `hello` 後再次確認來源，才建立 operation。background 依最新擷取內容重建所選 payload，訊息比較按欄位值而不依賴跨 API 的物件鍵順序。狀態與取消回覆須符合目前 instance／operation；晚到回覆不能復活已換掉的操作，也不能把終態降回 unknown。unknown 保留查詢與取消入口，只有使用者重新選取後才開始另一個操作。

目前 Chrome 實際產生的 `scope` 只有：

| 值 | 真實含義 |
| --- | --- |
| `loaded` | 這次 DOM 觀測到的全部訊息；不代表完整歷史或完整分支 |
| `selection` | 上述觀測中由使用者選定的訊息；未選正文與資產不傳出 |

驗證器也辨識 `current_branch` 與 `method: structured`，但目前 Chrome 不產生這兩項能力。不能因正常化成功就把 DOM 內容標成站方完整原始圖。沒有站方訊息 ID 時，擷取端使用 `capture-local-*`；正常化 mapping 的 parent／children 表示本次觀測順序。

可見標題、段落、列表、程式碼、表格、資料卡片、引用連結及可取得的 KaTeX 公式轉成 Markdown。CVSS／進度條只保存 DOM 提供的文字、`aria-valuetext`／`aria-valuenow` 等可存取數值；時間軸保留可見標籤與順序。有明示標記的工具活動／推理區和一般操作按鈕不列入正文。

引用來源卡片已採標準 Markdown 腳註，PCS 閱讀端提供 hover card。擷取會保留可取得的標題、日期及像素；只有 DOM 明確提供 `href/data-href/data-url` 時才保存真實來源網址。現行僅有按鈕、沒有 URL 的 ChatGPT 卡片保留已有資訊及「回到原對話查看來源」連結，不宣稱已取得原始網頁網址。合成來源擷取與 reader 的窄測之外，`citation-dom-capture` 已通過實際擷取 → writer → 本機 PNG → portable 重開 → PCS Qt 引用標籤／hover card。此全鏈只驗證一張 80×40 PNG 與兩筆來源：第一筆無 URL 而回到原對話，第二筆保留 DOM 提供的 `https://example.com/fix?version=002.50`。腳註首行／續行、程式碼及來源範圍邊界測例 16／16 通過；不延伸為真實 ChatGPT 所有卡片或縮圖的相容保證。

`0.2.5` 將可讀取的 SVG、Canvas，以及已載入的同源或合法 CORS 圖片 rasterize 成 PNG data URI，作為正文圖片。SVG 採繪圖元素與樣式白名單，只保留內部片段引用；Recharts 的單行、簡單 HTML 軸標籤轉成 SVG 文字，不嵌入任意 HTML。此路徑不下載來源媒體、不從像素推算數據，也不保留動畫或 hover 互動。

污染 Canvas、跨來源 iframe、表內圖、複雜 SVG HTML、隱藏數據及無法取得的媒體仍保留具名來源參照；來源不存在或包含臨時憑證時，以原對話網址作參照。參照是普通連結，不冒充已下載圖片。普通／合併表格、資料卡及公式的接點和邊界見相容性矩陣。

## Capture v1 與正常化

[`connector_capture.py`](../../prompt_calculus_studio/connector_capture.py) 的 `validate_capture()` 接受白名單欄位：

```json
{
  "version": 1,
  "provider": "chatgpt",
  "conversation_id": "conversation-id",
  "title": "對話標題",
  "url": "https://chatgpt.com/c/conversation-id",
  "method": "dom",
  "scope": "loaded",
  "adapter_version": "1",
  "captured_at": "2026-10-23T12:00:00Z",
  "messages": [
    {
      "id": "capture-local-0-example",
      "role": "assistant",
      "content": "| 指標 | 值 |\n| --- | --- |\n| CVSS | 8.1 |",
      "resources": [{"kind": "chart", "label": "風險趨勢", "url": null}]
    }
  ]
}
```

`conversation_id` 可為 JSON `null`；沒有站方識別時，以觀測內容產生本機識別。`resources` 可省略，項目固定為 `{kind,label,url}`，其中 `kind` 是 `citation/image/chart/attachment/embed`，`url` 是 http(s) 參照或 `null`。只有圖片的訊息可使用空 `content` 加上資產項目。普通 citation 優先直接留在 Markdown 正文，避免重複資產項。

正常化後的 `message.content.parts` 保存正文、白名單表格 HTML、PNG data URI 與一次具名資產投影；來源參照白名單另存於該訊息的 `metadata.pcs_capture_resources`。不攜帶任意可執行 HTML 或未選正文。頂層 `pcs_capture` 保存 provider、method、scope／source_scope、adapter version、訊息 IDs、`graph: observed-order`、`complete: false` 與訊息識別型別。

來源範圍與資產內容參與語意 digest；`captured_at` 不參與，直連時另存於操作帳本。這可避免同內容只因再次擷取時間不同就變成新來源。正常化不修改傳入物件。

## 與匯入、整理及可攜檔相容

[`conversation_archive.load_archive`](../../prompt_calculus_studio/conversation_archive.py) 辨識明確的 Capture envelope，呼叫同一驗證器，再沿原 ChatGPT mapping 流程。擴充下載的 JSON 與直連共用此語意；一般 mapping／既有 ChatGPT ZIP 的讀取契約保留。Markdown 下載則是選定內容的閱讀副本，不帶完整 PCS 來源關係。

[`conversation_import.import_selected`](../../prompt_calculus_studio/conversation_import.py) 保存正常化 `原始對話.json`、可編輯分支 Markdown、主文件及收據。Capture 只有一個分支且一卷時，入口 `對話.md` 直接呈現標題與訊息，沿用已受分卷上限約束的 bytes，保留隱藏訊息 trace；分支檔及原始 JSON 仍保存。多卷則使用簡短的來源範圍與分卷索引。一般 mapping／既有 ZIP 的分支索引不變。

`resources` 使用 capture-local metadata 和普通網址，不冒充既有附件指標，因此不會被 `conversation_markdown.references()` 誤判成已下載附件或重新抓取網頁檔案。可攜匯出保存目前主文件的實際編輯內容；重新開啟會直接看到同一正文與來源關係，不改寫原檔。

[`capture_images.py`](../../prompt_calculus_studio/capture_images.py) 只為帶有 `pcs_capture` 的來源處理自包含 PNG。保存前核對 Base64、PNG chunk／CRC／IHDR／IEND、尺寸及有界 scanline 解壓；每圖 2 MiB、1,600 萬像素，總 Capture 16 MiB。它以來源範圍只替換實際圖片連結 target，在閱讀副本改成 `assets/<sha256>.png`；code 範例與原始 JSON 不變。相同 bytes 共用資產，最終收據的 `capture_images` 與 files 清單記錄 hash。重用及恢復從原 JSON 重算預期資產，缺圖／改圖／回執不符不可標 complete；可攜輸出帶上同一圖片 bytes。

[`conversation_review.subset`](../../prompt_calculus_studio/conversation_review.py) 保留選定路徑的訊息與該訊息資產；`pcs_capture.message_ids` 同時取交集，縮小後 scope 成為 `selection`，source_scope 保留最初觀測範圍。來源範圍改動會使原候選失效。整理稿保存、重複另存與 [`markdown_portable`](../../prompt_calculus_studio/markdown_portable.py) 匯出／重開沿用同一 raw 與 subset，沒有另一套 MD1-2 保存格式。

## Native Messaging 與本機通道

固定 Chrome 擴充 ID 是 `migmpicacfjoipmfcgdjocgdbplgldan`，native host 名稱是 `com.promptcalculus.studio`。Manifest 的公開 key 固定開發模式載入時的擴充 ID；不是登入憑證。

[`tools/connector_host.py`](../../tools/connector_host.py) 是不依賴 Qt 的獨立標準庫主機。Chrome 與主機使用小端 4-byte 長度及 UTF-8 JSON frame；主機檢查固定 origin，再將請求轉成 Windows named pipe 的單次換行 JSON。PCS 的 `QLocalServer` 採用目前使用者存取權，另驗證每次啟動產生的 token。

PCS instance 依資料目錄識別，發現記錄預設位於 `%LOCALAPPDATA%/PromptCalculusStudio/Connector/instances`。`hello` 回傳可選 instance、能力與 readiness；workspace 尚未載入完成或正在關閉時不接受新操作。其餘命令為 `destinations/begin/chunk/finish/status/cancel/open/reveal`；`open/reveal` 只根據既有 operation 回執開啟文件，不接受任意外部檔案路徑。

[`connector_destinations.py`](../../prompt_calculus_studio/connector_destinations.py) 只列出 workspace 已接入根、既有保存位置及其下層目錄。`destinations` 接受服務端先前發出的 session opaque `folder_id`；回傳 roots、current、folders 及 default_id，每列有 id／name／path，目前目錄另有 parent_id。清單不讀檔案正文，排除連結與重解析點，限制枚舉項目與回覆大小。目錄 ID 綁路徑及身分；目錄被替換、脫離已接入範圍或與暫存重疊時須重新選擇。重新啟動後舊 session ID 失效，選單回到可用的預設位置。

## Spool、接受與恢復

[`TransferStore`](../../prompt_calculus_studio/connector_store.py) 將輸入暫存於發現目錄同層的 `transfers/<instance>`。service 在啟用、使用及改目的地時核對全部已接入根與保存目的地，避免暫存混入工作區文件樹。測試可用 `PCS_CONNECTOR_DISCOVERY` 指向隔離位置。

1. `begin` 持久建立 operation、傳輸大小與 SHA-256；新版同時核對 `destination_id` 並固定目的地、所屬根及其身分。`chunk` 按序累積 `.part`。同 operation 不得換 payload 或目的地。
2. `finish` 完整驗證後寫入正常化 `.source.json`，記下 source ID、語意 hash、預期 writer content hash，再進 `previewing`。來源已落盤後清除 `.part`。
3. 帶有網頁已選目的地的操作在 `finish` 完成來源驗證後，重新核對位置並排程背景保存；沒有目的地的舊操作才等 compact 視窗接受。接受後進 `committing`，writer 的 keyword-only `operation_id` 貫穿開始與完成收據。
4. 新建目錄的 `on_started(operation, folder)` 在任何 payload 前記錄輸出身分；重用既有同內容文件的 `on_reused(operation, folder)` 在 pinned receipt 範圍內記錄舊回執關聯，才回報 skipped。
5. `complete/recover` 核對原綁定目的地、輸出目錄身分、操作、source ID／hash、必要檔案與實際原始 JSON。新建驗新 operation；重用驗已綁定舊 operation，可兼容舊收據沒有 operation 的情況。Markdown 可由使用者編輯，不以 Markdown 內容 hash 阻止查回或覆寫其修改。

成功回覆依持久回執；writer 完成後 ledger 暫時寫失敗仍保留可對帳狀態。恢復只查本 operation 綁定的目錄，不以標題搜尋任意工作區。無法確認時是 `unknown`，既有檔案保留；有 owned_path 的 unknown 可再次對帳。

取消尚未抵達的 operation 會建立 cancelled 墓碑，攔住晚到的 begin／finish。已知 unknown 不降級成 cancelled。完成／取消會清理本 operation 生成的 `.part/.source.json`；清理失敗不改保存結果，下次新接收時重試，unknown 所需來源仍保留。開始但未完成的匯入不列成正常對話；既有無收據的手動 JSON 仍可使用。

## 容量與封裝界線

| 層次 | 目前上限 |
| --- | --- |
| Capture | 16 MiB UTF-8 JSON、5,000 則訊息；每則投影後文字 2 MiB |
| Resources | 每則 32、總共 128；label 300 碼點／1,200 bytes，URL 2,048 碼點／8,192 bytes |
| Capture PNG | 每張 2 MiB、1,600 萬像素，最多 128 張；仍受 Capture／訊息容量限制 |
| Native frame | 512 KiB；Chrome 原始資料塊 192 KiB，接收端允許至 256 KiB |
| Spool | 256 MiB 生成暫存總量；最多 4 個 receiving／previewing／committing，最多 1,000 個 ledger |
| 本機通道 | 最多 8 個 client；PCS 單請求 512 KiB、讀取計時 20 秒；host I/O deadline 15 秒 |

超限會拒絕，沒有截斷後冒稱成功。下游仍遵守 [Markdown 保留契約](markdown-retention.md) 的單檔、分卷、整理與可攜容量。

[`tools/install_chrome_connector.py`](../../tools/install_chrome_connector.py) 接收獨立 host EXE，寫入目前帳戶的 hash 版本目錄，再登記 HKCU 的 Chrome NativeMessagingHosts 32-bit registry view；保留舊版本檔案。不能把 PCS 圖形 EXE 當 stdio host。

[`tools/build_connector.py`](../../tools/build_connector.py) 建立獨立 host／installer、Chrome 目錄、對應來源、授權與 hash manifest；`--reuse-native` 僅在 native source 和既有 binary hash 相符時沿用已驗產物。此包與 PCS 主程式需要配套交付；`PCS_CONNECTOR_PACKAGE` 可指定獨立套件位置。新增來源包 allowlist 包含 `extensions/` 與三個 connector 工具，但 `package_documents.source_paths()` 仍只採 Git 已登記檔案；未追蹤檔案不會因本機存在而自動進來源包。僅更新文件或來源 ZIP 不代表桌面 EXE 或整合安裝包已重建。

## 驗證與尚未確認的範圍

`0.2.5` 當輪增量：下載紀錄已完成六項帳本保留、四項實際離屏頁面測試，以及受影響 compact-save 測例；包含日期／搜尋、原位進度更新、逐筆動作、隱藏紀錄不刪檔、成功不跳頁／通知。Chrome 的下載 API 回執、取消清空與保存後可繼續操作由新版 save/background/browser 測例驗證，不能沿用以下舊版成功 footer 的畫面作新版證據。

PNG 已完成五項合成瀏覽器擷取 → writer → portable 重開的檔案鏈路檢查；[`test_capture_images.py`](../../tests/test_capture_images.py) 另核對 PNG 完整性、來源不變、取消、重用與恢復。實際離屏 Qt reader 對原保存文件及可攜副本、1000／360 寬度完成 29 項檢查，五張 PNG 均解碼且無溢出，來源 bytes、dirty／Undo 保持；同程序 HTML reader 回歸一項通過。這是合成圖表的實際閱讀證據，不是所有真實 SVG／Canvas 的相容保證。普通 Markdown 出口另經 [`test_markdown_inline_png.py`](../../tests/test_markdown_inline_png.py) 三項檢查：實際匯出器產出含五張 PNG data URI 的單檔 `.md`，直接由 PCS 在 1000／360 寬度解碼，原件／Undo 不變且不建立資產檔；無效、超限與其他 MIME 不當作成功圖片。此路徑透過 `markdown_rich_view.resource()` 使用既有 PNG 完整驗證，與 JSON 匯入、writer／portable 路徑分開驗證。長對話仍為 loaded 範圍，逐視窗掃描只列在相容性方案；下方各版本原始驗證紀錄保留，不累加成一次完整驗收。

檔案層使用隔離合成資料驗證 Capture JSON／Markdown、富內容參照、範圍裁切、整理稿與可攜重開、取消、錯誤回執、目錄替換、skipped 後中斷、資產總量及暫存清理。相關測例是 [`test_connector_capture.py`](../../tests/test_connector_capture.py)、[`test_connector_recovery.py`](../../tests/test_connector_recovery.py)、[`test_conversation_import.py`](../../tests/test_conversation_import.py) 與 [`test_md12_retention.py`](../../tests/test_md12_retention.py)。

[`test_maintenance_package.py`](../../tests/test_maintenance_package.py) 核對本版 release 對應的必要 validation 文件、來源包 allowlist、connector 資料／transport 模組無 Qt 匯入，以及既有共享核心的 Qt-blocked 還原。這些是本地測試與臨時來源包，不構成發布。

已有背景 native host frame／Win32 pipe、獨立 EXE 與實際 Qt pipe 的有界測試，以及離屏 PCS 預覽／保存／查回測試。既有檔案與富內容測試覆蓋卡片、表格、citation、CVSS／時間軸、iframe 與圖片單訊息；舊側欄的介面結果不沿用為新版入口的驗收。

新版 [`connector_chrome_browser.test.mjs`](../../tests/connector_chrome_browser.test.mjs) 使用隔離的 headless Edge 實際載入未封裝 MV3 擴充，經真正的 service worker、content script、`chrome.scripting` 與 runtime messaging。網站網路請求由合成對話接管，Native host port 使用合成回覆。已觀測到注入錯誤可回傳具體原因、頁內入口不污染正文／指紋，以及單則 JSON／Markdown 下載；這比直接呼叫擷取函式的 DOM 測試多覆蓋注入與訊息邊界，仍不是真實 ChatGPT 帳號或桌面全鏈路測試。

同一測試組也檢查多 PCS 選擇、unknown 不隱含重送、`hello` 等待中導航、取消後晚到完成、原訊息歸屬、完成選取後保留內容、單則重新讀取、鍵盤操作及窄視窗選單。先前原操作列介面的背景紀錄 `chrome-integrated-final2-tests.txt` 為 28 通過、0 失敗、0 略過，耗時 13.08 秒；前輪失敗紀錄保留。這是上述隔離 MV3 與合成資料範圍的結果。

`0.2.1` 新增 [`connector_chrome_modern_dom.test.mjs`](../../tests/connector_chrome_modern_dom.test.mjs)，以本輪實際觀測的 DOM 結構建立合成頁面，驗證新角色單元、巢狀去重、隱藏舊頁排除、明示回覆載入失敗、訊息索引及原操作列歸屬。兩次測試的範圍分開記錄，不相加成一份驗收：

| 紀錄 | 結果與涵蓋範圍 |
| --- | --- |
| `chrome-modern-dom-tests.txt` | 32 通過、0 失敗、0 略過，13.876 秒；新 DOM 測例連同 core、background、browser、rich 五份相關測試，包含隔離 Edge MV3 的真實注入／訊息邊界與合成 Native host |
| `chrome-modern-controls-tests.txt` | 後續共享操作列修正後的局部 7 通過、0 失敗、0 略過，4.740 秒；新增 assistant 列位於共用 group 的結構、程式碼複製排除、相鄰 turn 不借用，以及單則下載歸屬；未再執行整套 32 項 |

`0.2.2` 移除動態匯入後，相關六份 JavaScript 測試的 `chrome-classic-content-tests.txt` 為 39 通過、0 失敗、0 略過，11.803 秒。除上述範圍外，新增 [`connector_chrome_bundle.test.mjs`](../../tests/connector_chrome_bundle.test.mjs) 的固定 esbuild 重建一致性、classic script 語法及 manifest 無公開資源檢查；background 測例核對工具列 fallback 先補載檔案再傳 toggle。實際隔離 Edge MV3 測例另將 `content.js` 連續補載兩次，確認入口、選單與每則訊息按鈕各維持一份，toggle 仍只處理一次，正文指紋不變。這輪仍使用合成網站與 Native host 回覆。

`0.2.3` 新增 [`connector_chrome_selection.test.mjs`](../../tests/connector_chrome_selection.test.mjs)，涵蓋頁首延遲出現、隱藏副本與 SPA 替換、右側勾選、正文／程式碼點選、拖曳及雙擊選字、原生操作、離開選取後清理及寬窄布局。首輪完整相關組 `chrome-right-selection-final.txt` 為 43 通過、3 失敗：舊測例在選單仍開啟時點被遮住的右側勾選框而超時，連帶影響下一子測例及父測。保留原紀錄，將操作改為先 Escape 關選單後，對 browser、selection、bundle 三檔完成 `chrome-right-selection-confirm.txt`：22 通過、0 失敗、0 略過，13.962 秒。最後外觀使用淡底色與圓角，已看過 1200／600 寬度的背景合成頁截圖；生成檔一致性及 Impeccable 靜態檢查通過。其他前輪已通且不受最後外觀／測例操作調整影響的項目沿用原紀錄，沒有把兩輪數目相加或稱為真實網站全流程通過。

`0.2.4` 直存首版 `d039395` 新增 [`connector_chrome_save.test.mjs`](../../tests/connector_chrome_save.test.mjs)，在同一隔離 MV3 環境測試單一／多個 PCS、根與子資料夾切換、最後位置及失效後回預設、返回不送正文、按儲存才綁定位置、舊版能力拒絕、儲存自動查回、錯誤恢復及 SPA 後晚到回覆。當時 1100／390 寬度背景截圖已檢視；第一輪原生選單測試曾以不受信任的程式事件切換而失敗，改用真實鍵盤操作後複驗，產品沒有放寬事件信任限制。此版位置表單隨後被使用者明確改為右側資料夾子選單，原表單截圖及測例結果只保留作首版歷史證據。

該首版最後檢查另發現位置下拉在非同步讀取時重建會失去焦點，導致鍵盤 Enter 意外觸發返回；修正後 `chrome-direct-save-keyboard.txt` 的 save、browser、bundle 三檔為 25 通過、0 失敗、0 略過。完整命令、環境及來源 hash 見本機 `chrome-direct-save-receipt.json`，先前原始失敗紀錄保留，不將首版表單的 25 項當成後續級聯選單驗收。

最終資料夾級聯選單的 save、browser、bundle 三檔為 21 通過、0 失敗、0 略過（`chrome-cascade-final.txt`，14.961 秒）。測試涵蓋 hover 不寫入、點資料夾直存、獨立展開箭頭、空位置直接保存、多 PCS／其他根、晚到回覆、失效位置、選取凍結與恢復、鍵盤焦點、窄頁返回及邊界翻轉；同時核對實際 MV3 載入與生成 bundle。1100／390 背景合成頁已檢視，Impeccable 最後檢查為空；命令、四個受影響檔案 hash 與限制記在本機 `chrome-cascade-receipt.json`。這組使用合成網站與 Native port，與下段真實 Qt pipe／writer 驗證分開。

同版 [`test_connector_destinations.py`](../../tests/test_connector_destinations.py) 檢查目錄識別、範圍、重解析點及清單容量；[`test_connector_direct_save.py`](../../tests/test_connector_direct_save.py) 以隔離離屏 PCS 驗證真實背景 writer、已選子資料夾、排隊／取消、關閉保護、舊新入口互斥、目錄失效、持久回執恢復與不開啟視窗。提供 `PCS_CONNECTOR_HOST` 時另以編譯 EXE 的 stdio frame 經實際 Qt pipe 跑 `destinations → begin/chunk/finish → status`，核對產出文件；未提供時明確略過該測例。這與 MV3 測試的合成 Native port 是分開的兩段證據。

[`test_connector_runtime.py`](../../tests/test_connector_runtime.py) 同步檢查 compact 相容視窗不渲染正文、不在載入後變大、不自動開啟文件或整理，以及發布後立即關閉仍能處理完成回執。相關 host、recovery、capture 與來源包 Qt 分離檢查亦需隨此協定增量驗證。各次原始紀錄保存在本機 QA 的 `direct-save-*`、`direct-save-r6/` 及 `screenshots-r6/`，不得把合成／離屏測試當成真實帳號完整驗收。

真實 Chrome 的只讀觀測確認，新頁面可能完全沒有 `data-message-author-role`，因此只靠舊 selector 無法讀取；本版已增加上述單元定位。但使用者先前的泛化讀取錯誤不只有這一種可能原因，不能將 selector 修正視為所有讀取失敗已解。另觀測到部分網站回答即使停用擴充，仍會轉成 `data-dil-render-failed`；這項對照將網站回覆載入失敗與 PCS 擷取錯誤分開，沒有據此宣稱站方失敗的根因。

後續真實 Chrome 的 `0.2.1` 啟用測試中，原網頁正常、沒有上述站方失敗標記，但 PCS 入口未出現；console 記錄 `Failed to fetch dynamically imported module`，目標是擴充的 `inpage.mjs`。`0.2.2` 因而改為直接載入 classic bundle，移除這條執行期 module fetch 路徑。使用者重新載入後回報可用；同一真實 Chrome 分頁已觀測到原訊息列的 PCS 入口，單則選單可讀取訊息並啟用保存及下載動作。這只確認該頁的載入與擷取入口恢復，沒有據此認定桌面保存完成。

使用者後續提供的 `0.2.3` 真實畫面已顯示從 Chrome 接收、PCS 完成保存及產出的文件，並指出整篇預覽與完成後整理動作多餘；這是此次 `0.2.4` 流程調整的直接依據。該畫面不等於新版原網頁選位置流程或保存 bytes 已完成驗證。新版真實 ChatGPT 原頁選位置 → Native Messaging → PCS 背景保存 → 查回文件仍須與本輪隔離測試分開記錄。原生桌面視窗、IME、DPI、真實媒體與跨帳戶部署仍待相應驗證。
