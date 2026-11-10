# NCU RAG 修復摘要

本機 Git CLI 已確認基準 main `164083292fa518e85ee2073a198dbb7ecbde9d7c` 與診斷快照的關鍵程式一致（只差 CRLF/LF），修復位於 `fix/rag-reliability`。第一版 PR #1 已由儲存庫擁有者合併為 c91ef22，本輪透過後續 Draft PR 交付；我未把修復分支合併進 main。未修改正式 Supabase、未合併 main、未執行 Vercel 部署。

## 工程修改

- H01–H08 原始安全題句全部固定 handoff，不呼叫 RAG／Claude。這輪新增 H03/H04 英文心理求助及 H05/H06 嚴重宿舍衝突的情境式辨識，保留否定、一般詢問及教育引用反例。
- 工作許可同義詞在擴充分支正規化，仍保留原始查詢分支；只讓工作許可相關段落入選，排除醫療授權、行事曆等同字不同義資料。身分參數保留。
- 住宿費問題保留費率段落及原始意圖。繳費方式 G03、尚未公布的未來費率 U01 不套用費率表救援排序；K=5、距離 .30、每文件兩段不變。
- 主檢索、鄰段、校名捷徑及本機 Chroma 套用一致資格。SQL/Python 同時阻擋過期、待審核、錯誤身分及已知舊法規；抓取日期不能作政策人工確認。
- 補上切塊漏洞：ARC 舊文件的「15 天」殘片即使本段缺少入境句，也會受相鄰爬蟲段落檢查；獨立人工 FAQ 不繼承另一題政策，也不再跨題補鄰段。
- F03 類 ARC 期限問題需有完整、合格的期限依據；不足時直接 no_data／承辦轉介，不交給 Claude 從問句的「15 天」猜答。這是安全拒答，不宣稱已補完現行法規答案。
- 人工核准綁定完整本文 hash 與確認日期。孤立的期限殘片需先整理成完整陳述；SQL 保留歷史版本、封存、隔離及 CAS 回復機制。

## 已執行

35 項 Python 測試方法、8 組 PostgreSQL 記憶體引擎測試通過。以同一 E5 ONNX 權重、1,195 段固定快照及實際 pgvector，比較 Excel 的 47 題非安全題，另外完成 8 題原句安全路由，以及中英文費用／四種工作許可問法與五種身分組合。

詳見 `REGRESSION_RESULTS.md`；這些不是 Claude 答案通過率。原始 55 題、Excel、ZIP、三份診斷成果與 35 份快照程式 hash 未變。

## SQL 發布與回復（全部尚未套用正式 DB）

1. 先在與正式相同版本的 PostgreSQL 17.11／pgvector 0.8.2 克隆及 PostgREST 驗收 `sql/001_reliability_v2.sql`。原 RPC 保留，新 Python 需要 v2。
2. 人工審查 `002_quarantine_obsolete.sql` 的 live preflight。這輪以 effective policy rule 包含切塊殘片；不能直接把原先字面掃描 6 舊／7 新的數量當作正式變更清單。
3. 核准後以獨立批次隔離舊資料；`002_rollback_quarantine.sql` 只回復本文／run 未變的列，保留稽核。不能同時回退到不安全 reader 並重新啟用舊法規。
4. 官方現行資料由人確認後，才使用 `004_approve_policy_template.sql`。缺少完整依據的段落需整理／重切並重算受影響向量，不能機械替換數字。

README 說明 push 不自動更新正式網站，Repository 無部署 workflow／自訂 hook；未直接查核 Vercel 遠端管理設定。PR 維持 Draft。
