"""分流器：決定一則訊息要走哪條路。
  1. 設定指令（選身分、切換語言）
  2. 高風險（受傷、心理、糾紛、緊急）→ 轉人工，不讓 AI 回答
  3. 點選單（食衣住行育樂）→ 該主題的懶人包
  4. 其他 → RAG 問答
"""
from __future__ import annotations

import re
import time

import config
import contacts
import db
import rag

HIGH_RISK = {
    "自我傷害": ["自殺", "想死", "不想活", "傷害自己", "割腕", "kill myself", "suicide", "self-harm",
                 "self harm", "end my life", "want to die"],
    "受傷就醫": ["受傷", "車禍", "急診", "救護車", "骨折", "流血", "injured", "injury", "accident",
                 "ambulance", "emergency room", "bleeding"],
    "心理困擾": ["憂鬱", "焦慮到", "崩潰", "恐慌", "depressed", "depression", "panic attack", "breakdown"],
    "糾紛安全": ["騷擾", "性騷", "被打", "被偷", "糾紛", "威脅", "報警", "跟蹤",
                 "harass", "assault", "stolen", "threat", "stalk", "police"],
}

MENU_QUERY = {
    "zh": "列出境外生在「{t}」方面最實用的事，最多 3 件，每件一句話（地點、價格、時間、怎麼辦）。",
    "en": "List the most useful things international students should know about '{t}', "
          "at most 3, one sentence each (places, prices, times, how-to).",
}
# 點選單時拿去檢索的關鍵字（問句太抽象，例如「住」會找到「居留證」）
MENU_SEARCH = {
    "食": "學生餐廳 學餐 美食 用餐 便當 價格 素食 清真 food dining restaurant cafeteria",
    "衣": "洗衣 洗衣機 天氣 氣候 冬天 衣服 laundry weather clothing",
    "住": "宿舍 住宿申請 入住 退宿 住宿費 校外租屋 dormitory housing check-in",
    "行": "交通 公車 客運 高鐵 機場 腳踏車 停車 transportation bus HSR airport bike",
    "育": "選課 課程 學分 註冊 學業 華語 course registration study credits",
    "樂": "社團 活動 旅遊 運動 文化交流 club activities travel sports",
    "規": "居留證 簽證 工作證 打工 健保 保險 規定 權益 ARC visa residence permit work permit health insurance regulations",
}
# 單一個字 Claude 會猜錯（「育」被當成體育），問句裡改用完整的說法
TOPIC_ZH = {"食": "飲食（學餐、餐廳、價格）", "衣": "衣物（洗衣、天氣、穿著）", "住": "住宿（宿舍申請、入住、退宿）",
            "行": "交通（公車、高鐵、機場）", "育": "學業（選課、註冊、行事曆、華語課）", "樂": "休閒（社團、活動、旅遊）",
            "規": "權益與規定（居留證、簽證、工作證、健保）"}
TOPIC_EN = {"食": "food and dining", "衣": "clothing, laundry and weather", "住": "housing and dorms",
            "行": "transportation", "育": "courses and study", "樂": "activities and clubs",
            "規": "rights and regulations (residence permit/ARC, visa, work permit, health insurance)"}
