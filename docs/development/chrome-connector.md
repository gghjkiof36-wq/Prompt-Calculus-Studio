# Chrome 對話連接與保存

[開發指南](README.md) · [既有對話整理與可攜契約](markdown-retention.md)

本功能是來源基準 `44676bd25bffd620ba82a9c8882ff4487fecdfc8` 之後的本機增量，讓使用者把 ChatGPT 頁面選定內容傳入 PCS、下載檔案及查回保存位置。本文說明目前實作，不代表正式發布或真實網站全流程已驗收。首批沒有 Prompt 輸出、帳號同步或 ChatGPT 登入整合。

## 操作入口與執行歸屬

Chrome 116 以上的 Manifest V3 擴充位於 [`extensions/chrome`](../../extensions/chrome/README.md)。目前介面版本為 `0.2.0`，在 ChatGPT 原訊息的複製按鈕旁加入 PCS 小入口；按下後以貼附該入口的選單傳送或下載這一則訊息。頁首的 PCS 入口提供「選取全部」及「選取訊息」，全部仍指目前 DOM 已載入範圍。勾選框只在選取模式顯示；「完成選取」收起勾選框並保留已選內容，接著可在選單傳到 PCS 或下載 Capture JSON／Markdown。介面沿用原對話，沒有聊天副本、Chrome 側欄或底部工具列。

傳送狀態、查詢結果、取消及最近保存也在同一貼附選單。單則訊息失效後可重新讀取原訊息；若原節點已離開頁面，回到選取流程。尚未完成的操作保留原 `source_title`；換聊天時關閉舊訊息選單，之後從頁首查看原操作結果，避免把前一段對話的操作顯示成新頁的「此則訊息」。

[`entry.js`](../../extensions/chrome/entry.js) 是在 ChatGPT 網域載入的 isolated content script，動態載入 [`inpage.mjs`](../../extensions/chrome/inpage.mjs) 與 [`core.mjs`](../../extensions/chrome/core.mjs)。`manifest.json` 以 web-accessible resources 提供這兩個模組，移除 `sidePanel` 權限與 `side_panel` 入口；保留 `activeTab/scripting/storage/nativeMessaging` 及兩個 ChatGPT 網域的 host permissions。Chrome 工具列動作也交由原分頁的頁內介面處理。

PCS 的「探索」採三張等尺寸入口卡片；「ChatGPT 對話」開啟 [`ConnectorPage`](../../prompt_calculus_studio/connector_page.py)。主頁保留「加入 Chrome」及對話列表，提供搜尋、檔案匯入和保存位置；完成的項目以對話標題呈現，可開啟文件、整理對話或顯示資料夾。保存位置是目前 workspace 時顯示「目前工作區」，完整路徑放在 tooltip。搜尋無結果時清除不可見選取；清除搜尋後選取可見項目，相關動作隨選取狀態恢復。

「加入 Chrome」使用既有 `StudioDialog`，把設定集中為兩步：

1. **連接 PCS**：執行配套 native host 的帳戶安裝程式，成功後啟用本 workspace 的接收服務。
2. **載入 Chrome 擴充**：提供「開啟擴充頁」及「開啟擴充資料夾」，由使用者在 Chrome 開發人員模式載入未封裝項目。

[`ConnectorService`](../../prompt_calculus_studio/connector_service.py) 屬於原 PCS workspace；[`ConnectorImportDialog`](../../prompt_calculus_studio/connector_dialog.py) 沿用既有匯入預覽與 writer。接收資料不等於接受保存：`finish` 到 `previewing` 後，仍由 PCS 預覽的匯入動作接受目的地。完成回執由工作執行端保存，視窗的回呼只刷新結果。

同一 service 同時只提交一份對話。workspace 關閉時，已在寫入的操作先收尾；關閉被既有保護拒絕時恢復連接就緒狀態。此接點獨立於 ComfyUI 連線與工作佇列。

## 擷取範圍與富內容

[`capture.mjs`](../../extensions/chrome/capture.mjs) 只觀測目前頁面已載入、可見的 user／assistant 訊息。傳送前再次核對分頁、網址與正文／資產指紋；串流中的回覆不建立快照。它不讀其他聊天、網站儲存空間或 cookies，也不呼叫 ChatGPT 的內部 API。

