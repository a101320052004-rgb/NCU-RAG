"""RAG 問答引擎：檢索 → 時效過濾 → 生成 → 附來源與日期。"""
from __future__ import annotations

import re

import config
import contacts
import db
import embedder
import knowledge_policy

CJK = re.compile(r"[一-鿿]")
_col = None


def detect_lang(text: str) -> str:
    return "zh" if CJK.search(text) else "en"


def collection():
    global _col
    if _col is None:
        import chromadb  # 只有本機版會用到；雲端版不用裝
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _col = client.get_collection("campus_kb")
    return _col


def reset_cache() -> None:
    global _col
    _col = None


# ---------------------------------------------------------------- 檢索與時效過濾
def build_where(identity: str | None, topic: str | None) -> dict:
    cutoff = int(config.stale_before().strftime("%Y%m%d"))
    conds = [{"status": "active"}, {"last_verified": {"$gte": cutoff}}]
    if identity and identity in config.IDENTITIES:
        conds.append({"$or": [{f"id_{identity}": True}, {f"id_{config.ALL_STUDENTS}": True}]})
    if topic:
        conds.append({f"t_{topic}": True})
    return {"$and": conds}


def _query_supabase(qvec: list[float], identity: str | None, topic: str | None) -> list[tuple]:
    import sb
    rows = sb.rpc("match_chunks_v2", {
        "query_embedding": sb.vec(qvec), "match_count": config.TOP_K * 3,
        "p_identity": identity if identity in config.IDENTITIES else None, "p_topic": topic,
        "p_stale_before": config.stale_before().isoformat()}) or []
    return [(r['body'], {k: v for k, v in r.items() if k not in ('body', 'distance')}, r['distance']) for r in rows]


# 中文短問題配英文資料很弱（「選課」找不到 Course Selection Calendar），檢索時補上英文說法。
# 只影響拿去找資料的字串，Claude 看到的還是學生原本的問題。
GLOSSARY = [
    (r"選課|加退選|退選|加選|課表", "course selection add drop course selection calendar 選課日程"),
    (r"行事曆|校曆|開學|放假|寒假|暑假|期中考|期末考", "academic calendar semester classes begin holiday 行事曆"),
    (r"註冊|繳費|學雜費", "registration calendar tuition fee payment 註冊"),
    (r"華語|中文課", "Chinese courses Mandarin language center 華語課程"),
    (r"居留證|居留", "ARC alien resident certificate residence permit"),
    (r"工作證|打工", "work permit part-time job"),
    (r"健保|保險", "national health insurance NHI medical insurance"),
    (r"看醫生|生病|診所|醫院", "medical clinic hospital health center"),
    (r"宿舍|住宿", "dormitory housing"),
    (r"租屋|租金|房租|租房|校外住", "off-campus housing rent Rent Off-Campus Student Services Division 校外租屋"),
    (r"獎學金|獎助", "scholarship financial aid"),
    (r"離校|畢業", "leaving school graduation procedures"),
    (r"包裹|郵件|郵局", "mail package post office"),
    (r"交通|公車|客運|高鐵|機場", "transportation bus HSR airport"),
    (r"學生證", "student ID card"),
    (r"圖書館|自習室", "library study room opening hours"),
]


# 反過來：國際處給的食衣住行、FAQ 都是中文，英文問題補上中文說法才找得到
GLOSSARY_EN = [
    (r"\bhsr\b|high[- ]speed rail|bullet train", "高鐵 高鐵桃園站 公車 172 173"),
    (r"\btrain\b|railway|\btra\b|zhongli station", "火車 中壢火車站 公車 交通"),
    (r"\bbus(es)?\b|shuttle", "公車 客運 交通"),
    (r"airport|taoyuan international", "機場 桃園機場 交通"),
    (r"youbike|\bbikes?\b|bicycle|scooter|taxi|uber", "YouBike 腳踏車 交通"),
    (r"vegetarian|vegan", "素食 蔬食 餐廳"),
    (r"cafeteria|canteen|on[- ]campus (food|dining|restaurant)", "松苑餐廳 校內餐廳 學餐"),
    (r"night market", "夜市 中壢觀光夜市 中原夜市"),
    (r"\bmalls?\b|department store|outlet|shopping (center|area)", "百貨 購物中心 商圈 逛街購物"),
    (r"movie|cinema|film", "電影院 購物中心 大江"),
    (r"\bclubs?\b|societ|student activit|extracurricular", "社團 課外活動 學生活動"),
    (r"halal|muslim", "清真 餐廳"),
    (r"\bfood\b|\beat\b|restaurants?|dining|cafeteria|canteen|breakfast|lunch|dinner", "餐廳 學餐 美食 用餐"),
    (r"supermarket|grocer|shopping|\bshops?\b|convenience store|\bbuy\b|daily necessities", "超市 購物 生活用品 便利商店"),
    (r"dorms?\b|dormitor|housing|\broom\b|accommodation", "宿舍 住宿"),
    (r"\brent(?:al|ing|s)?\b|apartments?|landlord|off[- ]campus", "校外租屋 租屋 off-campus housing Rent Off-Campus Student Services Division"),
    (r"electric|power card|prepaid card", "電費 電力計費儲值卡 插卡供電"),
    (r"laundry|washing machine", "洗衣 洗衣機"),
    (r"office hours|opening hours|lunch break|\bopen\b", "服務時間 辦公時間"),
    (r"\boia\b|international (affairs )?office|who (should|do|can) i (contact|ask)|contact person|in charge", "國際處 承辦人 聯絡"),
    (r"sister school|partner (school|universit)|exchange agreement", "姊妹校 交換學生"),
    (r"scholarship|financial aid|stipend", "獎學金 獎助學金"),
    (r"insurance|\bnhi\b", "保險 健保"),
    (r"\barc\b|resident certificate|residence permit", "居留證"),
    (r"work permit|part[- ]time|\bpermission\s+to\s+work\b|\b(?:work|employment)\s+authori[sz]ation\b|\bauthori[sz]ation\s+(?:to|for)\s+(?:work|employment)\b", "工作證 打工"),
    (r"lecture hall|venue|book a room|borrow", "借用 場地"),
]


