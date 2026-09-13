# ComfyUI 整合

Prompt Studio 擴充提供側邊欄、文字欄綁定、模組快照與收藏。公開版本不新增工作流節點，也不安裝 Qt 或模型。先確認你的 ComfyUI 工作流本身能正常生成。

## 從原始碼建立擴充

在 Prompt Studio 專案根目錄執行。以下沿用桌面安裝指南建立的 `.venv`；擴充打包本身只需 Python 標準函式庫。

```powershell
New-Item -ItemType Directory -Force release | Out-Null
.\.venv\Scripts\python.exe build_comfyui.py
```

先建立 `release` 是必要步驟，目前打包程式不會自動建立它。執行後產生：

- `build/comfyui/comfyui_prompt_studio`：可安裝的完整資料夾。
- `release/PromptStudio-ComfyUI.zip`：本機建立的擴充 ZIP。

打包會加入桌面共用核心。請使用 **build 下的完整資料夾**；專案根目錄同名資料夾缺少打包時加入的共用檔案，直接複製會無法載入。

## 安裝到 ComfyUI

1. 關閉 ComfyUI，找到你正在使用、包含 `main.py` 與 `custom_nodes` 的 ComfyUI 目錄。
2. 將完整的 `build/comfyui/comfyui_prompt_studio` 複製到該目錄的 `custom_nodes`。最終路徑應為 `custom_nodes/comfyui_prompt_studio/__init__.py`，不要多套一層資料夾。
3. 已有舊擴充時，先備份並移出 `custom_nodes`，再放入新版，避免同時載入兩份。
4. 重新啟動 ComfyUI 後端，再重新整理網頁。

擴充目前只接受本機連線。官方 ComfyUI Desktop 的安裝位置與擴充相容性尚未完成實機驗證，此指南以可自行管理 `custom_nodes` 的本機安裝為準。

## 第一次綁定

1. 至少啟動過一次 Prompt Studio 桌面程式，建立素材庫。
2. 開啟 ComfyUI 側邊欄的 **Prompt Studio**，在「連接與資料設定」選擇含 `studio.sqlite3` 的桌面資料夾。依本文件的原始碼安裝方式，預設是專案根目錄的 `data`。
3. 載入工作流，選擇最外層可直接編輯、具有 `text` 欄位的節點，按「綁定／切換」。
4. 若原文字與組合不同，會先保留成手動版本。確認不需要後，才按「清除手動內容」改用模組組合。
5. 選取素材或加入臨時片段，綁定欄位即會更新。保存工作流，以保存綁定與組合。

素材庫連接為唯讀，側邊欄使用時桌面程式不必持續開啟。多個文字節點可各自保存組合，側邊欄只編輯目前選中的節點。第一版不支援子圖內文字欄；若文字由上游連線供應，請改綁可直接編輯的上游 `text` 欄或自行解除連線。

## 從桌面送出生成

1. 在 ComfyUI 載入並綁定欲使用的工作流，按「交給桌面版控制」。
2. 回到 Prompt Studio，按「連接 ComfyUI」，輸入實際本機網址，例如 `http://127.0.0.1:8188`。
3. 確認顯示已連線及目標後，調整 Prompt、設定運行次數，再按「運行」。
4. 在「最近生成」查看結果，先選收藏位置，再收藏喜歡的圖片。

生成期間需保持接受控制的 ComfyUI 網頁開啟。同一時間由一個分頁接受桌面控制；關閉分頁或失去連線會暫停控制。提交結果不明時先檢查 ComfyUI 歷史與佇列，程式不會自動重送生成。

模型、採樣器及 Seed 行為沿用工作流設定，桌面工作區的參數建議不會自動改寫這些欄位。停止按鈕用於中斷生成，其右鍵選單可連同待執行佇列一起清除。

## 收藏與快照

側邊欄「挑圖收藏」顯示最近的 PNG 結果，可為工作區指定目的地。收藏複製 output／temp 中的原始 PNG，同名自動編號；不以預覽縮圖取代原檔。來源消失或目的地失效時會顯示錯誤。

標準 Save Image／Preview Image 流程可保存模組快照；是否存在取決於實際輸出。ComfyUI 關閉 metadata、第三方節點未保存或舊圖片缺資料時，不會補寫現在的 Prompt 冒充原始資訊。若生成命中快取而沒有新 PNG，舊圖仍對應原先生成它的任務。

將含快照的圖片加入桌面圖片庫，可使用「恢復這次模組組合」。恢復會先備份目前狀態，歷史素材以快照內容保留；這不會安裝缺少的模型或還原完整 ComfyUI 環境。

## 資料與移除

- 綁定與組合保存在工作流文字節點的 `properties.prompt_studio`。
- PNG 的 `prompt_studio` 欄位保存模組快照。
- 擴充設定、任務及收藏紀錄保存在 ComfyUI 的 `user/prompt_studio`。

移除前先保存工作流，再關閉 ComfyUI 並移出擴充資料夾。文字欄本身保有真正的 Prompt，可繼續生成。更新或移除前也請備份擴充資料。

本機打包指令目前沒有自動附上授權與來源文件。自行散布安裝包前，請依 [授權說明](https://github.com/gghjkiof36-wq/modular-prompt-manager/blob/main/docs/LICENSING.md) 準備完整 LICENSE、對應原始碼及第三方聲明；此指令不代表正式發布流程已完成。
