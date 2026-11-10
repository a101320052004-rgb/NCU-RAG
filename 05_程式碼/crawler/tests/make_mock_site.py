"""產生一個模擬國際處網站（RPAGE 版型的簡化版），用來測試爬蟲。"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "p").mkdir(parents=True, exist_ok=True)
(root / "var").mkdir(exist_ok=True)

NAV = """<header><nav><ul>
<li><a href="/p/17-1026.php?Lang=zh-tw">網站導覽</a></li><li>宿舍 交通 獎學金 居留證 保險 社團</li>
<li><a href="https://www.facebook.com/x">FB</a></li><li><a href="javascript:void(0)">選單</a></li>
</ul></nav></header>"""
FOOT = "<footer>地址：桃園市中壢區中大路300號 宿舍 交通 獎學金 保險 社團</footer>"


def page(name, title, body):
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title} - 國立中央大學國際事務處</title></head>
<body>{NAV}<div class="main"><h2 class="hdline">{title}</h2><div class="mpgdetail">{body}</div></div>{FOOT}</body></html>"""
    (root / "p" / name).write_text(html, encoding="utf-8")


page("17-1026.php", "網站導覽", """
<p><a href="/p/412-1026-1886.php?Lang=zh-tw">獎助學金</a></p>
<p><a href="/p/412-1026-1879.php?Lang=zh-tw&amp;foo=1#top">校園生活資訊</a></p>
<p><a href="/p/412-1026-1884.php?Lang=zh-tw">僑生保險</a></p>
<p><a href="/p/412-1026-9999.php?Lang=zh-tw">內部頁（robots 禁止）</a></p>
<p><a href="/p/412-1026-2204.php?Lang=en">Work Permit</a></p>
<p><a href="/p/412-1026-0404.php?Lang=zh-tw">壞掉的連結</a></p>
<p><a href="https://other.example.com/p/412-1026-1.php">外站</a></p>""")

page("412-1026-1886.php", "獎助學金", """
<p>僑生獎助學金申請，申請期限至2026年9月24日止。外籍學位生請參考英文版。</p>
<p>獎學金申請須檢附成績單，獎學金核定後於每學期撥款。</p>
<p><a href="/p/406-1026-50001,r342.php?Lang=zh-tw">115學年度僑生獎學金公告</a></p>
<p><a href="/var/file/26/1026/scholarship.pdf">獎學金申請表（PDF）</a></p>
<p><a href="/var/file/26/1026/guide.docx">申請須知（Word）</a></p>
<p><a href="/var/file/26/1026/old.doc">舊版表單（doc）</a></p>""")

page("406-1026-50001,r342.php", "115學年度僑生獎學金公告",
     "<p>115學年度第1學期僑生獎學金開始受理，請於115年10月15日前至國際處繳交。獎學金名額有限。</p>" * 3)

page("412-1026-1879.php", "校園生活資訊", """
<p>新生抵台後可先至學生宿舍辦理入住，宿舍入住時間請見住宿服務組公告。</p>
<p>校園交通：可搭乘公車132、133往返中壢火車站；腳踏車請至總務處申請停車證。</p>
<p>校內餐廳提供素食與清真餐點，學餐營業時間為上午7點至晚上8點。</p>
<p>社團活動與文化交流活動每學期舉辦，歡迎參加。</p>""")

page("412-1026-1884.php", "僑生保險", """
<p>僑生保險：109-110年度承保公司為國泰人壽，僑保保費由學生自行負擔560元。</p>
<p>健保：在台居留滿6個月後須加入全民健保，保險費每6個月4,494元。就醫請攜帶健保卡至醫院或診所。</p>""")

page("412-1026-9999.php", "內部頁", "<p>不應被爬到</p>")

page("412-1026-2204.php", "Work Permit", """
<p>International degree students and exchange students must apply for a work permit before part-time work.
The work permit is valid for at most 1 year. Students may work up to 20 hours per week.</p>
<p>Working without a work permit may lead to a fine and deportation. Apply through the Ministry of Labor work permit system.</p>""")

(root / "robots.txt").write_text("User-agent: *\nDisallow: /p/412-1026-9999.php\nCrawl-delay: 1\n")

# 附件
from reportlab.pdfgen import canvas  # noqa: E402
c = canvas.Canvas(str(root / "var" / "scholarship.pdf"))
c.drawString(72, 750, "Scholarship application form for international degree students.")
c.drawString(72, 730, "Deadline: 2026/09/24. Submit to Office of International Affairs.")
c.save()

import docx  # noqa: E402
d = docx.Document()
d.add_paragraph("獎學金申請須知：僑生與外籍學位生皆可申請，請檢附成績單與居留證影本。")
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).text, t.cell(0, 1).text = "項目", "金額"
t.cell(1, 0).text, t.cell(1, 1).text = "學士班", "每月 10,000 元"
d.save(str(root / "var" / "guide.docx"))
(root / "var" / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0fake-old-doc")
print("mock site ready")
