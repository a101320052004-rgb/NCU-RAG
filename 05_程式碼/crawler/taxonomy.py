"""分類字典：主題（食衣住行育樂＋國際生高頻業務）與適用身分。

要調整分類，改這個檔案的關鍵字即可，不必動主程式。
關鍵字不分大小寫；中英文都列，因為國際處有中英兩版網站。
"""

# 主題：key 是分類名稱，value 是關鍵字清單
TOPICS = {
    "食": ["餐廳", "美食", "飲食", "學餐", "餐飲", "素食", "清真", "伙食",
           "food", "dining", "restaurant", "cafeteria", "halal", "vegetarian", "meal"],
    "衣": ["洗衣", "衣物", "服裝", "天氣", "氣候", "颱風", "寒流",
           "laundry", "clothing", "weather", "climate", "typhoon"],
    "住": ["宿舍", "住宿", "租屋", "賃居", "床位", "退宿", "入住", "房租",
           "dorm", "dormitory", "housing", "accommodation", "rent", "check-in", "check-out"],
    "行": ["交通", "公車", "客運", "高鐵", "火車", "機場", "接機", "腳踏車", "自行車",
           "停車", "機車", "駕照", "捷運",
           "transport", "bus", "shuttle", "airport", "bike", "bicycle", "parking",
           "driver", "mrt", "hsr", "train", "pick-up"],
    "育": ["選課", "課程", "學分", "註冊", "學籍", "成績", "華語", "中文課", "圖書館",
           "論文", "休學", "退學", "畢業", "校曆",
           "course", "registration", "credit", "transcript", "library", "chinese language",
           "graduation", "academic calendar", "enrollment"],
    "樂": ["社團", "活動", "文化交流", "旅遊", "節慶", "體育", "健身", "導覽", "思鄉",
           "club", "activity", "activities", "festival", "culture", "cultural", "sports",
           "gym", "trip", "tour"],
    "居留簽證": ["居留證", "居留", "簽證", "移民署", "出入境", "入出境", "停留",
                 "arc", "visa", "residence permit", "immigration", "entry", "exit"],
    "工作": ["工作證", "工作許可", "工讀", "打工", "實習",
             "work permit", "part-time", "employment", "internship"],
    "獎助學金": ["獎學金", "助學金", "獎助", "學雜費減免", "補助",
                 "scholarship", "stipend", "tuition waiver", "financial aid", "grant"],
    "保險醫療": ["健保", "保險", "就醫", "醫院", "診所", "健康檢查", "體檢", "衛保",
                 "insurance", "nhi", "hospital", "clinic", "health check", "medical", "doctor"],
    "緊急安全": ["緊急", "校安", "報案", "意外", "受傷", "急診", "24小時", "24 小時",
                 "emergency", "safety", "accident", "injury", "24-hour", "24 hour", "police"],
    "新生": ["新生", "報到", "入學", "新生訓練", "新生手冊", "抵台", "來台",
             "orientation", "new student", "arrival", "freshman", "incoming"],
}

# 答錯後果嚴重的主題：知識庫標記 high，之後聊天機器人要附官方連結或轉人工
HIGH_RISK_TOPICS = {"居留簽證", "工作", "緊急安全"}

# 適用身分
IDENTITIES = {
    "外籍學位生": ["外籍學位生", "外國學生", "外籍生", "學位生",
                   "degree student", "international degree", "foreign student"],
    "交換生": ["外籍交換生", "來校交換", "陸港澳交換生", "exchange student", "incoming exchange"],
    "僑生": ["僑生", "港澳", "overseas chinese", "hong kong", "macau", "macao"],
    "陸生": ["陸生", "大陸地區", "大陸學生", "陸港澳", "mainland"],
    "訪問研修生": ["訪問學生", "研修生", "短期研習", "短期實習", "visiting student", "visiting"],
}
DEFAULT_IDENTITY = "全體境外生"

# 一個主題至少要命中幾次才算數，避免頁尾、選單的字誤判
MIN_TOPIC_HITS = 2
