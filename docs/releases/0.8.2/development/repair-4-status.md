# 0.8.2 Alpha 1 Repair 4：隔離候選，尚未整批驗收

2026-09-15，基於保留的 4891465，新 worktree 修復 API 重導、來源包邊界、圖片來源、手寫 owner 保護、工作流刪除殘留及復原原子性；改為完整包驗證及新目錄部署。7b6a9b8 空來源回歸已由04／05／06／08按各自範圍複驗通過。其後使用者於00核准02 ADR aada3d5，已補原CLIP選擇的可選刪除日誌映射、舊紀錄及衝突處理；新固定增量仍需獨立驗收，不沿用前候選通過結論。新包及回復演練證據以 build/082_DELIVERY.json 為準，實機與獨立複驗另列。說明見 [082_REPAIR4.md](../repairs/repair-4.md)，技術必要性盤點見 [082_REPAIR4_INVENTORY.md](../validation/repair-4-inventory.md)。舊包、舊入口、主倉庫及私人資料未修改。
