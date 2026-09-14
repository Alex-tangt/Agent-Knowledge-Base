"""抓取真实法典全文到 legal_web/data/raw/。

来源均为公开、合规的官方或公开转载站点（中国人大网 / 最高检 / 部委镜像 / 公开转载），
文本为法律法规全文，属公共领域，仅供学习参考。
"""
import os, sys, requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")
RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
os.makedirs(RAW, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (文件名, URL, 来源说明)
LAWS = [
    ("中华人民共和国刑法.md", "http://www.npc.gov.cn/npc/c1773/c1848/c21114/c25714/c25716/201905/t20190522_46193.html", "中国人大网（现行有效，含历次修正案）"),
    ("中华人民共和国劳动合同法.md", "http://www.npc.gov.cn/npc/c2/c183/c198/201905/t20190522_26978.html", "中国人大网（2012修正）"),
    ("中华人民共和国个人所得税法.md", "http://www.npc.gov.cn/zgrdw/npc/xinwen/2018-09/05/content_2060671.htm", "中国人大网（2018修正）"),
    ("中华人民共和国公司法(2023修订).md", "https://www.waizi.org.cn/doc/215381.html", "2023修订全文（主席令第十五号，公开转载）"),
    ("中华人民共和国行政处罚法.md", "http://www.npc.gov.cn/c2/c30834/202101/t20210122_309857.html", "中国人大网（2021修订）"),
    ("中华人民共和国行政许可法.md", "http://www.npc.gov.cn/zgrdw/npc/xinwen/2019-05/07/content_2086830.htm", "中国人大网（2019修正）"),
    ("中华人民共和国道路交通安全法.md", "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/bgt/art/2023/art_79dc72ea621f4a9b8adec327abf5d0e1.html", "国家市场监督管理总局镜像（2021修正）"),
    ("中华人民共和国消费者权益保护法.md", "http://www.npc.gov.cn/zgrdw/npc/xinwen/2013-10/26/content_1811773.htm", "中国人大网（2013修正）"),
    ("中华人民共和国社会保险法.md", "http://www.npc.gov.cn/cwhhdbdh/c4186/c14354/c14359/201905/t20190523_401570.html", "中国人大网（2018修正）"),
    ("中华人民共和国劳动法.md", "http://www.npc.gov.cn/npc/c3001/c3003/201905/t20190522_20595.html", "中国人大网（2018修正）"),
    ("中华人民共和国个人信息保护法.md", "https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm", "国家网信办（2021）"),
    ("中华人民共和国未成年人保护法.md", "http://www.npc.gov.cn/c2/c30834/202010/t20201017_308279.html", "中国人大网（2020修订）"),
    ("中华人民共和国行政复议法.md", "http://www.npc.gov.cn/c2/c30834/202309/t20230901_431409.html", "中国人大网（2023修订）"),
    ("中华人民共和国食品安全法.md", "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=90359", "商务部镜像（2021修正）"),
    ("中华人民共和国妇女权益保障法.md", "http://www.npc.gov.cn/c2/c30834/202210/t20221030_320091.html", "中国人大网（2022修订）"),
    ("中华人民共和国反电信网络诈骗法.md", "http://www.npc.gov.cn/c2/c30834/202209/t20220902_319186.html", "中国人大网（2022）"),
    ("中华人民共和国行政强制法.md", "http://www.npc.gov.cn/c4146/c10825/201905/t20190522_51060.html", "中国人大网（2011）"),
    ("中华人民共和国国家赔偿法.md", "https://www.spp.gov.cn/sscx/201404/t20140424_71280.shtml", "最高人民检察院（2012修正）"),
]

def decode_html(r):
    """这些政府站点很多不在 HTTP 头声明 charset（requests 会回退到 ISO-8859-1
    造成乱码）。原始字节实为 UTF-8，这里显式解码，必要时回退 GBK/GB18030。"""
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            t = r.content.decode(enc)
        except Exception:
            continue
        if t.count("�") == 0:
            return t
    # 兜底：用 apparent_encoding
    r.encoding = r.apparent_encoding
    return r.text

def extract(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    candidates = [
        soup.select_one("#detail"), soup.select_one(".detail"),
        soup.select_one(".jc-a"), soup.select_one(".TRS_Editor"),
        soup.select_one(".article"), soup.select_one('div[class*="content"]'),
        soup.select_one('div[id*="content"]'), soup.select_one('div[class*="article"]'),
    ]
    best = ""
    for c in candidates:
        if c:
            txt = c.get_text("\n", strip=True)
            if len(txt) > len(best):
                best = txt
    if len(best) < 500:
        ps = [p.get_text(strip=True) for p in soup.find_all("p")]
        ps = [p for p in ps if len(p) > 5]
        best = "\n".join(ps)
    if len(best) < 500:
        best = soup.get_text("\n", strip=True)
    return best

def main():
    for fname, url, note in LAWS:
        try:
            r = requests.get(url, timeout=30, headers=UA)
            text = extract(decode_html(r))
            if len(text) < 1000:
                print(f"!!! SKIP {fname}: only {len(text)} chars (likely JS-rendered/blocked)")
                continue
            title = fname[:-3]
            header = f"# {title}\n\n> 来源：{note} ｜ 本文件为公开法律法规全文，仅供学习参考，不构成法律意见。\n\n"
            out = header + text + "\n"
            with open(os.path.join(RAW, fname), "w", encoding="utf-8") as f:
                f.write(out)
            print(f"OK  {fname:30s} {len(out):7d} chars")
        except Exception as e:
            print(f"!!! ERR {fname}: {type(e).__name__}: {str(e)[:80]}")

if __name__ == "__main__":
    main()
