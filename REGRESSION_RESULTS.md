# 隔離回歸結果

2026-11-10，本機 Clone 的 fix/rag-reliability 分支，基準 1640832。已驗證程式與診斷快照一致；此處不是正式系統驗收，也沒有覆寫原始問答。

## 已實際執行

`python -m unittest discover -s tests -v`：23 個測試方法通過，含多組 subTest。無 Claude、Embedding 或資料庫呼叫。

| 驗證 | 結果／界線 |
|---|---|
| H07/H08 原始題句 | router.handle → handoff，mock rag.answer 未被呼叫，故不會經此路線呼叫 Claude |
| 安全反例 | 否定句、教育／翻譯引用、一般校務、第三人稱一般問句不新增自傷誤判；第三人稱實際危機與否定後轉折危機轉人工 |
| F03/G15/G16 規則 | 舊期限字面陳述即使重新抓取或加 approved，仍不能進 Context；測試為明確標記的合成 fixture，不是新增實證問答 |
| 審核與衝突 | 現行規定未經人工審核阻擋；正確 hash／日期核准通過，本文改動失效；混合新舊不能核准；不單憑年份判過期 |
| 檢索資格 | 五種身分、全體適用、日期邊界、過期、待審核、封存、錯誤身分／主題均有測試；合法鄰段保留 |
| 身分傳遞 | override 與已存身分進入 RAG；主 RPC、校名捷徑、鄰段帶資格參數 |
| 工作同義問法 | 原問題保留、補相同中文詞；coursework 不誤匹配。未取得新查詢向量，不宣稱真實檢索已改善 |
| 一般問題 | mock 固定候選下，OIA、退宿、學費、圖書館保留原排序、每文件兩段與距離限制；非原始 55 題端到端回歸 |
| 來源日期 | Supabase ISO、本地整數及 date 型別均維持相同日期輸出 |

另有數字子字串反例、SQL 版本不符／RPC 缺失停止上傳，以及 answer → 最終生成上下文帶身分與過期鄰段排除的驗證。

## q2–q6 候選重播

執行 `python tests/replay_captured.py --package <私有診斷包目錄>`。原始包留在 Git 以外，不把測試回答或知識庫全文上傳。固定知識庫批次 `20261110171206`、捕獲的 SQL candidates、距離與原始／擴充字串；沿用診斷時身分 None。未重新計算向量或 SQL，不能稱為正式環境查詢成功。

Golden 定義：手冊 `8af182dee53384c3-32/-33/-34` 三個費率表段落的完整本文，在最終 Context 中覆蓋的比例；只忽略標題與空白。不是全部正確答案的 Recall，也不代表資料當期有效性已核實。

| 題號 | 修改前 | 修改後 |
|---|---:|---:|
| q2 | 0/3 | 3/3 |
| q3 | 0/3 | 3/3 |
| q4 | 0/3 | 3/3 |
| q5 | 0/3 | 3/3 |
| q6 | 0/3 | 3/3 |

五組均以停用新費用排序的消融重現原本選中 IDs；Top-K=5、距離門檻 .30、每文件兩段不變。q3 原 Context 已有 FAQ 美元費用區間，0/3 僅表示指定手冊費率表未覆蓋，不能說原本完全沒有金額。詳見 `repair_evidence/captured_replay.json`。

## 未執行

- `sql/003_isolated_checks.sql`：此機器沒有 PostgreSQL／psql／pgvector 隔離環境，未執行原生 SQL；沒有宣稱 migration／PostgREST 已通過。
- Vercel 預覽及正式系統驗收；Repository 未配置 GitHub Actions workflow。
- 五種固定身分的真實中英文／fees/cost/charges/rates 向量與 Context Recall，以及 work permit 同義查詢的新向量對照。
- 原本正常題的完整 55 題檢索回歸、少量核准後的生成比對；現有單元／候選重播不取代這些驗收。

原始 ZIP、測試 Excel 與診斷包程式 hash 的保存檢查另見 `repair_evidence/validation.json`。