def expand_query(q: str) -> str:
    table = GLOSSARY if CJK.search(q) else GLOSSARY_EN
    extra = [w for pat, w in table if re.search(pat, q, re.I)]
    if _work_permit_question(q):
        extra.append('student work permit application 工作許可申請')
    if _dorm_fee_question(q):
        extra.append('dormitory fees accommodation rates 宿舍費用 收費標準')
    search = q
    if _work_permit_question(q):
        search = re.sub(r'\bwork permit\b|\bpermission\s+to\s+work\b|\b(?:work|employment)\s+authori[sz]ation\b|\bauthori[sz]ation\s+(?:to|for)\s+(?:work|employment)\b', 'work permit', search, flags=re.I)
    # Only a generic Chinese fee question gets a canonical English expansion.
    # Questions with room/year/payment conditions keep their original wording.
    if re.fullmatch(r'(?:請問)?宿舍(?:費用|收費)(?:是多少|多少錢|多少)?[？?]?', q.strip()):
        return 'What are the dormitory fees?'
    return f"{search} {' '.join(extra)}" if extra else search


# 姊妹校名單一個國家常有幾十所、切成好幾段，向量搜尋分不出是哪一段有「千葉大學」，所以學校名稱改用文字比對直接找
PARTNER_Q = re.compile(r"姊妹校|姐妹校|合作學校|簽約|合約|交換|我的學校|原校|母校|(?i:partner|sister|exchange|home (university|school))")
SCHOOL_KW = re.compile(r"大學|學院|學校|研究所|(?i:university|institute|college)")
SELF_NAMES = {"中央大學", "國立中央大學", "中大學校", "中央大學校", "我的學校", "的學校", "姊妹學校", "姐妹學校", "合作學校"}


def partner_terms(q: str) -> list[str]:
    terms, prev = [], 0
    for m in SCHOOL_KW.finditer(q):
        kw = m.group(0)
        if kw.isascii():  # 英文：取前面 1～4 個字，或 University of 後面的字
            before = re.findall(r"[A-Za-z&.'-]+", q[max(prev, m.start() - 60):m.start()])
            for n in range(1, min(4, len(before)) + 1):
                terms.append(" ".join(before[-n:] + [kw]))
            after = re.match(r"\s+of\s+((?:[A-Z][A-Za-z&.'-]*\s?){1,3})", q[m.end():])
            if after:
                terms.append(f"{kw} of {after.group(1).strip()}")
        else:  # 中文：關鍵字前面 2～8 個中文字，越長越精準
            run = re.search(r"[一-鿿]{1,8}$", q[prev:m.start()])
            if run:
                s = run.group(0)
                terms += [s[-n:] + kw for n in range(len(s), 1, -1)]
        prev = m.end()
    out = [t for t in dict.fromkeys(terms) if len(t) >= 4 and t not in SELF_NAMES
           and not t.lower().startswith(("national central", "central university"))]
    return sorted(out, key=len, reverse=True)[:12]


def _partner_hits(question: str, identity: str | None = None, topic: str | None = None) -> list[dict]:
    if config.STORE != "supabase" or not PARTNER_Q.search(question):
        return []
    terms = partner_terms(question)
    if not terms:
        return []
    try:
        import sb
        rows = sb.rpc("find_partner_chunks_v2", {"p_terms": terms, 'p_identity': identity,
                      'p_topic': topic, 'p_stale_before': config.stale_before().isoformat()}) or []
    except Exception as e:  # noqa: BLE001  找不到就照一般向量搜尋
        print("partner lookup failed:", e)
        return []
    return [{"text": r['body'], 'distance': 0.0, 'score': -1.0,
             'meta': {k: v for k, v in r.items() if k not in ('body', 'distance', 'hit')}}
            for r in rows if _eligible(r['body'], r, identity, topic)]