[`background.mjs`](../../extensions/chrome/background.mjs) 只接受本擴充、ChatGPT 網域、頂層 frame 的 content-script sender；讀取與傳送都綁定 `sender.tab.id`，不再以全域 active/currentWindow 另選分頁。注入時呼叫 `captureLoadedConversation(true)`；函式本體將例外轉成可序列化的 `capture_error: {code,message}`，background 再回傳操作錯誤。擷取函式不依賴注入後會遺失的外層 closure，且排除標記為 `data-pcs-ui` 的頁內控制項。這些是來源綁定與錯誤傳遞契約，並非真實網站 DOM 已完整支援的證明。

頁內選取同時核對 URL、tab、內容指紋與目前操作世代；送出前固定選取快照，等待 `hello` 後再次確認來源，才建立 operation。background 依最新擷取內容重建所選 payload，訊息比較按欄位值而不依賴跨 API 的物件鍵順序。狀態與取消回覆須符合目前 instance／operation；晚到回覆不能復活已換掉的操作，也不能把終態降回 unknown。unknown 保留查詢與取消入口，只有使用者重新選取後才開始另一個操作。

目前 Chrome 實際產生的 `scope` 只有：

| 值 | 真實含義 |
| --- | --- |
| `loaded` | 這次 DOM 觀測到的全部訊息；不代表完整歷史或完整分支 |
| `selection` | 上述觀測中由使用者選定的訊息；未選正文與資產不傳出 |

驗證器也辨識 `current_branch` 與 `method: structured`，但目前 Chrome 不產生這兩項能力。不能因正常化成功就把 DOM 內容標成站方完整原始圖。沒有站方訊息 ID 時，擷取端使用 `capture-local-*`；正常化 mapping 的 parent／children 表示本次觀測順序。

可見標題、段落、列表、程式碼、表格、資料卡片、引用連結及可取得的 KaTeX 公式轉成 Markdown。CVSS／進度條只保存 DOM 提供的文字、`aria-valuetext`／`aria-valuenow` 等可存取數值；時間軸保留可見標籤與順序。有明示標記的工具活動／推理區和一般操作按鈕不列入正文。

圖片、Canvas、SVG 圖表、iframe、音訊／影片與附件保留具名參照；不下載媒體、不讀跨來源 iframe、不擷取 hover 狀態，也不從像素推算數據。沒有可讀數據的圖表只是一個有名稱與來源的項目，並未重建互動元件。來源不存在或包含臨時憑證時，以原對話網址作參照。這些項目是普通 Markdown 連結，不是會自動載入的圖片或可執行 HTML。

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

正常化後的 `message.content.parts` 保存正文與一次具名資產投影；資產白名單另存於該訊息的 `metadata.pcs_capture_resources`。沒有不透明 HTML、未選完整 payload 或二進位附件。頂層 `pcs_capture` 保存 provider、method、scope／source_scope、adapter version、訊息 IDs、`graph: observed-order`、`complete: false` 與訊息識別型別。

來源範圍與資產內容參與語意 digest；`captured_at` 不參與，直連時另存於操作帳本。這可避免同內容只因再次擷取時間不同就變成新來源。正常化不修改傳入物件。

## 與匯入、整理及可攜檔相容

[`conversation_archive.load_archive`](../../prompt_calculus_studio/conversation_archive.py) 辨識明確的 Capture envelope，呼叫同一驗證器，再沿原 ChatGPT mapping 流程。擴充下載的 JSON 與直連共用此語意；一般 mapping／既有 ChatGPT ZIP 的讀取契約保留。Markdown 下載則是選定內容的閱讀副本，不帶完整 PCS 來源關係。

[`conversation_import.import_selected`](../../prompt_calculus_studio/conversation_import.py) 保存正常化 `原始對話.json`、可編輯分支 Markdown、主文件及收據。Capture 只有一個分支且一卷時，入口 `對話.md` 直接呈現標題與訊息，沿用已受分卷上限約束的 bytes，保留隱藏訊息 trace；分支檔及原始 JSON 仍保存。多卷則使用簡短的來源範圍與分卷索引。一般 mapping／既有 ZIP 的分支索引不變。

`resources` 使用 capture-local metadata 和普通網址，不冒充既有附件指標，因此不會被 `conversation_markdown.references()` 誤判成已下載附件或重新抓取網頁檔案。可攜匯出保存目前主文件的實際編輯內容；重新開啟會直接看到同一正文與來源關係，不改寫原檔。

