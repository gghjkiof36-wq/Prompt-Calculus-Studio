# R9：公式、貼上與保存恢復

日期：2026-10-24。基準 `1798054c63ea4e085b7b2988dbe68d3b86648c0c`，Chrome 0.2.5 → 0.2.6。本輪為本機試用修正，不是公開發布或完整相容性宣告。[使用說明](../guide/markdown-compatibility.md)

## 本輪優先順序

使用者要求先解決常見 Markdown／LLM 內容：數學與物理符號、精確數值、連結與縮圖、文字行距與間距，以及保存卡住。較難的網頁表格、完整圖表、互動小工具、長對話逐視窗取得延後；已有能力保留，不為接近外觀而改造整條保存格式。

## 修改接點與原因

| 接點 | 修改與保留契約 |
| --- | --- |
| `extensions/chrome/inpage.mjs`、`background.mjs` | Native 回覆將 UUID 正規化成 32 字元，原本覆蓋前端 36 字元 ID，導致 finally 與 polling 身分檢查失敗。保留前端請求身分、查詢去重；保存不鎖住下載，取消立即退出選取，晚回不復活。30 秒後保留可查詢結果，不自動重送 |
| `extensions/chrome/capture.mjs` | 正文語意 SVG 小圖示使用既有 PNG 路徑，保留顯示尺寸；排除按鈕／工具列／favicon／連結裝飾／公式內 SVG。取得既有 TeX annotation，避免視覺與隱藏數學重複。移除整體 NBSP 正常化，數字不轉浮點 |
| `markdown_math.py`、`markdown_render.py`、`markdown_syntax.py` | 共用四種公式分隔符；程式碼不渲染。引用／清單中的 block math 去掉 Markdown 容器前綴，保留矩陣換行。處理段首 `$2$ = 2` 的邊界，保留常見金額；不修改原公式或文件 bytes |
| `markdown_html.py`、`markdown_table_cells.py` | 文字型 TeX 不重複跳脫；表格的 pipe 轉義保留 `\|x\|` 與公式欄界。HTML 上下標／mark、合法有界 PNG、實際文章連結保留；未知 visual 留下可取得描述與來源 |
| `markdown_paste.py`、`markdown_paste_job.py`、`markdown_view.py` | HTML 空白但純文字有內容時使用純文字，大小界限與同步／背景路徑一致。失敗不改原件；沿用 read-only、IME、stale owner、Undo／Redo 契約 |
| task-list projection、`assets/markdown/reader.js` | 只讓 plugin 實際新增的 token 產生 checkbox；原始 HTML input 仍跳脫。DOMPurify 明確允許非 URI 的 boolean checked／disabled，避免第二層把唯讀與勾選狀態剝除 |
| `markdown_render.py`、`markdown_rich_view.py` | 遠端縮圖的手動載入動作移至文章連結外，避免巢狀 anchor 讓瀏覽器拆斷後面的文章標題；data PNG、文章 URL、縮圖來源分別保留，無新增自動下載 |
| `markdown_html.py`、`markdown_view.py` | 同時有 bitmap、空白文字及有效「文章連結包圖片」HTML 時，沿用 HTML 轉換保留文章 URL；空白、破損或不可用的連結不搶走既有 bitmap 貼上。沒有新增圖片儲存或下載管線 |

文字外觀的窄修另補明確空 divider 元件及巢狀粗體去重。`r9-reader-spacing-final/receipt.json` 核對實際 reader 為 16px、150%／24px 行高、段落 8／14px，與既有偏好一致，沒有改使用者字體。離屏 QFontDatabase 無 Windows 字型，截圖可能使用替代字型，不能把它當成原生 Windows 字型對照。

沒有加入另一個 capture controller、observer 或 writer；保存、下載仍在既有入口重新驗證對話身分。BAT 仍有工作區資料鎖，重複啟動不會同時寫入同一資料目錄。

最終擴充回歸另發現 Escape 焦點競態：render 替換按鈕後、下一幀焦點還原前，焦點會短暫落在 BODY，原判斷誤關整個選單。改由已開啟的最內層子選單決定關閉層級；左鍵仍依焦點範圍，選單已關閉時不攔截頁面 Escape。新增固定暫緩下一幀的回歸，沒有增加重試或放寬等待時間。

## 測試資料與已驗證範圍

`tools/markdown_compatibility_sample.py` 產生 `tests/fixtures/markdown_compatibility/`，含一份主 Markdown、輔助筆記、96×48 圖片與 69 組公式目錄。完整文件另有行內／容器／邊界控制組，不把「69 組」說成所有公式語法。

