# 分頁工作流的任務保存與排程

適用本輪 0.8.9 本機修復。可攜文件格式維持不變；舊完整資料庫導入與真實 ComfyUI／GPU 驗收仍屬各自範圍。

## 保存邊界

- `data/execution-index.sqlite3` 保存文件路徑、document ID、已保存 receipt、固定 runtime token、Save As identity mapping 與 FIFO 位置。
- `data/documents/<token>/studio.sqlite3` 保存該 owner 的狀態、來源游標、generation jobs、Stage／Queue 紀錄與原生操作回執；圖片副本也留在該目錄。
- 路徑、文件 ID 與已保存內容雜湊相符時沿用 runtime。一般保存換 inode 不換 owner；外部複製到另一位置不繼承任務。外部修改檔案會建立新 context，原有未知任務仍以舊 owner 核對。
- 文件未變時，以本機狀態恢復已接受的來源游標及未輸出到文件的內容。不能用較舊文件覆寫 journal 交易保存的游標。SQLite 保存成功後才輸出文件，成功輸出後才更新配對 receipt。
- Save As 保留舊 executor，建立新 token。當次 native Undo 使用的 runtime IDs 與新文件 IDs 的 mapping 一併保存。內容與原檔相同時，重開原檔重用舊 executor；有未保存內容時，舊 owner 標記為 recovery-only，原檔從磁碟新建內容 owner，避免把副本草稿寫回原件。舊任務仍用原 journal、凍結內容與游標完成對帳。
- 重新命名／資料夾移動更新已開啟與未開啟 owner 的路徑；失敗時沿文件操作的 rollback 邊界還原。索引／任務資料損壞會阻止新提交，原紀錄保留。

## 單一執行順序

CLIP／圖片節點綁定與 Stage 參數頁從文件的 `ComfyClient.workflow_catalog` 取得工作流，無須建立設定頁。目錄按執行 client 延後建立一次；同文件多視圖共用，不同文件各自保有讀取身分及綁定草稿。一般設定頁的工作流管理也使用自己的 client 目錄。原生讀取仍通過 `WorkspaceComfyClient` 的排程檢查，不能借工具頁 client 繞過文件的執行權。

WorkspaceScheduler 以 runtime token 持有一次執行權。Stage 接受輸入與來源游標的 transaction 完成後才登記順序、註冊 library 或派送。不同文件依序執行，同文件的多個視圖共享同一個 executor。

Stage pump、輸入套用與 Queue／Direct dispatch 在取出項目之前取得執行權；WorkspaceComfyClient 的 transport 再核對變更 ComfyUI 的操作。查看／切換原生工作流和取得 Queue 快照也屬受保護操作。執行期間 library 固定到 owner，不隨分頁焦點切換。

收到 queued 回執、按取消、斷線、關分頁或另存均不代表執行結束。`generation_jobs`、`work_queue`、已送出 input apply 與輔助原生操作只要仍待確認，就保留執行權。確定未送出的本機拒絕或明確後端拒絕不產生未知任務。

重開時掃描全部 owner，包括沒有恢復分頁的文件。用原 operation／prompt ID 查詢結果；從不因恢復而重送。尚未執行的 Stage 保留並暫停，需明確繼續。多份歷史未知任務可以同時查證，但查證前不開始新的執行。

「任務紀錄」集中列出工作區文件及另存前任務，可查詢或停止後續流程。已分離的紀錄不提供繼續／重試入口，避免從舊內容發起新任務；重新執行由開啟的文件明確開始。

## 驗證

`tests/test_workspace_execution.py` 使用共用的合成原生回執／佇列服務，經實際 Qt 執行控制與正式 journal／Stage 引擎，檢查多文件順序、多步流程、輸入凍結、取消、回覆遺失、無分頁恢復、Save As、路徑更名及保存失敗。`test_workspace_connection.py` 使用隔離的本機 HTTP peer 檢查實際 Qt 連線傳輸與燈號。這些不等於真實 ComfyUI、GPU 或使用者桌面驗收。

`test_workspace_binding.py` 直接經分頁 Canvas 的綁定按鈕驗證節點選擇、復原／重做、保存及重開，並檢查圖片綁定、Stage 參數套用、同文件共用目錄及不同文件的取消／延遲回覆隔離。`test_workflow_picker.py` 保留舊介面的工作流讀取、過期連線及原生／已儲存版本區分。
