# ChatGPT 網頁富格式相容性

[Chrome 連接契約](chrome-connector.md) · [Markdown 保存契約](markdown-retention.md)

本文供擷取、保存及閱讀端開發使用，記錄 2026-10-24、`0.2.5` 工作樹的富格式增量。前一固定基準為 `d68e14076e220d108e080728dbd3526d048ce856`；以下新增能力尚未代表已發布或網站所有變體皆已驗收。PNG 已通過合成瀏覽器擷取、檔案保存、可攜重開及實際離屏 Qt 圖像閱讀檢查；此證據不延伸成所有真實網站圖表皆相容。

## 內容類型與保存目標

先分辨「可讀的資料結構」、「目前畫面的視覺結果」與「可操作的小工具」。資料表保留欄列與原值；圖表同時保留可取得的資料和靜態圖；互動元件保留當前狀態及來源，並不把網頁執行環境搬進 Markdown。

OpenAI 的 2026-06-08 公告列出直接出現在回答內的互動圖表；2026-10-07 公告另描述比較、圖解與計算器等 Intelligent UI。這些能力不能推出任何固定 DOM 選擇器或匯出 API。[官方更新紀錄](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)

Data Analysis 的表格可下載 CSV，圖表預設下載 PNG；官方也區分靜態圖與可切換的互動圖。這提供資料／圖像兩條保存路徑的產品依據，但不是所有聊天內小工具都有相同下載按鈕的保證。[資料分析操作](https://help.openai.com/en/articles/9213685-extracting-insights-with-chatgpt-data-analysis)、[資料分析能力](https://help.openai.com/en/articles/8437071-data-analysis-with-chatgpt)

| 來源內容 | 保存及閱讀方式 | 本輪狀態／限制 | 下一個必要驗證或接點 |
| --- | --- | --- | --- |
| 一般 HTML 表格、GFM 表格 | 單層規則表轉 pipe Markdown，保留標題、欄序、文字值及 inline 格式；閱讀端使用可橫捲的滿寬表格 | 已有合成擷取與實際閱讀測試；數字不先轉浮點數，避免精度／前導零改變 | 無標頭表、多列標頭、表內 block 內容應走 HTML 路徑，不能一律把第一列升成欄名 |
| 合併儲存格、巢狀表 | 白名單 HTML：`table/caption/thead/tbody/tfoot/tr/th/td`，保留 `rowspan/colspan/scope`、巢狀歸屬及標準 inline | 已驗合併表、`rowspan="0"`、巢狀表與兩層 sanitizer；不複製來源 CSS、事件或 script | 表內有序清單已加入 `start/value` 白名單；`reversed` 及實際編號仍需端到端回歸，不能因外表正確而漏掉語意 |
| KaTeX 公式 | 讀已有的 `annotation[encoding="application/x-tex"]`，轉 `$…$`／`$$…$$`，由 PCS 本機 KaTeX 顯示 | 一般 Markdown 公式使用既有 renderer；安全 HTML 表內的 TeX 目前保持 literal，未重新解析成公式 | 若要支援表內公式，新增明確且受控的公式節點／回呼；不可把整格 HTML 文字重新當 Markdown 解讀 |
| 科學記號、化學式的上下標 | 保留惰性的 `<sup>/<sub>`，不將 `10⁻³` 或 `H₂O` 攤成相鄰普通數字 | 已有符號、精度與實際閱讀測試；不需要猜測並重寫成 TeX | 複合底數、Unicode 負號、標籤及單位連接關係；若無原公式來源，只保留可見語意 |
| 程式碼、MediaWiki 等原始文字 | 保留 code／pre 邊界、換行及足夠長的 fence；不將其內的管線字元轉成表格 | 已測巢狀表內 pre 換行與 literal 文字；目前擷取仍有整體 NBSP 正常化，不能承諾逐 byte 原文相同 | 增加 NBSP、反引號、`{| … |}`、HTML 字面值回歸；原始下載檔可作逐字對照 |
| `data-d-*` 摘要卡、四格數值、CVSS | 只對已確認結構產生本機固定布局的安全 HTML：二欄配對、2×2 grid、數值／分母與 meter | 已測摘要配對、四格順序、9.2/10 與 reader 保留比例；選擇器來自觀測，不是 OpenAI 公開契約 | 只保留原有分隔／外框；陌生結構退回完整文字順序。新增卡片變體必須有 fixture，不能按數字外觀猜分組 |
| 聊天內 SVG 圖表 | 受限淨化後輸出 PNG；簡單 HTML 軸標籤轉成文字；可讀 table／資料標籤另留結構 | 合成擷取、writer／portable 及實際 Qt 解碼已驗；未保留 hover、篩選或縮放互動 | 新變體核對色彩、字型、clipPath、相對引用、尺寸及 hash；轉圖失敗保留參照，不能偽造空白成功圖 |
| Canvas／已載入圖片 | origin-clean Canvas、同源或合法 CORS 圖片可輸出 PNG；另留真實資料或描述 | PNG 檔案鏈路及實際 Qt 寬窄閱讀已驗；污染 Canvas、表內圖、跨來源 iframe 與動畫互動仍不支援 | 零尺寸、WebGL 畫面、超大圖與新 CORS 變體；不能從像素反推精確數據 |
| 圖表可存取資料 | 讀明確 `aria-label/aria-describedby`、表格標頭及資料列；保留單位、系列名稱、資料順序 | 名稱／描述已有擷取；完整可存取數列轉換仍需具體 adapter | 座標軸刻度、legend 或單一 hover tooltip 不等於完整資料集；只有實際提供的數值可保存 |
| 引用來源與來源卡片 | 保存為標準 Markdown 腳註，PCS 閱讀端提供 hover card；保留可取得的標題、日期、圖片與來源連結 | 合成擷取 → writer → 本機 PNG → portable → Qt 引用標籤／hover card 已通；範圍為一張 80×40 PNG 與兩筆來源 | DOM 有 `href/data-href/data-url` 才保存真 URL；只有按鈕而無 URL 的卡片保留文字、可取像素及回到原對話的連結，不猜原始網址 |
| 互動表格的虛擬列／欄 | 預設保存已取得結構；完整資料優先用使用者取得的官方 CSV，或在同一表格逐視窗累積 | 尚未實作完整虛擬表格掃描；DOM 列數可能只是畫面視窗 | 依 `aria-rowindex/colindex/rowcount/colcount` 核對缺口，包含水平虛擬欄；排序／篩選期間要固定狀態 |
| 長對話的虛擬訊息 | 每個掛載視窗即時擷取，依穩定訊息鍵累積並恢復順序，完成後才形成保存快照 | 現行 `captureLoadedConversation()` 只有 `scope: loaded`；完整掃描是下節方案，尚未實作 | 已卸載訊息不能靠最後一次 query 補回；也不能把累積順序宣稱為站方完整分支圖 |
| 共享小工具、獨立圖表頁 | 專用元件 root adapter，保存目前表格／PNG／來源與顯示狀態 | 已有經授權共享頁 DOM 觀測；現有聊天擷取要求 user／assistant 訊息 root，不能因此宣稱獨立小工具頁已支援 | 頁面類型、元件身分和訊息模式分開；同源元件不應誤判 iframe，真正跨來源 iframe 仍只留來源 |

GFM 表格本身只有單一標頭列，儲存格不容納 block 結構，因此合併表或複雜內容不能靠增加分隔線解決。[GFM 表格規格](https://github.github.com/gfm/#tables-extension-) HTML 規格明確定義 `colspan` 上限 1000、`rowspan` 上限 65534，且 `rowspan=0` 代表延伸至該 row group 剩餘列；不可自行改成 1。[HTML 表格模型](https://html.spec.whatwg.org/multipage/tables.html#attributes-common-to-td-and-th-elements)

## 圖表與共享小工具的可實作路徑

本輪經授權的共享小工具觀測到 Recharts SVG，沒有 iframe；元件選單可切換「以表格形式查看」，其結果是普通 `TABLE`，欄名為 Month／Count，三筆 Count 為 `5045`、`10477`、`10740`。這是同一元件的兩種呈現，不能把 SVG 只保存成「圖表」字樣，也不能把切換後的表格當另一份無關內容。此案例只證明該頁的結構，未證明所有共享小工具相同。

建議按以下順序接入，不讀私有 API 或 cookies：

1. **已有資料表時直接讀結構。** 將 table 綁到同一圖表元件的 identity，保留原始文字與欄列。若只有圖表，先輸出其可讀描述及當前 PNG；不把軸刻度拼成假資料表。
2. **以表格形式查看是顯式轉換。** 在使用者執行保存時，由經該變體測試的 adapter 切換、等待表格穩定、核對欄列後擷取；完成或取消都恢復原模式。不掃描一般選單、任意點擊下載或切換其他小工具。尚無 adapter 的變體可先由使用者切換成表格後保存。
3. **視覺快照和資料分開保存。** 本機 PNG 用於忠實呈現當時畫面；Markdown 表格或使用者提供的 CSV 用於搜尋、複製及數值核對。PNG 不聲稱保留互動性，資料不存在時不經 OCR／像素推算補造。
4. **圖像只留惰性結果。** SVG 只接受繪圖元素、有限本地樣式與內部片段引用，移除 script、事件、外部資源和任意 HTML。Canvas 僅使用允許的讀取 API；失敗回傳明確狀態。保存端驗證 PNG signature、尺寸／大小上限與 hash，寫入受控附件路徑，再由正常 Markdown 圖片路徑顯示。

瀏覽器支援 SVG 作為 image／Canvas 輸入，但 image context 會限制腳本及外部資源；不能把直接嵌入 SVG 文件和安全的靜態圖像混為一談。[MDN SVG image](https://developer.mozilla.org/en-US/docs/Web/SVG/Guides/SVG_as_an_image) Canvas 的 PNG 輸出有標準 API，origin 不乾淨時會拋出 `SecurityError`；這是要處理的錯誤分支，不是圖表一律不可保存的理由。[MDN Canvas toBlob](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toBlob)

## 長對話：社群案例的後續及 PCS 方案

[#282](https://github.com/sho7650/obsidian-AI-exporter/issues/282) 的作者在 2026-07-07 回覆已由 [#338](https://github.com/sho7650/obsidian-AI-exporter/pull/338) 修正並關閉；不能繼續把它當作無解的限制。公開 PR 於 `2026-07-06T23:20:32Z` 合併為 `8be01e86e905cfcfad57494fef2228f8033c4d30`，不是只更新 README。

本輪讀到的實作與測例如下。它們是該專案自身實作的證據和 PCS 的比較案例，不構成 OpenAI DOM 保證，也不代表 PCS 已跑過該專案測試：

| 固定來源 | 實際處理 |
| --- | --- |
| [`scroll-manager.ts:161–195`](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/src/lib/scroll-manager.ts#L161-L195) | 依前後視窗重疊的 key 合併順序；不重複加入相同 key |
| [`scroll-manager.ts:228–323`](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/src/lib/scroll-manager.ts#L228-L323) | 每次往上捲即 harvest，Map 保留最新內容；頂端持續無新資料或逾時後結束 |
| [`chatgpt.ts:143–185`](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/src/content/extractors/chatgpt.ts#L143-L185) | 使用 `data-turn-id`、`data-message-id`，最後才退回內容 hash |
| [`selectors/chatgpt.ts:40–53`](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/src/content/extractors/selectors/chatgpt.ts#L40-L53) | 錨定對話的 `data-scroll-root`，避開外觀相同的側欄捲動區 |
| [`chatgpt-autoscroll.test.ts:107–160`](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/test/extractors/chatgpt-autoscroll.test.ts#L107-L160) | 模擬只掛載三則的八則對話，核對累積順序；關閉自動捲動時只回三則；另讀桌面 DOM fixture |

該 commit 的 [ADR-017](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/docs/adr/017-autoscroll-virtualized-platforms.md) 仍列有桌面自動化與 mweb 版型差異；PR 上人工 smoke 的勾選不能取代 PCS 的實際驗證。專案為 [MIT 授權](https://github.com/sho7650/obsidian-AI-exporter/blob/8be01e86e905cfcfad57494fef2228f8033c4d30/LICENSE)；此處只研究方法，未拷入原碼或新增依賴。

PCS 可採相同累積原理，具體接點為 [`dom.mjs`](../../extensions/chrome/dom.mjs)、[`capture.mjs`](../../extensions/chrome/capture.mjs)、[`background.mjs`](../../extensions/chrome/background.mjs) 和 [`connector_capture.py`](../../prompt_calculus_studio/connector_capture.py)：

1. **開始一次掃描操作。** 使用者執行整段保存後，固定 tab、URL、聊天／分支身分及 operation generation。只定位包含這些訊息的實際捲動 root，記住原捲動位置與焦點；保存操作顯示已讀訊息數並可取消。
2. **每個視窗即取快照。** 用目前擷取器的相同富格式規則，把即將卸載的訊息和資產存進該次操作記憶體；先以重疊視窗覆蓋目前位置至底端，再向上累積到頂端，避免從中間開始時漏掉較新的訊息。不是先捲完再跑一次 query。
3. **身分與順序分開。** 優先使用站方單一 message ID；新 DOM 可用已驗證的 search-unit key 加角色。turn 可能包含多個角色，不可僅用 turn ID 合併整組。用相鄰視窗的重疊關係拼接，沒有重疊時記為缺口並補掃；不要把相同文字的兩則訊息以內容 hash 合併。串流中的內容先等穩定，不能當已完成快照。
4. **完成判定有範圍。** 同時核對上下端、連續視窗無缺口、沒有載入失敗／streaming、停留數輪後內容指紋穩定；若有可靠總數再核數量。`scrollTop=0` 本身不證明整段完整，沒有站方完整性證據時只記「完成此次掃描」。目前 `scope: loaded` 的含義保持不變；若增加 coverage 欄位或 scope，須同時更新 capture 驗證器及正常化契約。
5. **結束與取消都還原。** URL／分支改變立即中止且丟棄本次尚未接受的快照；逾時或達到訊息／位元組上限回傳可續作的部分結果狀態。恢復原捲動位置和焦點，不操作其他聊天。背景重新核對時使用這次不可變快照及掃描 identity，不能再以一個目前 DOM 視窗要求全量完全相等。

最低回歸集合是：真實卸載／重掛的長列表、從中段開始、相同文字不同 ID、同 turn 多訊息、延遲載入、捲動區誘餌、視窗無重疊、切換聊天、取消、逾時、串流尾端與資產上限。先在合成 MV3 頁面驗順序、完整度、還原和取消，再經授權使用一段實際長對話核對首尾及訊息數。官方 ZIP／JSON 匯入仍是已有能力，可取得歷史資料時繼續使用，不要求以未授權內部 API 取代。

虛擬表格採同樣的逐視窗累積，但 identity 是 `(rowindex, colindex)`，必須包含水平欄位、目前排序與篩選狀態；有 `aria-rowcount=-1` 時總數未知，不可推定已完整。WAI-ARIA 明確區分 DOM 目前列數和資料總列數，並定義索引／總數屬性。[W3C Grid and Table Properties](https://www.w3.org/WAI/ARIA/apg/practices/grid-and-table-properties/)

## 證據、驗證與尚未涵蓋的語意

現有測試接點：

- [`connector_chrome_rich.test.mjs`](../../tests/connector_chrome_rich.test.mjs)：上下標、普通／合併／巢狀表、literal 和資產參照。
- [`connector_chrome_components.test.mjs`](../../tests/connector_chrome_components.test.mjs)：摘要配對、CVSS、四格、粗體與引用。
- [`test_markdown_html_tables.py`](../../tests/test_markdown_html_tables.py)：保存重開、惰性安全 HTML、數值／結構保留。
- [`test_markdown_html_table_reader.py`](../../tests/test_markdown_html_table_reader.py)：實際 WebEngine 經兩層 sanitizer 後仍保留 meter／span／scope，來源 bytes、dirty 和 Undo 不變。
- [`test_markdown_table_presentation.py`](../../tests/test_markdown_table_presentation.py)：一般表格在寬窄閱讀區的布局。
- [`test_markdown_inline_png.py`](../../tests/test_markdown_inline_png.py)：實際 Chrome Markdown 匯出器產出的單一 `.md`，直接由 PCS 閱讀內嵌 PNG；不經 Capture JSON 匯入。
- [`connector_chrome_citations.test.mjs`](../../tests/connector_chrome_citations.test.mjs)、[`test_markdown_citations.py`](../../tests/test_markdown_citations.py)：標準來源腳註的合成擷取與閱讀 hover card；另以 `citation-dom-capture` 合成輸出驗證保存、本機圖片、可攜重開與實際 Qt 呈現。

本輪閱讀驗證使用合成 fixture；它能驗證規則和 reader，不等於所有真實 ChatGPT 版型的驗收。資料卡、引用、共享小工具各有不同 root，不能用一張成功圖延伸到全部內容。PNG 五項合成瀏覽器擷取至 writer／portable 重開的檔案檢查已通過，另有 [`test_capture_images.py`](../../tests/test_capture_images.py) 核對格式、取消、原件和恢復。實際 Qt reader 在原保存文件及可攜重開副本、1000／360 寬度解碼五張圖片，29 項檢查與一項 HTML reader 回歸通過；數值表格、圖片原尺寸、來源 bytes、dirty／Undo 狀態保持，沒有水平溢出。引用來源已實作標準腳註與 hover card；`citation-dom-capture` 的實際合成擷取已串接 writer、本機 PNG 資產、portable 重開及 PCS Qt 引用標籤／hover card。範圍為一張 80×40 PNG 與兩筆來源：第一筆沒有 URL，只保留回到原對話的連結；第二筆保留 DOM 真正提供的 `https://example.com/fix?version=002.50`。腳註首行／續行、程式碼及來源範圍邊界測例 16／16 通過；原 JSON 保持不變，重複保存沿用既有結果。此合成全鏈證據不代表真實 ChatGPT 的所有來源卡片或縮圖均成功。

普通 Markdown 出口另有獨立驗證：先前 `markdown_rich_view.resource()` 將 PNG data URI 當成工作區外資源，已補上經 `capture_images.png_bytes()` 完整檢查的分支，只接受有界 PNG，不新增網路讀取或寫入資產。三項窄測試通過；沿用合成擷取的五張圖片，經實際 `core.markdownDocument()` 匯出後直接開啟 `.md`，在 1000／360 寬度皆成功解碼且不溢出，來源 bytes、dirty／Undo 和目錄檔案保持不變。無效、過大或其他 MIME 的 data URI 不顯示成成功圖片，也不把長 Base64 當錯誤標籤。此證據補足單檔 Markdown 路徑，不能用 JSON 匯入／portable 成功替代；真實共享 URL、其他網站圖表與互動性仍有上表界線。

社群的 [MediaWiki Copy 問題](https://community.openai.com/t/chatgpts-copy-button-corrupts-mediawiki-tables/1381986) 提供可重現測例：同內容以複製按鈕與 `.txt` 檔輸出產生不同格式，後續官方支援回覆建議下載原始文字檔。這不能證明所有複製都壞，但足以要求測試 literal 表格語法，不能把網站 Copy 當無條件正確的原文來源。KaTeX 則有公開的 TeX 輸入／render API，可沿 PCS 既有公式路徑處理，不需要逆推畫面字形。[KaTeX API](https://katex.org/docs/api.html)