[`conversation_review.subset`](../../prompt_calculus_studio/conversation_review.py) 保留選定路徑的訊息與該訊息資產；`pcs_capture.message_ids` 同時取交集，縮小後 scope 成為 `selection`，source_scope 保留最初觀測範圍。來源範圍改動會使原候選失效。整理稿保存、重複另存與 [`markdown_portable`](../../prompt_calculus_studio/markdown_portable.py) 匯出／重開沿用同一 raw 與 subset，沒有另一套 MD1-2 保存格式。

## Native Messaging 與本機通道

固定 Chrome 擴充 ID 是 `migmpicacfjoipmfcgdjocgdbplgldan`，native host 名稱是 `com.promptcalculus.studio`。Manifest 的公開 key 固定開發模式載入時的擴充 ID；不是登入憑證。

[`tools/connector_host.py`](../../tools/connector_host.py) 是不依賴 Qt 的獨立標準庫主機。Chrome 與主機使用小端 4-byte 長度及 UTF-8 JSON frame；主機檢查固定 origin，再將請求轉成 Windows named pipe 的單次換行 JSON。PCS 的 `QLocalServer` 採用目前使用者存取權，另驗證每次啟動產生的 token。

PCS instance 依資料目錄識別，發現記錄預設位於 `%LOCALAPPDATA%/PromptCalculusStudio/Connector/instances`。`hello` 回傳可選 instance 與 readiness；workspace 尚未載入完成或正在關閉時不接受新操作。其餘命令為 `begin/chunk/finish/status/cancel/open/reveal`；`open/reveal` 只根據既有 operation 回執開啟文件，不接受任意外部檔案路徑。

## Spool、接受與恢復

[`TransferStore`](../../prompt_calculus_studio/connector_store.py) 將輸入暫存於發現目錄同層的 `transfers/<instance>`。service 在啟用、使用及改目的地時核對全部已接入根與保存目的地，避免暫存混入工作區文件樹。測試可用 `PCS_CONNECTOR_DISCOVERY` 指向隔離位置。

1. `begin` 持久建立 operation、傳輸大小與 SHA-256；`chunk` 按序累積 `.part`。同 operation 不得換 payload。
2. `finish` 完整驗證後寫入正常化 `.source.json`，記下 source ID、語意 hash、預期 writer content hash，再進 `previewing`。來源已落盤後清除 `.part`。
3. 使用者接受時綁定目的地與其目錄身分，進 `committing`。writer 的 keyword-only `operation_id` 貫穿開始與完成收據。
4. 新建目錄的 `on_started(operation, folder)` 在任何 payload 前記錄輸出身分；重用既有同內容文件的 `on_reused(operation, folder)` 在 pinned receipt 範圍內記錄舊回執關聯，才回報 skipped。
5. `complete/recover` 核對原綁定目的地、輸出目錄身分、操作、source ID／hash、必要檔案與實際原始 JSON。新建驗新 operation；重用驗已綁定舊 operation，可兼容舊收據沒有 operation 的情況。Markdown 可由使用者編輯，不以 Markdown 內容 hash 阻止查回或覆寫其修改。

成功回覆依持久回執；writer 完成後 ledger 暫時寫失敗仍保留可對帳狀態。恢復只查本 operation 綁定的目錄，不以標題搜尋任意工作區。無法確認時是 `unknown`，既有檔案保留；有 owned_path 的 unknown 可再次對帳。

取消尚未抵達的 operation 會建立 cancelled 墓碑，攔住晚到的 begin／finish。已知 unknown 不降級成 cancelled。完成／取消會清理本 operation 生成的 `.part/.source.json`；清理失敗不改保存結果，下次新接收時重試，unknown 所需來源仍保留。開始但未完成的匯入不列成正常對話；既有無收據的手動 JSON 仍可使用。

## 容量與封裝界線

| 層次 | 目前上限 |
| --- | --- |
| Capture | 16 MiB UTF-8 JSON、5,000 則訊息；每則投影後文字 2 MiB |
| Resources | 每則 32、總共 128；label 300 碼點／1,200 bytes，URL 2,048 碼點／8,192 bytes |
| Native frame | 512 KiB；Chrome 原始資料塊 192 KiB，接收端允許至 256 KiB |
| Spool | 256 MiB 生成暫存總量；最多 4 個 receiving／previewing／committing，最多 1,000 個 ledger |
| 本機通道 | 最多 8 個 client；PCS 單請求 512 KiB、讀取計時 20 秒；host I/O deadline 15 秒 |

