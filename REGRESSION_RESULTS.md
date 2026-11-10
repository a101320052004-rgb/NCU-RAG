# 回歸驗證結果

## 已實際執行

- Python：35 個測試方法通過，含多組 subTest；原始 H01–H08 均為 handoff，RAG／Claude 不被呼叫。
- SQL：8 組測試在真實 PostgreSQL WASM 引擎執行，包含 migration 重跑、F05–F08、統一資格、secret／核准原子性、版本保存、隔離／回復、核准本文變更失效、ARC 殘片、FAQ 獨立性與 RLS。引擎為 PGlite 0.5.16 / PostgreSQL 18.6 / pgvector 0.8.7，**不是正式 17.11 / 0.8.2 或 PostgREST**。
- 真實向量：既有 multilingual-e5-small ONNX，384 維、query/passage 前綴與正規化保持原程式。模型檔 hash、runtime、固定快照及原 Excel hash 記於 `repair_evidence/real_retrieval_results.json`。
- 原始問答、評分、診斷成果都未覆寫。沒有 Claude 或正式資料庫呼叫。

## 指標與界線

| 檢查 | 修復前 | 修復後 | 意義 |
|---|---:|---:|---|
| H01–H08 固定 handoff（初始 1640832 快照） | 2/8 | 8/8 | 原始題句的實際路由；不等於全部轉介內容評分 |
| 工作許可相關來源覆蓋 | 9/24 | 24/24 | 四種問法 × None/五種身分；不是工作資格或完整答案正確率 |
| 工作許可查詢入選無關段落 | 109 | 0 | 依明確工作許可詞與來源內容檢查；仍需生成驗收 |
| 適用這份手冊的英文費率表完整覆蓋 | 0/8 | 8/8 | 四種英文問法 × None/外籍學位生，三段完整本文覆蓋 |
| 同一手冊的中文費率表完整覆蓋 | 0/2 | 2/2 | 泛用中文費用問法使用同意圖英文擴充分支，原中文仍查詢 |
| 非修復目標題主要段落 | 基準 | 41/41 相同 | 主要來源未退步；上下文因移除跨 FAQ／不合格鄰段而有 30 題縮減，需生成比對 |
| 已知舊規則／ARC 殘片進入修復後上下文 | — | 0 | 按本次已知規則與指定殘片檢查，非所有法規的通用正確性保證 |

費率共跑 30 組。其餘 20 組身分的 metadata 不允許使用這份「外籍學位生」手冊 golden，因此不列入此指標分母，也沒有取消身分過濾。這不代表其餘身分的費率資訊已完整。

47 題非安全題及 8 題安全路由涵蓋原先 55 個已測題號，但**沒有重新生成／評分 55 份回答**。I01/I02 已取得工作許可相關官方段落；不同身分是否可申請，仍須來源與生成答案驗收。F03 在未核准完整現行期限資料時停止生成並轉介。

q2–q6 捕獲候選重播仍各由 0/3 → 3/3；擴充字串凍結為原捕獲版本，只驗證候選融合／排序。新擴充分支以真實向量測試另行驗證，沒有捏造新 query vectors。

## 重跑方式

```text
python -m unittest discover -s tests -v
npm ci --prefix tests/sql_engine --ignore-scripts
node tests/sql_engine/engine.mjs <repo> <私有診斷包目錄> checks
python -m pip install -r tests/requirements-retrieval.txt
python tests/real_retrieval_regression.py --package <私有診斷包目錄> --workbook <原始Excel> --cache-dir <repo外的私有快取>
python tests/replay_captured.py --package <私有診斷包目錄>
```

首次下載原模型；不換正式模型。原始包、Excel、模型／向量快取與 node_modules 均不上 Git。新向量由遮罩後快照重建，屬本次控制重跑，不能冒稱歷史正式檢索。沒有量測正式延遲或 Claude 答案通過率。
