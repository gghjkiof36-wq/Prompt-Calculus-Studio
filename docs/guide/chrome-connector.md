# 從 Chrome 保存 ChatGPT 對話

適用 [0.8.10 Pre-alpha1](../releases/0.8.10/pre-alpha.1.md)，Windows、Chrome 116 以上，配套擴充為 0.2.7。

## 安裝配套

1. 從 [Pre-alpha1 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.1) 下載 `PCS-v0.8.10-Pre-alpha1-Chrome-Connector.zip` 並完整解壓。
2. 將裡面的 `PCS-Chrome-Connector` 資料夾改名為 `connector-package`，放在 PCS 的 `run.py` 旁。保留其內的 `chrome`、連線工具與其他檔案。
3. 啟動 PCS，在「探索 → 下載紀錄」按「加入 Chrome」，再按「連接 PCS」。這會安裝目前 Windows 帳戶的連線工具，並啟用此工作區的接收服務。
4. 按「開啟擴充頁」，在 Chrome 啟用「開發人員模式」，選「載入未封裝項目」，載入配套內的 `chrome` 資料夾；可用「開啟擴充資料夾」找到位置。
5. 重新整理 ChatGPT 對話頁面，並保持 PCS 開啟。更新既有擴充時，在 Chrome 擴充頁重新載入後，再重新整理對話頁。

連線工具與主程式分開下載。只想下載 JSON／Markdown 時，可直接載入來源包 `extensions/chrome` 的擴充，不必先連接 PCS。

## 保存訊息

單則訊息：點該則複製／分享列旁的「PCS」，移到或點選「儲存到 PCS」，再點目的資料夾。也可選「下載 JSON」或「下載 Markdown」。

多則訊息：從頁面右上角「PCS」選「選取訊息」或「選取全部」，勾選訊息後按「儲存到 PCS」，選擇資料夾；也可直接下載。等待串流回覆完成後再保存。

PCS 的「下載紀錄」可搜尋並開啟文件或所在資料夾。移除紀錄不會刪除文件。若保存逾時，先查紀錄，確認是否已完成後再決定是否重試。

## 擷取範圍

只處理目前網頁已載入的訊息；長對話請先載入需要的段落。「全選」不會補抓網站尚未載入的歷史。圖片與圖表依可取得的內容保存，互動功能不會一起帶入；不支援的內容可能只留下文字或來源連結。

JSON、Markdown 與附件仍可能包含私人內容，分享前請檢查。[資料與隱私](data-backup-privacy.md) · [筆記保存與可攜匯出](markdown-retention.md)