# 核心主題的官方頁面：問題明顯屬於這些主題時，這幾段一定放進候選。
# 11/12 實測：保險、工作證、獎學金、就醫的官方頁面明明在知識庫裡，向量排名卻被手冊、行事曆擠掉，回答變成「沒有資料」。
# 指定的段落照樣要通過時效與法規檢查（例如工作證效期那兩段待人工核准，核准前不會被放進來）。
PINNED = [
    ("insurance", re.compile(r"insurance|\bnhi\b|health card|cathay|保險|健保|國泰", re.I),
     ["d56b81c706265a59-0", "d56b81c706265a59-2", "d56b81c706265a59-6"]),
    ("work_permit", re.compile(r"work permit|permission to work|work while studying|part[- ]time (?:job|work)|"
                               r"(?:work|employment) authori[sz]ation|工作證|工作許可|打工", re.I),
     ["a20d7a7e9a628894-0", "a20d7a7e9a628894-1", "a20d7a7e9a628894-2", "a20d7a7e9a628894-3"]),
    ("scholarship", re.compile(r"scholarship|financial aid|stipend|獎學金|獎助", re.I),
     ["12b5e67f79afb723-2", "12b5e67f79afb723-3", "12b5e67f79afb723-0"]),
    ("medical", re.compile(r"medical (?:help|assistance|care|service)|doctor|clinic|\bsick\b|health (?:center|centre|service)|"
                           r"first aid|see a doctor|看醫生|生病|就醫|衛保|保健組", re.I),
     ["8af182dee53384c3-57", "163d19b21b0c6dca-0", "163d19b21b0c6dca-1"]),
    ("counseling", re.compile(r"counsel|mental health|psycholog|therap|諮商|心理", re.I),
     ["ebc593e9b9048487-2", "ebc593e9b9048487-3"]),
    # 繳費：學雜費繳費單從 Portal 下載，ATM／信用卡／超商／第一銀行都能繳（Registration Calendar），出納組在行政大樓一樓（新生手冊）
    ("payment", re.compile(r"\bpay(?:ing|ment)?\b|繳費|繳交|怎麼繳|如何繳|繳費單", re.I),
     ["694ddc8efa604d15-7", "694ddc8efa604d15-11", "8af182dee53384c3-56"]),
    ("emergency", re.compile(r"emergenc|campus security|security (?:center|centre|office)|24[- ]hour|校安|緊急", re.I),
     ["163d19b21b0c6dca-0", "163d19b21b0c6dca-1"]),
]


# 依身分換成該身分的辦法：陸生、僑生的獎學金是另一套規定，不能拿外籍生獎學金回答
PINNED_BY_IDENTITY = {
    ("scholarship", "陸生"): ["657db3ed38bca9bb-0", "657db3ed38bca9bb-1"],
    ("scholarship", "僑生"): ["31aa0d4899e6700b-0", "31aa0d4899e6700b-1"],
}
# 已知身分時，檢索多查一次加上身分說法的版本，讓該身分專屬的資料浮上來
IDENTITY_TERMS = {"外籍學位生": "international degree students 外籍生 外籍學位生",
                  "交換生": "exchange students 交換生",
                  "僑生": "overseas Chinese students Hong Kong Macau 僑生 港澳生",
                  "陸生": "Mainland Chinese students 陸生 大陸地區人民",
                  "訪問研修生": "visiting students 訪問研修生"}


def pinned_ids(question: str, identity: str | None = None) -> list[str]:
    ids = []
    for name, pat, chunk_ids in PINNED:
        if pat.search(question):
            if name == "medical" and PINNED[0][1].search(question):
                continue  # 問醫療保險時不要把緊急電話表塞進來
            ids += PINNED_BY_IDENTITY.get((name, identity), chunk_ids)
    return list(dict.fromkeys(ids))


def _pinned_hits(question: str, topic: str | None = None, identity: str | None = None) -> list[dict]:
    ids = pinned_ids(question, identity)
    if config.STORE != "supabase" or not ids:
        return []
    try:
        import sb
        rows = sb.rpc("get_chunks_v2", {"p_ids": ids, "p_identity": None, "p_topic": topic,
                                         "p_stale_before": config.stale_before().isoformat()}) or []
    except Exception as e:  # noqa: BLE001  拿不到就照一般向量搜尋
        print("pinned lookup failed:", e)
        return []
    rows = [r.get("get_chunks_v2", r) for r in rows]
    order = {cid: i for i, cid in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r.get("id"), 99))
    return [{"text": r["body"], "distance": 0.0, "score": -0.5,
             "meta": {k: v for k, v in r.items() if k != "body"}}
            for r in rows if _eligible(r["body"], r, None, topic)]


def _eligible(text: str, md: dict, identity: str | None, topic: str | None) -> bool:
    return knowledge_policy.eligible(text, md, identity=identity, topic=topic,
                                     today=config.today(), stale_before=config.stale_before())


def _dorm_fee_question(question: str) -> bool:
    # Payment procedure and unpublished future prices must keep their original
    # ranking; a monetary table is not an answer to either question.
    if re.search(r"not (?:yet )?(?:announced|published)|尚未公布|未公布", question, re.I):
        return False
    method = re.search(r"\b(?:how|where)\b.{0,70}\bpay\b|payment (?:method|process|procedure)|如何.{0,15}繳|怎麼.{0,15}繳", question, re.I)
    amount = re.search(r"\bhow much\b|\b(?:costs?|charges?|rates?|prices?|amount)\b|多少錢|金額|收費標準", question, re.I)
    if method and not amount:
        return False
    return bool(re.search(r'dorm|housing|accommodation|宿舍|住宿', question, re.I)
                and re.search(r'\b(?:fees?|cost|charges?|rates?|pay|price)\b|費用|收費|多少錢|住宿費', question, re.I))


def _dorm_fee_payload(text: str, md: dict) -> bool:
    t = ((md.get('title') or '') + '\n' + text).lower()
    money = re.search(r'(?:ntd|nt\$|twd|usd|新[臺台]幣)\s*[0-9][0-9,.]*', t)
    accommodation = re.search(r'dorm|housing|accommodation|hsi wang|宿舍|住宿', t)
    unit = re.search(r'per person|per semester|每[人房學]|每學期|/每學期', t)
    fee_table = re.search(r'standard for dormitory fee|宿舍收費|住宿費', t)
    return bool(money and accommodation and (unit or fee_table))


def _work_permit_question(question: str) -> bool:
    return bool(re.search(r'work permit|permission\s+to\s+work|(?:work|employment)\s+authori[sz]ation|authori[sz]ation\s+(?:to|for)\s+(?:work|employment)|工作證|工作許可', question, re.I))