# LINE 圖文選單（2026-11 新版五格）按下去送出的文字 → 主題；舊的「食衣住行育樂」單字也照樣認得
MENU_BUTTONS = {"food": "食", "stay": "住", "transport": "行", "fun": "樂", "rights & regulations": "規"}
# 點選單後附上的常見問題按鈕（LINE 按鈕文字上限 20 字，所以都寫短）
MENU_SUGGEST = {
    "食": (["素食推薦", "校內餐廳", "附近便宜美食", "附近夜市"],
          ["Vegetarian options?", "Campus cafeterias", "Cheap food nearby", "Night markets"]),
    "住": (["怎麼申請宿舍", "宿舍入住流程", "宿舍電費怎麼付", "退宿流程"],
          ["Apply for a dorm", "Dorm check-in", "Moving out of dorm"]),
    "行": (["怎麼去高鐵站", "怎麼去中壢火車站", "機場到中大", "YouBike"],
          ["Bus to HSR station", "Bus to Zhongli", "Airport to NCU", "YouBike near NCU"]),
    "樂": (["附近夜市", "百貨商場", "學生社團", "Outlet 購物"],
          ["Night markets", "Shopping malls", "Student clubs", "Outlet shopping"]),
    "規": (["怎麼申請居留證", "居留證延期", "工作證申請", "健保怎麼辦"],
          ["Apply for an ARC", "Extend my ARC", "Work permit", "Health insurance"]),
}
MENU_NAME_EN = {"食": "Food", "住": "Stay", "行": "Transport", "樂": "Fun", "規": "Rights & Regulations",
                "衣": "Clothing", "育": "Study"}


def menu_key(text: str) -> str | None:
    if text in config.MENU_TOPICS:
        return text
    return MENU_BUTTONS.get(text.strip().lower())


# 不用查資料、也不用呼叫 Claude 的閒聊（省錢、也不會亂塞不相關的資料）
END = r"[!！~～。.,，\s]*$"
SMALLTALK = [
    ("greet", re.compile(r"^(hi|hello|hey|嗨+|你好|您好|哈囉|哈嘍|早安|午安|晚安|安安)" + END, re.I)),
    ("thanks", re.compile(r"^(謝謝|感謝|謝啦|thanks?( you)?|thx|ok|okay|好的?|了解|知道了|收到)" + END, re.I)),
    ("bye", re.compile(r"^(bye|掰掰?|再見|拜拜)" + END, re.I)),
]
MOOD_GOOD = ("心情很好", "心情好", "好開心", "很開心", "好快樂", "happy", "great day", "good mood")
MOOD_BAD = ("心情不好", "難過", "想家", "孤單", "寂寞", "好累", "壓力好大", "好煩", "sad", "homesick", "lonely",
            "stressed", "so tired", "bad day")
ASKING = re.compile(r"[?？]|怎麼|如何|哪|嗎|什麼|請問|要不要|可以|\b(how|what|where|when|which|can|need|should)\b", re.I)
# 國際處要求不閒聊：打招呼、道謝、聊心情一律只回「可以問什麼」這句必要資訊
HELP_LINE = ("可以問居留證、宿舍、獎學金、保險、選課、交通、飲食，直接打字或點下方選單。",
             "Ask me about your ARC, dorms, scholarships, insurance, courses, transportation or food, "
             "or tap the menu below.")
SMALLTALK_REPLY = {
    "greet": HELP_LINE,
    "thanks": HELP_LINE,
    "bye": HELP_LINE,
    "good": HELP_LINE,
    "bad": ("需要找人談的話，可以打給國際處承辦人（下方按鈕）。" + HELP_LINE[0],
            "If you need someone to talk to, call the OIA staff below. " + HELP_LINE[1]),
}


def detect_smalltalk(text: str) -> str | None:
    t = text.strip()
    for kind, pat in SMALLTALK:
        if pat.match(t):
            return kind
    if len(t) <= 40 and not ASKING.search(t):
        low = t.lower()
        if any(w in low for w in MOOD_BAD):
            return "bad"
        if any(w in low for w in MOOD_GOOD):
            return "good"
    return None


