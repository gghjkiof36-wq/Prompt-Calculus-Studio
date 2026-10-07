# 0.8.4 Alpha 1 公開來源對照

2026-10-01 對外版本統一採分段格式：本輪成果的舊稱0.831、0.84對應 **0.8.4 Alpha 1**。完整歷史對照見 [版本規則](../README.md)。本次只更新公開來源的當前版本身份、文件與封裝，功能、資料格式及內部工作紀錄維持原樣。

- 功能基準 `ea7df89dc714fc83c7e11ca32e5c7655619ec8a0`；原0.84公開提交 `8b123f77087a521c32737a73ffcc2a6ec8ba5465`。本次接續此公開歷史，沒有覆寫舊提交或推送全部本機開發祖先。
- 程式差異僅 `prompt_studio/releases.py` 的 CURRENT、版本、launcher及相容別名，與 `run.py` 的新啟動參數。新建置參數為 `--v0.8.4-alpha.1`，新啟動參數為 `--v0.8.4-alpha`；舊 `--v084-alpha1`、`--v084-alpha` 及前次別名仍可使用。歷史版本登記保持舊值供舊包相容；資料目錄、節點ID與序列化欄位不改。
- ComfyUI程式與22個共享核心維持前公開提交內容。版本相容檢查涵蓋舊包release值及0.11.1／0.20.2多位數版本識別，沒有新增產品功能或真GPU驗收。
- 來源由Git白名單收集，ZIP根目錄包含run.py；排除個人資料、憑證、模型、Git歷史、qa、vendor及第三方runtime。擴充附完全相同的來源ZIP與154項runtime來源映射。
- `SOURCE_MANIFEST.json` 描述來源，`PACKAGE_MANIFEST.json` 描述交付；`BUILD_INFO.json` 的git_head／product_git_head記本次來源，functional_baseline_git_head記既有功能基準，previous_public_git_head記8b123f7。來源和擴充未附EXE，不宣稱binary重建。
- 新0.8.1／0.8.2／0.8.3歷史標籤直接指向原公開提交，附件ZIP與原包逐位元組相同。當時包內的舊版號保留，對外名稱透過RELEASE_IDENTITY明確對照；不假裝重新驗收或重建過往版本。

[第三方核對](third-party.md)仍是原ea7df89 Windows候選的實際稽核，保留原ZIP／EXE雜湊。內部本機0.84後續交付不由本次公開更名重新編譯，也不取代該歷史稽核。Windows未公開，完整runtime通知／對應來源／重建替換材料仍待補。

既有功能驗收與EXE證據見 [驗收範圍](validation/alpha.1.md)。此次核對只涵蓋版本、公開文件、來源／套件及下載一致性。