def _regulatory_payload_matches(question: str, text: str, md: dict) -> bool:
    payload = (md.get('title') or '') + '\n' + text
    if _work_permit_question(question):
        return bool(re.search(r'work permit|工作證|工作許可', payload, re.I))
    arc = re.search(r'\barc\b|alien resident certificate|居留證', question, re.I)
    deadline = re.search(r'deadline|within\s+\d+\s+days?|\d+[- ]day|申請期限', question, re.I)
    if arc and deadline:
        family, obsolete = knowledge_policy.policy_claim(payload)
        return not obsolete and bool(family and 'arc_arrival_deadline' in family)
    return True


# 追問：用代名詞或「那…呢」接著上一題問，這一句單獨拿去檢索會找不到東西
FOLLOW_UP = re.compile(r"\b(?:it|its|that|this|these|those|they|them|there|same|what about|how about|and for|also)\b"
                       r"|那個|這個|那裡|這裡|它|他們|還有|一樣|那要|那如果|那.{0,12}呢|呢[？?]?\s*$", re.I)


def is_follow_up(question: str) -> bool:
    return bool(FOLLOW_UP.search(question))


def follow_up_context(history: list[dict] | None) -> str:
    """追問時拿來和這一句一起檢索：最近一則「完整的問題」（主題）加上上一題。
    連續追問（What about B5? → Is that per semester? → How do I apply for it?）時，主題才不會接丟。"""
    qs = [(t.get("question") or "").strip() for t in (history or []) if (t.get("question") or "").strip()]
    if not qs:
        return ""
    anchor = next((q for q in reversed(qs) if not is_follow_up(q)), qs[0])
    return " ".join(dict.fromkeys([anchor, qs[-1]]))


def retrieve(question: str, identity: str | None, lang: str, topic: str | None = None,
             context: str | None = None, identity_hint: str | None = None) -> list[dict]:
    exact = _partner_hits(question, identity, topic)
    search = expand_query(question)
    expanded = search != question
    queries = [question, search] if expanded else [question]
    # 追問時再用「前一題＋這一句」查一次，和原句的結果合併；原句的結果照樣保留
    if context and is_follow_up(question):
        queries.append(expand_query(f"{context} {question}"))
    if identity_hint in IDENTITY_TERMS:
        queries.append(f"{search} {IDENTITY_TERMS[identity_hint]}")
    # 補了英文的版本和原本的問題各查一次再合併：補字幫英文資料浮上來，但不能把剛好對題的中文資料（例如公告）擠掉
    rows, seen, original_rank = [], {}, {}
    for q in queries:
        qvec = embedder.embed_query(q)
        found = _query_supabase(qvec, identity, topic) if config.STORE == "supabase" else _query_local(qvec, identity, topic)
        for rank, (doc, md, dist) in enumerate(found, 1):
            key = md.get("id") or (md.get("url"), doc[:50])
            if q == question:
                original_rank[key] = rank
            if key in seen:
                i = seen[key]
                if dist < rows[i][2]:
                    rows[i] = (doc, md, dist)
                continue
            seen[key] = len(rows)
            rows.append((doc, md, dist))
    hits = []
    for doc, md, dist in rows:
        if not _eligible(doc, md, identity, topic):
            continue
        if not _regulatory_payload_matches(question, doc, md):
            continue
        if topic is None and dist > config.MAX_DISTANCE:
            continue
        # 同語言的資料稍微優先；補了英文關鍵字的問題不加，避免英文的官方日程被擠下去
        score = dist - (0.03 if md.get("lang") == lang and not expanded else 0)
        cid = str(md.get("id", ""))
        if cid.startswith("ann-"):  # 國際處發的公告是最新、最直接的資料，稍微優先
            score -= 0.01
        elif cid.startswith(("sheet-", "md")):  # 國際處提供、人工整理的資料，排在爬蟲網頁前面
            score -= 0.015
        hits.append({"text": doc, "meta": md, "distance": dist, "score": score})
    hits.sort(key=lambda h: h["score"])
    if _dorm_fee_question(question):
        # Only fee queries receive a bounded rescue tier. Original-query evidence
        # is kept ahead of broad expansion/manual bonuses; TOP_K and distance
        # threshold stay unchanged. Every rescued chunk already passed eligibility.
        hits.sort(key=lambda h: (0 if _dorm_fee_payload(h['text'], h['meta']) else 1,
                                original_rank.get(h['meta'].get('id'), 10**6)
                                if _dorm_fee_payload(h['text'], h['meta']) else h['score'],
                                h['score']))
    pinned = _pinned_hits(question, topic, identity_hint)
    if pinned:
        ids = {h["meta"]["id"] for h in pinned}
        hits = pinned + [h for h in hits if h["meta"].get("id") not in ids]
    if exact:
        ids = {h["meta"]["id"] for h in exact}
        hits = exact + [h for h in hits if h["meta"].get("id") not in ids]
    # 同一份文件最多取 2 段，讓回答涵蓋不同來源（前後文由 expand_context 補）
    picked, per_doc = [], {}
    for h in hits:
        key = h["meta"].get("url") or h["meta"].get("title")
        if per_doc.get(key, 0) >= 2:
            continue
        per_doc[key] = per_doc.get(key, 0) + 1
        picked.append(h)
        if len(picked) >= config.TOP_K:
            break
    return picked


