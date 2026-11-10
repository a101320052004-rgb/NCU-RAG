"""小組內部測試頁（Streamlit，github.com/streamlit/streamlit）。
先啟動後端：uvicorn main:app --port 8000
再執行：streamlit run test_ui.py
"""
import httpx
import streamlit as st

API = "http://127.0.0.1:8000/api/chat"
IDENTITIES = ["（未設定）", "外籍學位生", "交換生", "僑生", "陸生", "訪問研修生"]
ROUTE_LABEL = {"rag": "RAG 問答", "menu": "選單懶人包", "handoff": "轉人工",
               "no_data": "查無有效資料", "setting": "設定"}

st.set_page_config(page_title="國際生小幫手 測試頁", page_icon="💬")
st.title("國際生小幫手 測試頁")
st.caption("只給小組測試回答品質用。每則回答下方會顯示走了哪條路、用了哪些來源。")

with st.sidebar:
    identity = st.selectbox("模擬學生身分", IDENTITIES)
    st.markdown("**選單快捷鍵**")
    cols = st.columns(3)
    clicked = None
    for i, t in enumerate(["食", "衣", "住", "行", "育", "樂"]):
        if cols[i % 3].button(t, use_container_width=True):
            clicked = t
    st.markdown("**測試題（訪談中的真實情境）**")
    samples = ["獎學金續領什麼時候截止？", "腳踏車要貼貼紙嗎？沒貼會怎樣？",
               "My ARC expires next month, what should I do?", "宿舍停水要去哪裡看公告？",
               "I got injured on my bike, what should I do?"]
    for s in samples:
        if st.button(s, use_container_width=True):
            clicked = s
    if st.button("清除對話"):
        st.session_state.history = []

st.session_state.setdefault("history", [])
for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])
        if turn.get("meta"):
            st.caption(turn["meta"])

prompt = st.chat_input("輸入問題，中英文都可以") or clicked
if prompt:
    st.session_state.history.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            r = httpx.post(API, json={"message": prompt,
                                      "identity": None if identity.startswith("（") else identity},
                           timeout=60).json()
            text = r["answer"]
            meta = f"路線：{ROUTE_LABEL.get(r['route'], r['route'])}"
            if r.get("sources"):
                meta += "｜來源：" + "；".join(f"{s['title']}（{s['updated']}）" for s in r["sources"])
        except Exception as e:  # noqa: BLE001
            text, meta = f"連不到後端：{e}。請確認已執行 uvicorn main:app --port 8000", ""
        st.markdown(text.replace("\n", "  \n"))
        st.caption(meta)
    st.session_state.history.append({"role": "assistant", "text": text.replace("\n", "  \n"), "meta": meta})