def detect_high_risk(text: str) -> str | None:
    low = text.lower().replace("’", "'")
    low = re.sub(r"\b(?:don't|doesn't|didn't|won't|wouldn't)\b", "do not", low)
    # Educational mentions are not evidence of an actual person's intent.
    # A reported real person's immediate risk must still reach a human.
    educational = re.compile(r"^\s*(?:please\s+)?(?:translate\b|(?:the )?(?:definition|meaning|example sentence|lyrics)\b|in (?:my|a|the) (?:novel|fictional story)\b|what does .+ mean|how (?:can|do) i (?:avoid|prevent)\b.+\b(?:while|when)\b)")
    action = re.compile(r"\b(?:hurt(?:ing)?|harm(?:ing)?|kill(?:ing)?)\s+(?:myself|himself|herself|themselves|yourself)\b")
    negated = re.compile(r"\b(?:not|never|no longer)\s+(?:(?:thinking|thoughts?)\s+(?:about|of)\s+|(?:planning|going|trying|want(?:ing)?|intend(?:ing)?)\s+to\s+)?$")
    clauses = re.split(r"[.!?;\n]|\bbut\b|\bhowever\b", low)
    for clause in clauses:
        for match in action.finditer(clause):
            if not educational.search(clause) and not negated.search(clause[:match.start()]):
                return "自我傷害"
        for word in HIGH_RISK['自我傷害']:
            for match in re.finditer(re.escape(word), clause):
                if educational.search(clause) or negated.search(clause[:match.start()]):
                    continue
                if re.search(r"(?:不想|不會|沒有|不打算)\s*$", clause[:match.start()]) and word != '不想活':
                    continue
                return '自我傷害'
    # Distress requests and serious dorm conflicts in the evaluation set need
    # a person too. Require personal context, not a broad keyword alone.
    personal = re.compile(r"\b(?:i|my|we|our)\b")
    support = re.compile(r"talk to (?:someone|somebody)|someone to talk to|who (?:at ncu )?can help|(?:need|want|would like).{0,40}(?:support|counsel|help)")
    for clause in clauses:
        if educational.search(clause) or not personal.search(clause):
            continue
        for match in re.finditer(r"\b(?:anxious|overwhelmed)\b", clause):
            if support.search(low) and not re.search(r"\b(?:not|never|no longer)\s+(?:feeling\s+)?$", clause[:match.start()]):
                return '心理困擾'
        conflict = re.search(r"\bserious\s+(?:dispute|conflict)\b", clause)
        if conflict and re.search(r"roommate|dormitor|\bdorm\b", clause):
            negative = re.search(r"\b(?:not|never|no longer)\s+(?:(?:having|experiencing|in)\s+)?(?:(?:a|any)\s+)?$", clause[:conflict.start()])
            resolved = re.search(r"\b(?:i|we) (?:have |already )?(?:resolved|settled)\b", clause)
            if not negative and not resolved:
                return '糾紛安全'
    for cat, words in HIGH_RISK.items():
        if cat != '自我傷害' and any(word in low for word in words):
            return cat
    return None


ID_EN = {"外籍學位生": "Degree student", "交換生": "Exchange student", "僑生": "Overseas Chinese",
         "陸生": "Mainland China", "訪問研修生": "Visiting student"}
# 國際處決定：不先問學生身分，回答一律用英文；學生用中文或英文問都可以（檢索會看問題本身的語言）
DEFAULT_LANG = "en"

# 只打「fee」「費用」這種一兩個字，檢索分不出是哪一種費用，先請學生選
VAGUE_FEE = re.compile(r"^\s*(?:the\s+|any\s+)?(?:fees?|costs?|prices?|charges?|payments?|expenses?|how much|費用|收費|多少錢|繳費|要繳什麼錢)\s*[?？!！.。]*\s*$", re.I)
FEE_CHOICES = ["Dorm fees", "Tuition fees", "ARC fee", "Health insurance fee"]
# 只問「How do I pay?」「怎麼繳費」：先請學生選要繳什麼（11/12 實測：直接檢索會抓到不相干的頁面）
VAGUE_PAY = re.compile(r"^\s*(?:how|where|when)\s+(?:do|can|should|will)\s+i\s+pay(?:\s+(?:it|them|this|that|fees?|the fees?|my fees?))?"
                       r"\s*[?？.!！]*\s*$|^\s*(?:請問)?(?:怎麼|如何|去哪裡?|在哪裡?)繳(?:費|錢)?[?？]*\s*$", re.I)