def _local_policy_rows(rows: list[tuple]) -> list[tuple]:
    wanted = list(dict.fromkeys(n for cid, text, md in rows
                               for n in knowledge_policy.neighboring_policy_ids(cid, md)))
    got = collection().get(ids=wanted) if wanted else {'ids':[], 'documents':[], 'metadatas':[]}
    neighbors = {cid: text for cid, text, md in zip(got['ids'], got['documents'], got.get('metadatas') or [])
                 if md and md.get('status') == 'active'}
    out = []
    for cid, text, md in rows:
        ids = knowledge_policy.neighboring_policy_ids(cid, md)
        if ids:
            doc, _, n = cid.rpartition('-')
            context = '\n'.join([neighbors.get(f'{doc}-{int(n)-1}', ''), text, neighbors.get(f'{doc}-{int(n)+1}', '')])
        else:
            context = text
        family, obsolete = knowledge_policy.policy_claim(context)
        own_family, own_obsolete = knowledge_policy.policy_claim(text)
        fragment = re.search(r'(15|30|fifteen|thirty)\s*(days?\b|日|天)|(1|one)\s*year\b|一年|6\s*months?\b|6\s*個月', text, re.I)
        rule = ('obsolete:' + family) if obsolete else ('review:regulated_claim' if own_family or (family and fragment) else None)
        out.append((cid, text, {**md, 'id':cid, 'policy_context_rule':rule}))
    return out


def _query_local(qvec: list[float], identity: str | None, topic: str | None) -> list[tuple]:
    res = None
    for attempt in range(2):
        try:
            res = collection().query(query_embeddings=[qvec], n_results=config.TOP_K * 3,
                                     where=build_where(identity, topic))
            break
        except Exception:  # noqa: BLE001
            # 資料庫還沒建，或剛被 ingest.py 重建：清掉舊連線重試一次，不用重開後端
            reset_cache()
    if res is None:
        return []
    rows = _local_policy_rows(list(zip(res['ids'][0], res['documents'][0], res['metadatas'][0])))
    return [(text, md, dist) for (cid, text, md), dist in zip(rows, res['distances'][0])]


# ---------------------------------------------------------------- 補上下文
NEIGHBOR_HITS = 3  # 前幾名的段落，把前後段一起給 AI（PDF 常被切斷在句子中間）


def _fetch_chunks(ids: list[str], identity: str | None = None, topic: str | None = None) -> dict[str, str]:
    if not ids:
        return {}
    try:
        if config.STORE == "supabase":
            import sb
            rows = sb.rpc('get_chunks_v2', {'p_ids': ids, 'p_identity': identity, 'p_topic': topic,
                          'p_stale_before': config.stale_before().isoformat()}) or []
            return {r['id']: r['body'] for r in rows if _eligible(r['body'], r, identity, topic)}
        got = collection().get(ids=ids)
        rows = _local_policy_rows(list(zip(got['ids'], got['documents'], got.get('metadatas') or [])))
        return {cid: text for cid, text, md in rows if _eligible(text, md, identity, topic)}
    except Exception as e:  # noqa: BLE001  補不到就用原本的段落
        print("fetch neighbors failed:", e)
        return {}


def expand_context(hits: list[dict], identity: str | None = None, topic: str | None = None) -> list[dict]:
    """段落 id 是「文件id-序號」：把前幾名的前一段、後一段接起來，讓 AI 看到完整的步驟或條件。"""
    hits = [h for h in hits if _eligible(h['text'], h['meta'], identity, topic)]
    want, plan = [], []
    for h in hits[:NEIGHBOR_HITS]:
        doc, _, n = str(h["meta"].get("id", "")).rpartition("-")
        # 人工 FAQ、國際處給的資料表每一段各自獨立（一家餐廳、一題問答），不用補前後段
        if not doc or not n.isdigit() or doc == "faq" or doc.startswith(("sheet-", "md")):
            plan.append(None)
            continue
        n = int(n)
        ids = [f"{doc}-{n - 1}", f"{doc}-{n + 1}"] if n > 0 else [f"{doc}-{n + 1}"]
        want += ids
        plan.append((doc, n))
    got = _fetch_chunks(want, identity, topic)
    used: set[str] = {str(h["meta"].get("id")) for h in hits}
    out = []
    for h, p in zip(hits, plan + [None] * (len(hits) - len(plan))):
        if not p:
            out.append(h)
            continue
        doc, n = p
        strip = lambda t: re.sub(r"^【[^】]*】\n?", "", t)  # noqa: E731  前後段開頭重複的標題拿掉
        before = got.get(f"{doc}-{n - 1}") if f"{doc}-{n - 1}" not in used else None
        after = got.get(f"{doc}-{n + 1}") if f"{doc}-{n + 1}" not in used else None
        used.update(x for x in (f"{doc}-{n - 1}" if before else "", f"{doc}-{n + 1}" if after else "") if x)
        text = "\n".join(x for x in ((before or ""), h["text"], strip(after) if after else "") if x)
        out.append({**h, "text": text})
    return out


def fmt_date(n) -> str:
    value = knowledge_policy.date_value(n)
    return value.isoformat() if value else '未知'


# ---------------------------------------------------------------- 生成
def system_prompt(identity: str | None, lang: str) -> str:
    tpl = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return (tpl.replace("{identity}", identity or "未設定")
               .replace("{lang}", "繁體中文" if lang == "zh" else "English")
               .replace("{today}", config.today().isoformat())
               .replace("{stale_before}", config.stale_before().isoformat())
               .replace("{current_academic_year}", str(config.CURRENT_ACADEMIC_YEAR)))