超限會拒絕，沒有截斷後冒稱成功。下游仍遵守 [Markdown 保留契約](markdown-retention.md) 的單檔、分卷、整理與可攜容量。

[`tools/install_chrome_connector.py`](../../tools/install_chrome_connector.py) 接收獨立 host EXE，寫入目前帳戶的 hash 版本目錄，再登記 HKCU 的 Chrome NativeMessagingHosts 32-bit registry view；保留舊版本檔案。不能把 PCS 圖形 EXE 當 stdio host。

[`tools/build_connector.py`](../../tools/build_connector.py) 建立獨立 host／installer、Chrome 目錄、對應來源、授權與 hash manifest；`--reuse-native` 僅在 native source 和既有 binary hash 相符時沿用已驗產物。此包與 PCS 主程式需要配套交付；`PCS_CONNECTOR_PACKAGE` 可指定獨立套件位置。新增來源包 allowlist 包含 `extensions/` 與三個 connector 工具，但 `package_documents.source_paths()` 仍只採 Git 已登記檔案；未追蹤檔案不會因本機存在而自動進來源包。僅更新文件或來源 ZIP 不代表桌面 EXE 或整合安裝包已重建。

## 驗證與尚未確認的範圍

檔案層使用隔離合成資料驗證 Capture JSON／Markdown、富內容參照、範圍裁切、整理稿與可攜重開、取消、錯誤回執、目錄替換、skipped 後中斷、資產總量及暫存清理。相關測例是 [`test_connector_capture.py`](../../tests/test_connector_capture.py)、[`test_connector_recovery.py`](../../tests/test_connector_recovery.py)、[`test_conversation_import.py`](../../tests/test_conversation_import.py) 與 [`test_md12_retention.py`](../../tests/test_md12_retention.py)。

[`test_maintenance_package.py`](../../tests/test_maintenance_package.py) 核對本版 release 對應的必要 validation 文件、來源包 allowlist、connector 資料／transport 模組無 Qt 匯入，以及既有共享核心的 Qt-blocked 還原。這些是本地測試與臨時來源包，不構成發布。

已有背景 native host frame／Win32 pipe、獨立 EXE 與實際 Qt pipe 的有界測試，以及離屏 PCS 預覽／保存／查回測試。既有檔案與富內容測試覆蓋卡片、表格、citation、CVSS／時間軸、iframe 與圖片單訊息；舊側欄的介面結果不沿用為新版入口的驗收。

新版 [`connector_chrome_browser.test.mjs`](../../tests/connector_chrome_browser.test.mjs) 使用隔離的 headless Edge 實際載入未封裝 MV3 擴充，經真正的 service worker、content script、`chrome.scripting` 與 runtime messaging。網站網路請求由合成對話接管，Native host port 使用合成回覆。已觀測到注入錯誤可回傳具體原因、頁內入口不污染正文／指紋，以及單則 JSON／Markdown 下載；這比直接呼叫擷取函式的 DOM 測試多覆蓋注入與訊息邊界，仍不是真實 ChatGPT 帳號或桌面全鏈路測試。

同一測試組也檢查多 PCS 選擇、unknown 不隱含重送、`hello` 等待中導航、取消後晚到完成、原訊息歸屬、完成選取後保留內容、單則重新讀取、鍵盤操作及窄視窗選單。最終背景紀錄 `qa-private/03-chrome-transfer-20261023/chrome-integrated-final2-tests.txt` 為 28 通過、0 失敗、0 略過，耗時 13.08 秒；前輪失敗紀錄保留。這是上述隔離 MV3 與合成資料範圍的結果。

使用者已回報真實 Chrome 在對話載入後仍顯示「無法讀取對話，請重新讀取」。原碼核對確認舊版沒有在注入函式內保留例外原因，本輪已增加 `capture_error` 並綁定呼叫分頁；但尚未重現並確認該次真實頁面觸發的具體例外。不能把合成 DOM 通過或錯誤傳遞修正寫成真實讀取問題已解決。

尚未完成 ChatGPT 原頁選取 → 正式安裝 PCS 的人工驗收。registry 寫入採 mock；原生桌面視窗、IME、DPI、真實媒體與跨帳戶部署仍待相應驗證。
