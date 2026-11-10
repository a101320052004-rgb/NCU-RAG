# NCU RAG 修復工作副本

2026-11-10。已使用本機 Git CLI Fetch 並確認基準 `main` = `164083292fa518e85ee2073a198dbb7ecbde9d7c`，在 `fix/rag-reliability` 整合修復。已比對關鍵 10 個程式：檢索／路由／入口與部署快照一致，匯入／設定／DB／Embedding 與電腦版快照一致；只正規化 CRLF/LF，未混用替換 config、DB 或 Embedding。版本證據見 `repair_evidence/version_comparison.json`。正式部署及 Supabase 均未變更。

## 已準備的工程修改

| 項目 | 修改及隔離驗證 | 狀態 |
|---|---|---|
| P0-1 | `05_程式碼/app/router.py` 辨識 H07/H08 的 hurt/hurting myself；否定句與明確教育引用排除；真實第三人稱風險轉人工 | 本機測試通過 |
| P0-2 | 共用 `knowledge_policy.py` 阻擋已知 ARC 15 天、工作許可最長 6 個月陳述；新版法規陳述需明確人工確認日期及完整本文 SHA256。混合新舊陳述不能靠核准繞過。抓取日期不作人工法規確認 | Python 通過；SQL 未執行 |
| P0-3 | 主檢索、鄰段與校名捷徑皆傳身分、主題、期限；SQL v2 與 Python 防線同時檢查 active、有效期限、驗證日期及法規審核 | Python mock 通過；真實 RPC 待驗收 |
| P1-1 | 僅對住宿費意圖，優先保留含金額及住宿計費單位的合格段落，以原句排名保留原始意圖；原 manual 加權、每文件兩段、K=5、距離 .30 均保留 | q2–q6 快照重播通過 |
| P1-2 | permission to work、employment authorization、authorisation to work 補相同中文關鍵詞，保留原問句及身分過濾 | 字串與參數測試通過；新向量排名待驗收 |

`rag.py` 同時保留 ISO 日期與本地整數日期的來源顯示，避免新 RPC metadata 造成日期變成「未知」。沒有更換模型、向量資料庫或 UI。

## SQL 與資料變更順序（尚未執行）

1. 在隔離 PostgreSQL 17／pgvector 克隆套用 `sql/001_reliability_v2.sql`，執行 `003_isolated_checks.sql`。新 v2 RPC 不覆蓋原 RPC。新增政策審核表及私有版本歷史；既有 writer 更新／刪除時先保存完整舊列。finish v2 封存舊批次，不刪內容。
2. 審查即時唯讀 preflight 清單；快照掃描有 6 段舊規定字面命中、7 段新版規定待人工確認，並非宣稱這 13 段均為已確認錯誤。清單見 `repair_evidence/captured_replay.json`。
3. 經人工核准後才以 `002_quarantine_obsolete.sql` 將即時確認的舊規定 active 改 needs_review，保留正文與批次／hash 稽核；回復方式在 `002_rollback_quarantine.sql`。不得將遮罩匯出文字的 hash 當成正式資料 hash。
4. 人工核實最新版來源、身分及適用條件，以 `004_approve_policy_template.sql` 對精確 live body 核准；不能自動改寫 15→30 或 6 個月→1 年。人工 Markdown 若以 metadata 輸入核准，hash 必須是含標題的最終單一段落本文；多段落不能共用一個 hash。
5. 確認 v2 SQL 可用後，才在隔離／預覽環境驗收 Python。新程式不靜默回退舊 RPC。上傳工具先檢查 v2 版本，避免先動資料再發現版本不相容。

回復 Python 排序／路由可獨立撤回 Commit；不要同時退回不安全的舊檢索與重新啟用舊法規。審核與歷史表保留，不以 DROP／DELETE 清掉稽核。

## GitHub 與部署檢查

以本機 Git 的既有授權 Fetch／Push，不使用 GitHub Connector。已確認可 Push、預設分支 main；建立修復分支，提交程式與測試、文件與證據兩批 Commit。PR 應維持 Draft，待隔離 SQL 及必要回歸驗收後再決定是否合併。

Repository 沒有 .github workflow 或自訂 Git hook；現有 README 明載正式部署由組長處理，push 不自動更新正式網址。Vercel 遠端 Project Git 設定因非管理員未直接核實；本次只推修復分支，不操作 Vercel CLI 或正式分支。

安全檢查不包含原始診斷包、55 題回答或機密檔案上傳。修復文件、合成 fixture 測試、官方 chunk ID／hash 與聚合指標才列入 Commit；敏感資料完整保存於既有本機位置。

既有診斷三份成果、來源快照及原始 55 題紀錄保持原樣；測試結果另存。完整驗證與未完成項目分別見 `REGRESSION_RESULTS.md`、`KNOWN_LIMITATIONS.md`。本次 SQL 不變更原 RPC，但新程式必須先在隔離環境完成 v2 migration 驗收，不能直接部署到仍只有舊 RPC 的服務。