def build_user_message(question: str, hits: list[dict], history: list[dict], lang: str,
                       contact: str | None = None) -> str:
    src = []
    for i, h in enumerate(hits, 1):
        m = h["meta"]
        src.append(f'<source id="{i}" title="{m.get("title", "")}" url="{m.get("url", "")}" '
                   f'updated="{fmt_date(m.get("last_verified", 0))}">\n{h["text"]}\n</source>')
    contact = contact or (config.CONTACT_ZH if lang == "zh" else config.CONTACT_EN)
    hist = "\n".join(f"學生：{t['question']}\n你：{t['answer']}" for t in history)
    return (f"<sources>\n{chr(10).join(src)}\n</sources>\n"
            f"<contact>{contact}；校安中心 24 小時：{config.SECURITY_PHONE}</contact>\n"
            + (f"<history>\n{hist}\n</history>\n" if hist else "")
            + f"<question>{question}</question>"
            # 問題是中文時模型容易跟著用中文答，所以每一題都再明講一次要用哪種語言
            + ("\nReply in English only, even if the question is written in Chinese." if lang == "en"
               else "\n請用繁體中文回答。"))


def call_llm(system: str, user: str, hits: list[dict]) -> str:
    if config.LLM_PROVIDER == "fake":  # 測試用：不呼叫 API
        return "（測試模式）找到的資料：\n" + "\n".join(f"- {h['meta']['title']}" for h in hits)
    if config.LLM_PROVIDER == "openai_compat":
        # 任何提供 OpenAI 相容介面的服務都走這裡，例如 Gemini、本機 Ollama
        from openai import OpenAI
        client = OpenAI(base_url=config.COMPAT_BASE_URL, api_key=config.COMPAT_API_KEY or "none")
        r = client.chat.completions.create(
            # Gemini 3 系列會先「思考」再回答，思考也算在 max_tokens 裡，給少了會回空白
            model=config.COMPAT_MODEL, max_tokens=4000, temperature=0.2,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return (r.choices[0].message.content or "").strip()
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    # 新版 anthropic 套件已不接受 temperature 參數，用預設值即可
    msg = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=450,
                                 system=system, messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


EMOJI = re.compile("[\\U0001F300-\\U0001FAFF\\u2600-\\u27BF\\uFE0F]")
CLOSERS = re.compile(r"\n+\s*(需要其他(幫忙|幫助|協助)嗎|還有其他(問題|想問的)嗎|希望(這些資訊)?對你有幫助|有(任何)?問題(都可以|隨時)[^\n]*"
                     r"|Anything else[^\n]*|Hope this helps[^\n]*|Let me know if[^\n]*)[？?！!。.]*\s*$", re.I)


# 開頭只在講「我查到什麼」、沒有內容的那一行（例如「根據資料我找到的有：」）直接拿掉
META_OPENER = re.compile(r"\A\s*(根據|依據)?[^。\n]{0,12}(資料|查到|找到)[^。\n]{0,16}[：:]\s*\n+")


# 「資料裡只有…」「我查到的資料主要是…」這類在講資料本身的句子：學生不需要知道，整句拿掉。
# 只抓「資料＋只有／沒有／主要」這種組合，避免誤刪「把申請資料上傳」這類正常句子。
META_SENT = re.compile(r"[^。！？!?\n]*(資料(裡|中|庫)(面)?(只|沒|並沒|並未|主要|目前|查不到|找不到)|我查到的(資料)?(只|主要|沒)|"
                       r"(提供|現有)的資料(只|沒|主要|並未))"
                       r"[^。！？!?\n]*[。！？!?]?"
                       r"|(?i:[^.!?\n]*\bthe (provided )?(sources|documents|information) (only|don't|do not|doesn't)[^.!?\n]*[.!?]?)")
# 句首的「根據資料，」只拿掉這幾個字，後面的內容保留
META_PREFIX = re.compile(r"(?m)(^|[。！？\n])\s*(根據|依據)(提供的|這些|現有的)?(資料|來源)(顯示)?[，,：:]?\s*")


def drop_meta(t: str) -> str:
    t = META_SENT.sub("", t)
    t = META_PREFIX.sub(lambda m: m.group(1), t)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"(?m)^[ \t]*[，,、]\s*", "", t)).strip()


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def scrub_email(t: str) -> str:
    """國際處決定只對學生公開總信箱：爬來的網頁裡有同仁個人信箱，回答時一律換成總信箱。"""
    t = EMAIL.sub(lambda m: m.group(0) if m.group(0).lower() == contacts.OIA_MAIL else contacts.OIA_MAIL, t)
    return re.sub(rf"({re.escape(contacts.OIA_MAIL)})([、，,/／或及和\s]*{re.escape(contacts.OIA_MAIL)})+", r"\1", t)