本機收據位於 `qa-private/03-chrome-transfer-20261023/`：

- `r9-save-before-fix.json`／PNG：以舊 content bundle 與真實 32 字元回覆重現保存卡住，已 committed 仍未 polling。
- `r9-chrome-full-regression.txt`：12 份相關 Chrome 測試 109/109、0 略過，81.268 秒；真正 MV3／runtime messaging，網站與 Native port 是合成資料。此為後續外觀調整前基準，最後來源以交付 STATUS／新回條為準。
- `r9-chrome-final-fixed-regression.txt` 及同名前綴回條：最終 12 份 111/111、0 略過，81.030 秒；測試前後來源雜湊相同，bundle 檢查通過。首次 final 110 項中 1 項失敗保留在 `r9-chrome-final-regression.txt`，後續以暫緩焦點還原重現並修復 Escape 競態；沒有把首次失敗改寫為工具故障。
- `r9-math-clipboard-final.txt`：10 模組 84/84，20.756 秒；包括五項實際 KaTeX reader、69 組逐式 annotation、數字／表格公式、貼上圖片、保存重開、Undo／Redo、唯讀／IME／背景貼上及原有文件契約。Qt 使用 offscreen；GPU 診斷訊息保留，未當成原生 GPU 驗收。
- `r9-python-final.txt`：縮圖連結修正後的 15 模組 113/113，27.611 秒；加驗圖片完整性／保存、單檔內嵌 PNG、HTML 表格閱讀與引用懸停卡片。以上 84 項包含在此範圍，不能重複相加。
- `r9-python-multimime-final.txt`：補齊多 MIME 連結縮圖入口後的最終 15 模組 114/114，25.405 秒；包含上述 113 項。先重現 bitmap 優先造成文章 href 丟失，再驗有效連結、無效 HTML 回 bitmap、唯讀、單次 Undo／Redo 與保存重開，未擴大成全面 HTML 優先。
- `r9-content-reader-final/receipt.json`：50/50；三個 18px 圖示的 writer → 本地 PNG → 寬窄閱讀、修復副本 30 組公式、完整 fixture 93 個公式節點／4 圖片／2 表格／2 嵌入／Mermaid，以及原件／dirty／Undo 不變。
- `r9-task-reader-final.txt`：實際 reader 的 checked／unchecked／disabled 回歸；初次第二層 sanitizer 丟狀態的失敗收據仍保留於 `r9-content-reader/`。
- `r9-test-documents-receipt.json`：試用目錄的測試資料夾與 ZIP 完整性；原 fixture 不變。

原使用者 `01.md` 已由舊貼上轉換留下額外跳脫，不能只更新 renderer 就假裝它是正確原文。此次依確認的舊格式另建 `01-公式修復副本.md`，修復 30 個區段；原件未改。腳本和 hash 在 `r9-legacy-math-copy-receipt.json`，不把它套用到所有筆記。

上述是不同範圍的證據，不相加為全產品通過數。未把合成圖示截圖當作真實 Chrome 外觀一比一驗收；該截圖只證明圖片資料鏈路。

## 本輪仍有的界線

- PCS 使用內附 KaTeX 0.19.0，Obsidian 使用 MathJax；共同語法以 `$…$`／`$$…$$` 優先。額外 LaTeX 套件、自訂巨集與 Obsidian 外掛不是本輪保證。
- 未執行 Obsidian 實際程式全流程；已對照官方核心語法，並驗 PCS 解析、閱讀及原文保存。未將此稱為完全適配。
- 圖示存在不代表原頁兩欄卡片、線條與比例完全保留。擴大到 HTML 圖片、本地化、可攜包的完整布局路徑先延後，避免外觀改動破壞資料流。
- 一般貼上只有 SVG／Canvas 外觀、沒有 TeX 或可讀原始資料時，不推測公式或數列。保留來源與缺失說明；擴充的既有靜態 PNG 不保留互動。
- 遠端圖片維持明確載入；跨來源不可讀像素、無 href 的來源按鈕不能保證得到原文章網址。只保存真實取得的連結，不猜網址。
- 極長對話／虛擬表格仍只取得已載入範圍；擴充選單已有範圍提示，未宣稱完整對話。
- 複製內容跨多個節點才形成一條公式、特殊 MathML 沒有 TeX annotation、歧義貨幣等仍需要具體案例；字面美元可用 `\$`。
- 未公開發布、未替換正式安裝；本機 package 與 BAT 的固定來源及啟用狀態記於交接 STATUS。
