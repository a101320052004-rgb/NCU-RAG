"""建立 LINE 圖文選單：國際處提供的五格設計（2026-11-10）。

  ┌───────────┬───────────┬──────────────┐
  │ Food      │ Stay      │ Rights &     │
  ├───────────┼───────────┤ Regulations  │
  │ Transport │ Fun       │ （右邊整欄）  │
  └───────────┴───────────┴──────────────┘

圖片用同資料夾的 richmenu.jpg（2500×1686，LINE 規定 1MB 以內）。換設計時，換掉這張圖再執行一次即可覆蓋。
按下各格會送出英文文字（Food、Stay…），後端 router.py 的 MENU_BUTTONS 會把它對應到主題。
寫法參考 github.com/line/line-bot-sdk-python 的 rich menu 範例。

用法：python setup_richmenu.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import config

IMAGE = Path(__file__).with_name("richmenu.jpg")
W, H = 2500, 1686
CW, CH = W // 3, H // 2
# (按下去送出的文字, x, y, 寬, 高)
AREAS = [
    ("Food", 0, 0, CW, CH),
    ("Stay", CW, 0, CW, CH),
    ("Transport", 0, CH, CW, H - CH),
    ("Fun", CW, CH, CW, H - CH),
    ("Rights & Regulations", 2 * CW, 0, W - 2 * CW, H),
]


def main() -> int:
    if not config.LINE_CHANNEL_ACCESS_TOKEN:
        print("請先在 .env 填入 LINE_CHANNEL_ACCESS_TOKEN")
        return 1
    if not IMAGE.exists():
        print(f"找不到選單圖片：{IMAGE}")
        return 1
    data = IMAGE.read_bytes()
    if len(data) > 1_000_000:
        print("選單圖片超過 1MB，LINE 不收，請先壓縮。")
        return 1
    from linebot.v3.messaging import (ApiClient, Configuration, MessageAction, MessagingApi,
                                      MessagingApiBlob, RichMenuArea, RichMenuBounds,
                                      RichMenuRequest, RichMenuSize)
    conf = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
    areas = [RichMenuArea(bounds=RichMenuBounds(x=x, y=y, width=w, height=h),
                          action=MessageAction(label=text[:20], text=text))
             for text, x, y, w, h in AREAS]
    req = RichMenuRequest(size=RichMenuSize(width=W, height=H), selected=True,
                          name="國際生選單 2026-11", chat_bar_text="Menu 選單", areas=areas)
    with ApiClient(conf) as client:
        api, blob = MessagingApi(client), MessagingApiBlob(client)
        for old in api.get_rich_menu_list().richmenus:  # 清掉舊的，避免累積
            api.delete_rich_menu(old.rich_menu_id)
        menu_id = api.create_rich_menu(rich_menu_request=req).rich_menu_id
        blob.set_rich_menu_image(rich_menu_id=menu_id, body=bytearray(data),
                                 _headers={"Content-Type": "image/jpeg"})
        api.set_default_rich_menu(menu_id)
    print(f"完成：圖文選單 {menu_id} 已設為預設。打開 LINE 聊天室就會看到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