PAY_CHOICES = ["Pay tuition fees", "Pay credit fees", "Pay dorm fees", "Pay health insurance"]


SWITCH_ID = {"身分", "身份", "切換身分", "切換身份", "更改身分", "identity", "change identity", "切換身分 change id"}
SWITCH_CHIP = "切換身分 Change ID"


def identity_chips() -> list[str]:
    return [f"身分：{i} {ID_EN[i]}" for i in config.IDENTITIES[:4]]


def parse_identity(text: str) -> str | None:
    m = re.match(r"^(身分|identity|i am|i'm|im(?=\s))\s*[:：]?\s*(.+)$", text, re.I)
    if not m:
        return None
    rest = m.group(2).lower()
    found = next((i for i in config.IDENTITIES if i in rest or ID_EN[i].lower() in rest), None)
    return found or contacts.guess_identity(rest)


def only_identity(text: str) -> bool:
    """「I'm an exchange student」這種只是在講身分；後面還接著問問題就要照樣回答。
    11/12 實測：「I'm an exchange student. Can I apply for a scholarship?」只回了「Got it: Exchange student」，問題被吃掉。"""
    first = re.split(r"[.?!,;。？！，；\n]", text.strip(), maxsplit=1)
    rest = first[1].strip() if len(first) > 1 else ""
    return not rest and "?" not in text and "？" not in text and len(text.split()) <= 8


# 依情況補上的求助管道（來源：住宿服務組「學生安全與緊急聯絡」頁、中大電子報「心理資源求助專區」，2026-11 查核）
# 英文回答用的校安電話（環境變數 SECURITY_PHONE 是中文寫法，英文回答裡會整串中文）
SECURITY_EN = "03-280-5666 or mobile 0911-949-630 (campus ext. 57212)"
HEALTH_CENTER = {"zh": "衛生保健組（上班時間）：分機 57270／03-280-4814",
                 "en": "Health Center (office hours): ext. 57270 or 03-280-4814"}
COUNSELING = {"zh": "諮商中心（週一至週五 9:00–17:00）：03-422-7151 分機 57263／57264，或在 NCU Portal 的「個別諮商」預約",
              "en": "Counseling Center (Mon–Fri 9:00–17:00): +886-3-422-7151 ext. 57263 / 57264, "
                    "or book through NCU Portal > Student Consulting Service > Consult System"}


def handoff_message(category: str, lang: str, identity: str | None = None, text: str = "") -> str:
    contact = contacts.contact_line(identity, lang, text)
    if lang == "zh":
        lines = ["這件事很重要，需要由真人協助，我不想只給你一般資訊。"]
        if category in ("自我傷害", "受傷就醫"):
            lines.append("・有生命危險或傷勢嚴重：立刻撥 119 叫救護車。")
        lines.append(f"・緊急狀況請立刻撥校安中心 24 小時電話：{config.SECURITY_PHONE}")
        if category == "自我傷害":
            lines.append("・如果你現在很難受，可以撥 1925 安心專線（24 小時、免付費），有人可以陪你聊。")
        if category in ("自我傷害", "心理困擾"):
            lines.append("・" + COUNSELING["zh"])
        if category == "受傷就醫":
            lines.append("・" + HEALTH_CENTER["zh"])
        lines.append(f"・國際處承辦人：{contact}")
        return "\n".join(lines)
    lines = ["This matters, and a real person should help you with it rather than general information from me."]
    if category in ("自我傷害", "受傷就醫"):
        lines.append("- If your life is in danger or the injury is serious, call 119 (ambulance) now.")
    lines.append(f"- In an emergency, call the Campus Security 24-hour line now: {SECURITY_EN}")
    if category == "自我傷害":
        lines.append("- If you're struggling right now, call 1925 (Taiwan's 24-hour free support line). Someone will talk with you.")
    if category in ("自我傷害", "心理困擾"):
        lines.append("- " + COUNSELING["en"])
    if category == "受傷就醫":
        lines.append("- " + HEALTH_CENTER["en"])
    lines.append(f"- {contact}")
    return "\n".join(lines)