def plain_text(t: str) -> str:
    """LINE 和聊天頁都不顯示 Markdown；模型偶爾還是會寫粗體、標題、表情符號或客套結尾，這裡清掉。"""
    t = scrub_email(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?m)^#{1,6}\s*", "", t)
    t = EMOJI.sub("", t)
    t = CLOSERS.sub("", t)
    t = META_OPENER.sub("", t, count=1)
    t = drop_meta(t)
    t = re.sub(r"(?m)^[ \t]+", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


WORD = re.compile(r"[A-Za-z][A-Za-z'-]{3,}|\d[\d,.:]*\d|\d|[一-鿿]{2,}")
STOP = {"your", "with", "from", "that", "this", "they", "them", "there", "have", "will", "must", "need", "when",
        "what", "which", "where", "about", "into", "also", "only", "then", "than", "each", "more", "contact",
        "call", "email", "office", "check", "student", "students", "international", "please", "ncuoia"}


def rank_by_answer(hits: list[dict], reply: str) -> list[dict]:
    """來源要對應答案實際用到的頁面：用答案裡的關鍵字和數字比對每一段，重合越多越前面。
    11/12 實測：答案明明出自工作證頁，來源卻列「住宿服務組在哪裡」，因為原本只照檢索排名列前兩名。"""
    words = {w.lower() for w in WORD.findall(reply)} - STOP
    if not words:
        return hits
    def overlap(h):
        body = h["text"].lower()
        return sum(1 for w in words if w in body)
    scored = [(overlap(h), i, h) for i, h in enumerate(hits)]
    best = max(s for s, _, _ in scored)
    if best == 0:
        return hits
    # 重合太少的段落（不到最佳的三分之一）不列為來源
    keep = [(s, i, h) for s, i, h in scored if s >= max(2, best / 3)]
    return [h for s, i, h in sorted(keep, key=lambda x: (-x[0], x[1]))] or hits


def source_footer(hits: list[dict], lang: str, n: int = 2) -> tuple[str, list[dict]]:
    seen, used = set(), []
    for h in hits:
        m = h["meta"]
        key = m.get("url") or m.get("title")
        if key in seen:
            continue
        seen.add(key)
        used.append({"title": m.get("title", ""), "url": m.get("url", ""),
                     "updated": fmt_date(m.get("last_verified", 0))})
        if len(used) >= n:
            break
    label, upd = ("來源", "資料更新") if lang == "zh" else ("Source", "Updated")
    sep = "：" if lang == "zh" else ": "
    lines = [f"{label}{sep}{u['title']}｜{upd}{sep}{u['updated']}" + (f"\n{u['url']}" if u["url"] else "")
             for u in used]
    return "\n".join(lines), used


def no_data_answer(lang: str, contact: str | None = None) -> str:
    if lang == "zh":
        return ("這個問題我查不到最新、確定的資料，不想給你可能過時的答案。\n"
                f"請直接問承辦人：{contact or config.CONTACT_ZH}")
    return ("I couldn't find up-to-date information on this, and I don't want to give you an outdated answer.\n"
            f"Please contact: {contact or config.CONTACT_EN}")


def answer(question: str, identity: str | None, lang: str | None = None,
           topic: str | None = None, history: list[dict] | None = None, quota_key: str = "anon",
           search: str | None = None) -> dict:
    lang = lang or detect_lang(question)
    # 學生沒設定身分、但問題裡自己講了（As an exchange student…），就照問題裡的身分回答和轉接
    identity = identity or contacts.guess_identity(question)
    contact = contacts.contact_line(identity, lang, question)  # 依身分轉給負責的承辦人
    # 回答語言固定（預設英文），但檢索要看問題本身是哪種語言，中文問題才找得到中文資料。
    # 檢索不分身分（國際處原則）：資料上的身分標籤是自動標的、不可靠（例如保險頁只標了交換生），
    # 用身分篩選會讓學位生查不到保險頁。身分只拿來決定轉給哪位承辦人。
    hits = retrieve(search or question, None, detect_lang(question), topic,
                    context=None if search else follow_up_context(history),  # 選單用關鍵字檢索，比問句準
                    identity_hint=identity)
    if not hits:
        return {"answer": no_data_answer(lang, contact), "route": "no_data", "sources": []}
    footer, used = source_footer(hits, lang)
    allow = db.llm_allow(quota_key)  # 雲端版：AI 開關＋每日次數上限，保護 Claude 費用
    if allow != "ok":
        route = "ai_off" if allow == "off" else "limited"
        return {"answer": f"{quota_answer(allow, lang, contact)}\n\n{footer}", "route": route, "sources": used}
    try:
        context = expand_context(hits, None, topic)
        text = call_llm(system_prompt(identity, lang),
                        build_user_message(question, context, history or [], lang, contact), context)
    except Exception as e:  # noqa: BLE001  API 金鑰錯、沒餘額、額度用完、網路斷線
        log_error("LLM", e)
        return {"answer": f"{llm_down_answer(lang, contact)}\n\n{footer}", "route": "llm_error", "sources": used}
    if text.lstrip().startswith("[CHAT]"):  # 閒聊或無關的問題：不閒聊，只回「可以問什麼」
        return {"answer": CHAT_REPLY[lang], "route": "chat", "sources": []}
    if text.lstrip().startswith("[OUTSIDE]"):  # 即時或校外資訊（電影場次、即時車資）：說明不在服務範圍，不附承辦人
        note = english_only(plain_text(text.lstrip().removeprefix("[OUTSIDE]")), lang)
        return {"answer": f"{OUTSIDE_REPLY[lang]}{(chr(10) + note) if note else ''}", "route": "chat", "sources": []}
    if text.lstrip().startswith("[NOINFO]") or not plain_text(text):  # 資料答不了：固定說法＋去哪裡看＋找誰
        return no_info_answer(english_only(plain_text(text.lstrip().removeprefix("[NOINFO]")), lang), hits, lang, contact)
    reply = english_only(plain_text(text), lang)
    # 最後一道保險：AI 寫出已知的舊法規（例如入境後 15 天辦居留證）就不送出，改請學生找承辦人
    if knowledge_policy.policy_claim(reply)[1]:
        log_error("obsolete-policy-in-answer", RuntimeError(reply[:200]))
        return no_info_answer("", hits, lang, contact)
    if REGULATED_Q.search(question):  # 只看問題：答案順帶提到 ARC（例如報到要帶的文件）不用加
        reply += "\n\n" + REGULATED_NOTE[lang]
    footer, used = source_footer(rank_by_answer(context, reply), lang)
    return {"answer": f"{reply}\n\n{footer}", "route": "rag", "sources": used}


# 居留證、簽證、工作證類的提醒句：由程式固定加上（原本叫 AI 寫，英文回答裡會冒出中文那句）
REGULATED_Q = re.compile(r"\barc\b|resident certificate|residence permit|\bvisa\b|work permit|permission to work|"
                         r"居留|簽證|工作證|工作許可|打工|入出境", re.I)
REGULATED_NOTE = {"zh": "以移民署／勞動部最新公告為準。",
                  "en": "Always follow the latest announcements from the National Immigration Agency / Ministry of Labor."}
OUTSIDE_REPLY = {"zh": "這個問題超出我能協助的範圍（我只回答中大學生事務與校園生活）。",
                 "en": "That's outside what I can help with. I only answer questions about NCU student services and campus life."}
_DISCLAIMER = re.compile(r"[（(]?\s*以(?:內政部)?(?:移民署|勞動部)[^。\n]{0,20}最新公告為準[^\n]*|"
                         r"^.*(?:latest|most recent) (?:announcements?|regulations?|guidelines?) (?:of|from) "
                         r"(?:the |taiwan's )?(?:national )?(?:immigration|ministry of labor).*$", re.I | re.M)
_SYSTEM_TALK = re.compile(r"[^。\n]*系統會自動[^。\n]*[。]?")


def english_only(t: str, lang: str) -> str:
    """英文回答裡不該出現整句中文（單位或文件的中文原名放在括號裡可以）。
    11/12 實測：答不出來時 AI 用中文寫了「可以先看…」「系統會自動附上聯絡方式」。"""
    t = _SYSTEM_TALK.sub("", _DISCLAIMER.sub("", t))
    if lang != "en":
        return t.strip()
    out = []
    for line in t.split("\n"):
        letters = re.findall(r"[A-Za-z]", line)
        cjk = CJK.findall(line)
        if cjk and len(cjk) > len(letters) * 0.6 and len(cjk) >= 6:
            continue  # 這一行主要是中文，整行拿掉
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


CHAT_REPLY = {"zh": "可以問居留證、宿舍、獎學金、保險、選課、交通、飲食，直接打字或點下方選單。",
              "en": "Ask me about your ARC, dorms, scholarships, insurance, courses, transportation or food, "
                    "or tap the menu below."}


def no_info_answer(pointer: str, hits: list[dict], lang: str, contact: str | None) -> dict:
    """答不出來時的回覆。只附 AI 指名的那一頁當來源，不把不相關的資料列出來。"""
    named = [h for h in hits if (h["meta"].get("title") or "\0") in pointer]
    footer, used = source_footer(named, lang, n=1) if named else ("", [])
    if lang == "zh":
        head = "這個我目前沒有確切的資訊。"
        tail = f"想直接問人：{contact or config.CONTACT_ZH}"
    else:
        head = "I don't have reliable details on this yet."
        tail = f"To talk to a person: {contact or config.CONTACT_EN}"
    body = "\n".join(x for x in (head + (pointer if lang == "zh" else " " + pointer if pointer else ""), tail) if x)
    # 路線記成 no_data，後台的「知識缺口」才會一起統計；answered=True 讓選單不要再換成「還沒整理好」
    return {"answer": f"{body}\n\n{footer}".strip(), "route": "no_data", "sources": used, "answered": True}


def quota_answer(reason: str, lang: str, contact: str | None = None) -> str:
    """不呼叫 Claude 時的回覆：照樣給找到的官方頁面，學生點連結就能看。"""
    if lang == "zh":
        head = {"off": "AI 回答功能目前暫停中，",
                "user_limit": "你今天的提問次數已經用完了，",
                "site_limit": "今天的提問人數比較多，AI 回答先暫停，"}.get(reason, "")
        return f"{head}先把找到的官方資料給你，點連結就能看完整內容。\n想直接問人的話：{contact or config.CONTACT_ZH}"
    head = {"off": "AI answers are paused right now. ",
            "user_limit": "You've reached today's question limit. ",
            "site_limit": "We're very busy today, so AI answers are paused. "}.get(reason, "")
    return f"{head}Here are the official pages I found for you.\nTo talk to a person: {contact or config.CONTACT_EN}"


def llm_down_answer(lang: str, contact: str | None = None) -> str:
    """AI 暫時不能用時：不讓學生看到錯誤，改給找到的官方頁面和承辦窗口。"""
    if lang == "zh":
        return ("AI 回答服務暫時忙線中，先把找到的官方資料給你，點連結就能看完整內容。\n"
                f"想直接問人的話：{contact or config.CONTACT_ZH}")
    return ("The AI service is busy right now. Here are the official pages I found for you.\n"
            f"To talk to a person: {contact or config.CONTACT_EN}")


def log_error(where: str, e: Exception) -> None:
    """錯誤印在後端視窗，本機版另外寫進 store/error.log，方便事後查原因。"""
    import datetime as dt
    import traceback
    msg = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {where}: {type(e).__name__}: {e}"
    print(msg)
    if config.STORE == "local":
        try:
            config.STORE_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.STORE_DIR / "error.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n" + traceback.format_exc() + "\n")
        except OSError:
            pass