# 問別人的個資（學號、房號、電話、地址…）：不查、不回答，固定說明（11/12 實測 U05 原本回成選單提示）
OTHERS = (r"(?:\b(?:another (?:student|person|classmate)|other (?:students?|people|person)|someone else|somebody else|"
          r"(?:a|my) (?:classmate|roommate|friend)'s|(?:classmate|roommate|friend)'s)\b|別人|其他同學|同學|室友)")
PERSONAL = (r"(?:\b(?:student (?:id|number)|id (?:number|no)|room (?:number|no)|phone (?:number|no)|address|e-?mail|passport (?:number|no)|arc number)\b|"
            r"學號|房號|寢室號|電話|地址|信箱|護照號碼|居留證號)")
ASK = r"(?:\b(?:tell|give|share|find|look up|send|what(?:'s| is| are))\b|查|給我|告訴我|是多少|是什麼)"
PRIVACY_ASK = re.compile(ASK + r".{0,40}" + OTHERS + r".{0,60}" + PERSONAL + "|" + OTHERS + r"的?" + PERSONAL + r".{0,10}(?:是多少|是什麼|多少|[?？])", re.I)
PRIVACY_REPLY = {"zh": "我不能提供或查詢其他同學的個人資料（學號、房號、聯絡方式等）。需要聯絡對方的話，可以請住宿服務組或國際處協助轉達。",
                 "en": "I can't share or look up other students' personal information (student ID, room number, contact details). "
                       "If you need to reach someone, the Student Housing Service Division or OIA can pass on a message."}


def handle(user_id: str, text: str, channel: str = "line", identity_override: str | None = None,
           limit_key: str | None = None) -> dict:
    """回傳 {answer, route, sources, quick_replies}。"""
    t0 = time.time()
    uh = db.hash_user(user_id)
    user = db.get_user(uh)
    text = text.strip()
    known = identity_override or user.get("identity")
    lang = DEFAULT_LANG  # 一律英文回答，不看資料庫裡舊的語言設定
    result = {"quick_replies": []}

    # 1. 設定指令（按鈕是中文，不拿來判斷語言）
    new_id = parse_identity(text)
    if new_id:
        db.set_user(uh, identity=new_id)
        known = new_id
        if only_identity(text):
            result.update(answer=(f"好的，已設定為「{new_id}」。有問題直接打字，或點下方選單。" if lang == "zh" else
                                  f"Got it: {ID_EN[new_id]}. Type your question or tap the menu."),
                          route="setting", sources=[])
            return result
    if text.lower() in SWITCH_ID:
        cur = f"目前身分：{known}。" if known else ""
        cur_en = f"Current: {ID_EN[known]}. " if known else ""
        result.update(answer=f"{cur}請點下面的按鈕選擇新的身分。\n{cur_en}Tap a button below to choose who you are.",
                      route="setting", sources=[], quick_replies=identity_chips())
        return result
    if text.lower() in {"english", "language", "切換語言", "中文"}:
        result.update(answer="Replies are in English. You can ask your question in English or Chinese.",
                      route="setting", sources=[])
        return result
    identity = known  # 沒選身分也沒關係：承辦人會從問題內容判斷（contacts.guess_identity）

    # 2. 高風險 → 轉人工
    cat = detect_high_risk(text)
    if cat:
        db.log_handoff(uh, cat, text)
        result.update(answer=handoff_message(cat, lang, identity, text), route="handoff", sources=[])
        result["calls"] = [{"label": contacts.SECURITY_CALL[lang], "tel": contacts.SECURITY_CALL["tel"]},
                           contacts.call_button(identity, lang, text)]
        if cat == "自我傷害":
            result["calls"].insert(0, {"label": contacts.CRISIS_CALL[lang], "tel": contacts.CRISIS_CALL["tel"]})
        if cat in ("自我傷害", "受傷就醫"):
            result["calls"].insert(0, {"label": contacts.AMBULANCE_CALL[lang], "tel": contacts.AMBULANCE_CALL["tel"]})
        if cat in ("自我傷害", "心理困擾"):
            result["calls"].append({"label": contacts.COUNSELING_CALL[lang], "tel": contacts.COUNSELING_CALL["tel"]})
    elif PRIVACY_ASK.search(text):
        result.update(answer=PRIVACY_REPLY[lang], route="privacy", sources=[])
    elif detect_smalltalk(text):
        kind = detect_smalltalk(text)
        zh, en = SMALLTALK_REPLY[kind]
        result.update(answer=zh if lang == "zh" else en, route="chat", sources=[])
        if kind == "bad":
            result["calls"] = [contacts.call_button(identity, lang)]
    # 3. 選單
    elif menu_key(text):
        key = menu_key(text)
        q = MENU_QUERY[lang].format(t=TOPIC_ZH[key] if lang == "zh" else TOPIC_EN[key])
        # 「權益與規定」橫跨居留、工作、保險好幾個主題，不限定主題，只靠關鍵字找
        topic = key if key in config.MENU_TOPICS else None
        result.update(rag.answer(q, identity, lang, topic=topic, quota_key=limit_key or uh, search=MENU_SEARCH[key]))
        zh_q, en_q = MENU_SUGGEST.get(key, ([], []))
        if result["route"] == "no_data" and (zh_q or en_q):
            # 主題太廣、AI 整理不出重點時，不說「沒有資料」，改成列出常見問題讓學生點
            result.pop("answered", None)
            name = TOPIC_ZH[key].split("（")[0] if lang == "zh" else MENU_NAME_EN.get(key, text)
            result.update(answer=(f"「{name}」常見的問題在下面，點一個就能問，也可以直接打字。" if lang == "zh" else
                                  f"Here are common questions about {name}. Tap one, or type your own question."),
                          route="rag", sources=[])
        elif result["route"] == "no_data" and not result.pop("answered", False):
            result["answer"] = (f"「{text}」這個分類目前還沒有整理好的最新資料，我們正在補充。\n"
                                f"有急事可以先問：{contacts.contact_line(identity, lang)}") if lang == "zh" else result["answer"]
        if result["route"] in {"rag", "menu"}:
            result["quick_replies"] = zh_q if lang == "zh" else en_q
        result["route"] = "menu" if result["route"] == "rag" else result["route"]
    # 4. 問得太籠統的費用問題：先請學生選是哪一種費用
    elif VAGUE_PAY.match(text):
        result.update(answer="What do you need to pay? Tap one below, or type your question in full "
                             "(for example: How do I pay my tuition fees?).",
                      route="menu", sources=[], quick_replies=PAY_CHOICES)
    elif VAGUE_FEE.match(text):
        result.update(answer="Which fee do you mean? Tap one below, or type your question in full "
                             "(for example: How much is the dorm fee?).",
                      route="menu", sources=[], quick_replies=FEE_CHOICES)
    # 5. 一般問題
    else:
        result.update(rag.answer(text, identity, lang, history=db.recent_turns(uh), quota_key=limit_key or uh))
    result.pop("answered", None)

    # 需要找人的情況，附上一鍵撥號（撥總機後自動轉分機）
    if result["route"] in {"no_data", "ai_off", "limited", "llm_error"} and not result.get("calls"):
        result["calls"] = [contacts.call_button(identity, lang, text)]

    # 不再追問身分，也不顯示身分按鈕

    db.log_message(uh, channel, text, result["answer"], result["route"], result["sources"],
                   int((time.time() - t0) * 1000))
    return result
