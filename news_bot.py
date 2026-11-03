"""Trading yangiliklari boti.

  FXStreet     : oltin, neft, dollar yangiliklari. Har 5 daqiqada tekshiriladi, yangi chiqsa darhol kanalga yuboriladi.
  Forex Factory: AQSH (USD) uchun yuqori ta'sirli (high impact) voqealar. Har kuni 00:00 da (O'zbekiston vaqti) bitta post.

Rasm: avval FXStreet maqolasining o'z rasmi (og:image) olinadi, bo'lmasa brendli kartochka chiziladi.
Matn: tarjima + AI xulosa + manba (ANTHROPIC_API_KEY bo'lmasa, original inglizcha matn yuboriladi).

Buyruqlar:
    python news_bot.py fxstreet               # bir marta tekshiradi (har 5 daqiqada ishga tushiring)
    python news_bot.py forexfactory           # bugungi kunning digest postini yuboradi (00:00 da ishga tushiring)
    python news_bot.py loop                   # serverda/kompyuterda doimiy ishlaydi: har 5 daqiqada FXStreet, 00:00 da Forex Factory
Sinash (yubormaydi, out/ papkaga yozadi):
    python news_bot.py fxstreet --dry-run --rss-file fixtures/sample_fxstreet.xml
    python news_bot.py forexfactory --dry-run --ff-file fixtures/sample_ff_SYNTHETIC.json --date 2026-11-04

Muhit o'zgaruvchilari:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL   (yuborish uchun shart)
    ANTHROPIC_API_KEY                      (ixtiyoriy: tarjima va xulosa uchun)
    ANTHROPIC_MODEL                        (ixtiyoriy, standart: claude-haiku-5-5)
"""
import argparse
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

# ---------------- SOZLAMALAR ----------------
UZ = timezone(timedelta(hours=5))             # O'zbekiston vaqti
HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")

RSS_URLS = ["https://xml.fxstreet.com/news/forex-news/index.xml",
            "https://www.fxstreet.com/rss/news"]
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]
FF_NEXT_URLS = ["https://nfs.faireconomy.media/ff_calendar_nextweek.json",
                "https://cdn-nfs.faireconomy.media/ff_calendar_nextweek.json"]
FF_LINK = "https://www.forexfactory.com/calendar"

MAX_PER_RUN = 5          # bir tekshiruvda ko'pi bilan nechta post
AGE_LIMIT_H = 3          # shundan eski yangiliklar yuborilmaydi (uzoq to'xtashdan keyin to'lib ketmasligi uchun)
CAPTION_LIMIT = 1024
SEEN_CAP = 1500
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# mavzular: (kod, nom, emoji, naqsh, kartochka ranglari: yorliq foni, yorliq matni)
TOPICS = [
    ("gold", "Oltin", "🟡", re.compile(r"\b(gold|xau|bullion)\b", re.I), "#412402", "#ef9f27"),
    ("oil", "Neft", "🛢", re.compile(r"\b(oil|wti|brent|crude|opec\+?)\b", re.I), "#2c2c2a", "#d3d1c7"),
    ("usd", "Dollar", "💵", re.compile(
        r"\b(us dollar|usd|dxy|dollar index|greenback|fed|federal reserve|fomc|treasury yields?)\b", re.I),
     "#04342c", "#5dcaa5"),
]
UP_WORDS = re.compile(r"\b(rebounds?|rises?|rises|gains?|climbs?|jumps?|surges?|rallies|rally|higher|recovers?|advances?|extends gains)\b", re.I)
DOWN_WORDS = re.compile(r"\b(drops?|falls?|slips?|tumbles?|plunges?|declines?|lower|slumps?|retreats?|dips?|tanks?|weakens?|depreciates?)\b", re.I)
NO_AI = False


# ---------------- UMUMIY ----------------
def log(*a):
    print(*a, flush=True)


def http_get(urls, **kw):
    """Birinchi ishlagan manzildan javob qaytaradi, bo'lmasa None. Xato matniga URL/token chiqarmaydi."""
    for u in urls if isinstance(urls, list) else [urls]:
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=25, **kw)
            if r.ok:
                return r
            log(f"  GET {u.split('/')[2]}: HTTP {r.status_code}")
        except Exception as e:
            log(f"  GET {u.split('/')[2]}: {type(e).__name__}")
    return None


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return None


def save_state(st):
    st["seen"] = st["seen"][-SEEN_CAP:]
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    tmp.replace(STATE_FILE)


# ---------------- AI (tarjima va xulosa) ----------------
def ai_json(prompt, max_tokens=1200):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if NO_AI or not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        if not r.ok:
            log(f"  AI xatosi: HTTP {r.status_code}")
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        a, b = text.find("{"), text.rfind("}")
        return json.loads(text[a:b + 1])
    except Exception as e:
        log(f"  AI xatosi: {type(e).__name__}")
        return None


def ai_news(title, summary):
    p = f"""Sen trading yangiliklari tarjimonisan. Quyidagi inglizcha yangilikni o'zbek tiliga (lotin yozuvi) tarjima qil.
Qoidalar:
- Tarjima aniq bo'lsin. Raqamlar, tikerlar (XAU/USD, WTI), ismlar va lavozimlar o'zgarmasin. Matnda yo'q narsani qo'shma.
- "title": sarlavha tarjimasi (qisqa, 90 belgigacha).
- "text": matn tarjimasi.
- "summary": 1-2 gapli xulosa. Faqat berilgan matndan chiqadigan xulosa, bashorat yoki moliyaviy maslahat yo'q.
Faqat JSON qaytar: {{"title": "...", "text": "...", "summary": "..."}}

SARLAVHA: {title}
MATN: {summary}"""
    return ai_json(p)


def ai_events(rows, day_label):
    lst = "\n".join(f"{i}. {r['title']} (prognoz: {r['forecast'] or '-'}, oldingi: {r['previous'] or '-'})"
                    for i, r in enumerate(rows))
    p = f"""Sen iqtisodiy kalendar tarjimonisan. Quyida {day_label} kuni AQSH (USD) uchun yuqori ta'sirli voqealar ro'yxati.
Qoidalar:
- "names": har bir voqea nomini o'zbek tiliga (lotin yozuvi) tarjima qil, mashhur qisqartmani qavsda qoldir (masalan "NFP", "CPI"). Tartib va soni ro'yxatdagidek bo'lsin.
- "summary": 1-2 gap: bugun nimalar kutilayotganini umumiy ayt. Faqat ro'yxatdan foydalan, bozor qaysi tomonga ketishini bashorat qilma, maslahat berma.
Faqat JSON qaytar: {{"names": ["...", "..."], "summary": "..."}}

{lst}"""
    return ai_json(p)


# ---------------- RASMLAR ----------------
def _font(size, bold=False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for p in (HERE / "fonts" / name, Path("/usr/share/fonts/truetype/dejavu") / name):
        if p.exists():
            return ImageFont.truetype(str(p), int(size))
    raise RuntimeError("Shrift topilmadi: fonts/ papkaga DejaVuSans.ttf qo'ying")


def _wrap(draw, text, font, max_w, max_lines):
    text = re.sub(r"(\d) (\d{3})\b", "\\1\u00a0\\2", " ".join(text.split()))   # '3 900' bir qatorda qolsin
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def article_image(url):
    """FXStreet maqolasining o'z rasmi (og:image). Sayt bot deb to'ssa yoki rasm yo'q bo'lsa None."""
    from PIL import Image
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=20)
        if not r.ok:
            return None
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', r.text, re.I))
        if not m:
            return None
        img_url = m.group(1)
        if re.search(r"logo|default|placeholder|open_graph", img_url, re.I):
            return None
        ir = requests.get(img_url, headers={"User-Agent": UA}, timeout=20)
        if not ir.ok or len(ir.content) < 8000:
            return None
        img = Image.open(io.BytesIO(ir.content)).convert("RGB")
        if img.width < 400:
            return None
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:
        log(f"  rasm olinmadi: {type(e).__name__}")
        return None


def _price(topic, title, summary):
    for text in (summary, title):          # matndagi narx aniqroq, sarlavhadagi yaxlitlangan
        if topic == "gold":
            m = re.search(r"\$\s?(\d{1,2},?\d{3}(?:\.\d+)?)", text)
            if m:
                v = float(m.group(1).replace(",", ""))
                if 1000 <= v <= 10000:
                    return "$" + f"{v:,.0f}".replace(",", " ")
        if topic == "oil":
            m = re.search(r"\$\s?(\d{2,3}(?:\.\d{1,2})?)\b", text)
            if m and 20 <= float(m.group(1)) <= 300:
                return "$" + m.group(1)
    return None


def _direction(title):
    up, down = bool(UP_WORDS.search(title)), bool(DOWN_WORDS.search(title))
    return "up" if up and not down else "down" if down and not up else None


def news_card(topic, title_uz, orig_title, orig_summary, pub_uz):
    from PIL import Image, ImageDraw
    S, W, H = 3, 340, 200
    code, name, _e, _rx, tag_bg, tag_fg = next(t for t in TOPICS if t[0] == topic)
    img = Image.new("RGB", (W * S, H * S), "#14172e")
    d = ImageDraw.Draw(img)
    tag = {"gold": "OLTIN · XAU/USD", "oil": "NEFT", "usd": "DOLLAR · USD"}[code]
    f_tag = _font(12 * S, True)
    tw = d.textlength(tag, font=f_tag)
    d.rounded_rectangle([16 * S, 16 * S, 16 * S + tw + 24 * S, 40 * S], radius=12 * S, fill=tag_bg)
    d.text((16 * S + 12 * S, 28 * S), tag, font=f_tag, fill=tag_fg, anchor="lm")
    price = _price(code, orig_title, orig_summary)
    big = price or name.upper()
    f_big = _font(40 * S, True)
    d.text((16 * S, 88 * S), big, font=f_big, fill="#f1efe8", anchor="ls")
    direction = _direction(orig_title) if price else None
    if direction:
        x = 16 * S + d.textlength(big, font=f_big) + 14 * S
        y0, y1 = 56 * S, 80 * S
        pts = ([(x, y1), (x + 12 * S, y0), (x + 24 * S, y1)] if direction == "up"
               else [(x, y0), (x + 12 * S, y1), (x + 24 * S, y0)])
        d.polygon(pts, fill="#5dcaa5" if direction == "up" else "#e24b4a")
    f_t = _font(15 * S)
    for i, ln in enumerate(_wrap(d, title_uz, f_t, 308 * S, 2)):
        d.text((16 * S, (118 + 20 * i) * S), ln, font=f_t, fill="#f1efe8", anchor="ls")
    d.line([(16 * S, 160 * S), (324 * S, 160 * S)], fill="#3c3489", width=S)
    f_s = _font(11 * S)
    d.text((16 * S, 182 * S), f"{pub_uz:%H:%M} · {pub_uz.day} {MONTH[pub_uz.month]} · O'zbekiston vaqti",
           font=f_s, fill="#b4b2a9", anchor="ls")
    d.text((324 * S, 182 * S), "FXStreet", font=f_s, fill="#b4b2a9", anchor="rs")
    return _png(img)


def ff_card(day, rows, names):
    from PIL import Image, ImageDraw
    S, W, H = 3, 340, 240
    img = Image.new("RGB", (W * S, H * S), "#14172e")
    d = ImageDraw.Draw(img)
    f_tag = _font(12 * S, True)
    tag = "AQSH · YUQORI TA'SIR"
    tw = d.textlength(tag, font=f_tag)
    d.rounded_rectangle([16 * S, 16 * S, 16 * S + tw + 24 * S, 40 * S], radius=12 * S, fill="#501313")
    d.text((28 * S, 28 * S), tag, font=f_tag, fill="#f09595", anchor="lm")
    d.text((16 * S, 78 * S), "Bugungi voqealar", font=_font(26 * S, True), fill="#f1efe8", anchor="ls")
    d.text((16 * S, 98 * S), f"{day.day} {MONTH[day.month]} {day.year}", font=_font(12 * S),
           fill="#b4b2a9", anchor="ls")
    f_r, f_rb = _font(13 * S), _font(13 * S, True)
    show = list(zip(rows, names))[:5]
    for i, (r, nm) in enumerate(show):
        y = (128 + 22 * i) * S
        d.text((16 * S, y), f"{r['uz']:%H:%M}", font=f_rb, fill="#ef9f27", anchor="ls")
        line = nm
        while d.textlength(line, font=f_r) > 252 * S and " " in line:
            line = line.rsplit(" ", 1)[0]
        d.text((68 * S, y), line, font=f_r, fill="#f1efe8", anchor="ls")
    if len(rows) > 5:
        d.text((68 * S, (128 + 22 * 5) * S), f"+ yana {len(rows) - 5} ta", font=f_r, fill="#b4b2a9", anchor="ls")
    d.text((16 * S, 228 * S), "O'zbekiston vaqti · Forex Factory", font=_font(11 * S), fill="#b4b2a9", anchor="ls")
    return _png(img)


MONTH = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
         "sentyabr", "oktyabr", "noyabr", "dekabr"]
WEEKDAY = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


# ---------------- TELEGRAM ----------------
def _tg(method, data, files=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
    except Exception as e:  # tokenli URL xato matniga tushib qolmasligi uchun
        log(f"  Telegram: ulanib bo'lmadi ({type(e).__name__})")
        return False
    if not r.ok:
        log(f"  Telegram xatosi {r.status_code}: {r.text}")
    return r.ok


def send(image, caption):
    chat = os.environ.get("TELEGRAM_CHANNEL", "")
    if not chat or not os.environ.get("TELEGRAM_BOT_TOKEN"):
        log("  TELEGRAM_BOT_TOKEN yoki TELEGRAM_CHANNEL o'rnatilmagan.")
        return False
    ext, mime = ("jpg", "image/jpeg") if image[:2] == b"\xff\xd8" else ("png", "image/png")
    files = {"photo": (f"post.{ext}", image, mime)}
    if len(caption) <= CAPTION_LIMIT:
        return _tg("sendPhoto", {"chat_id": chat, "caption": caption}, files)
    ok = _tg("sendPhoto", {"chat_id": chat}, files)
    return ok and _tg("sendMessage", {"chat_id": chat, "text": caption[:4000],
                                      "disable_web_page_preview": "true"})


def emit(kind, key, image, caption, dry_run, out_dir="out"):
    if dry_run:
        out = Path(out_dir)
        out.mkdir(exist_ok=True)
        ext = "jpg" if image[:2] == b"\xff\xd8" else "png"
        (out / f"{kind}_{key}.{ext}").write_bytes(image)
        (out / f"{kind}_{key}.txt").write_text(caption, encoding="utf-8")
        log(f"--- [{kind}] {key} ({len(caption)} belgi, rasm: {ext}) ---\n{caption}\n")
        return True
    return send(image, caption)


# ---------------- FXSTREET ----------------
NS = "{http://www.fxstreet.com/syndicate/rss/namespaces/}"


def parse_rss(content):
    root = ET.fromstring(content)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        try:
            dt = parsedate_to_datetime(re.sub(r"\sZ$", " +0000", pub))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        items.append({
            "id": (it.findtext("guid") or link).strip(),
            "title": " ".join((it.findtext("title") or "").split()),
            "summary": " ".join((it.findtext(NS + "summary") or "").split()),
            "link": link,
            "pub": dt,
        })
    return items


def classify(item):
    if "/cryptocurrencies/" in item["link"]:
        return None
    text = f"{item['title']} {item['summary']}"
    for t in TOPICS:
        if t[3].search(item["title"]):
            return t[0]
    for t in TOPICS:
        if t[3].search(text):
            return t[0]
    return None


def build_news_post(item, topic):
    pub_uz = item["pub"].astimezone(UZ)
    _c, name, emoji, *_ = next(t for t in TOPICS if t[0] == topic)
    ai = ai_news(item["title"], item["summary"])
    head = f"{emoji} {name} · FXStreet\n🕔 {pub_uz:%H:%M} (O'zbekiston vaqti)\n"
    if ai and ai.get("text"):
        title_uz = ai.get("title") or item["title"]
        body = f"\nTarjima\n{ai['text'].strip()}\n"
        if ai.get("summary"):
            body += f"\nAI xulosa\n{ai['summary'].strip()}\n"
    else:
        title_uz = item["title"]
        body = f"\n{item['title']}\n{item['summary']}\n\n(AI tarjima o'chiq: matn original inglizcha)\n"
    caption = f"{head}{body}\nManba: FXStreet\n{item['link']}"
    image = article_image(item["link"]) or news_card(topic, title_uz, item["title"], item["summary"], pub_uz)
    return image, caption


def run_fxstreet(args):
    test = bool(args.rss_file)
    if test:
        content = Path(args.rss_file).read_bytes()
    else:
        r = http_get(RSS_URLS)
        if r is None:
            log("FXStreet RSS ochilmadi.")
            return 1
        content = r.content
    items = parse_rss(content)
    log(f"FXStreet: {len(items)} ta yangilik o'qildi.")

    state = None if test else load_state()
    if not test and state is None:
        # birinchi ishga tushirish: eskilarini yubormaymiz, faqat eslab qolamiz
        state = {"seen": [i["id"] for i in items]}
        if args.dry_run:
            log("Birinchi ishga tushirish (dry-run): holat saqlanmadi.")
            return 0
        save_state(state)
        log("Birinchi ishga tushirish: mavjud yangiliklar belgilandi, post yuborilmadi.")
        return 0

    seen = set(state["seen"]) if state else set()
    now = datetime.now(timezone.utc)
    fresh = [i for i in items if i["id"] not in seen
             and (test or now - i["pub"] <= timedelta(hours=AGE_LIMIT_H))]
    fresh.sort(key=lambda i: i["pub"])
    todo = []
    for i in fresh:
        t = classify(i)
        if t:
            todo.append((i, t))
    log(f"Yangi: {len(fresh)}, mavzuga mos: {len(todo)}.")

    failed = False
    failed_ids = set()
    for item, topic in todo[:MAX_PER_RUN]:
        log(f"Post: [{topic}] {item['title']}")
        image, caption = build_news_post(item, topic)
        if not emit("fxstreet", item["id"][:8], image, caption, args.dry_run or test):
            failed = True
            failed_ids.add(item["id"])
    if not test and not args.dry_run:
        postponed = {i["id"] for i, _ in todo[MAX_PER_RUN:]}   # navbatdagilari keyingi safar yuboriladi
        keep = postponed | failed_ids                           # yuborilmaganlar qayta uriniladi
        for i in items:
            if i["id"] not in keep and i["id"] not in seen:
                state["seen"].append(i["id"])
        save_state(state)
    return 1 if failed else 0


# ---------------- FOREX FACTORY ----------------
def parse_ff(data):
    rows = []
    for e in data:
        cur = (e.get("country") or e.get("currency") or "").upper()
        imp = (e.get("impact") or "").strip().lower()
        if cur != "USD" or imp != "high":
            continue
        try:
            dt = datetime.fromisoformat(e["date"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=-5)))
        except Exception:
            continue
        rows.append({"title": (e.get("title") or "").strip(),
                     "forecast": (e.get("forecast") or "").strip(),
                     "previous": (e.get("previous") or "").strip(),
                     "uz": dt.astimezone(UZ)})
    rows.sort(key=lambda r: r["uz"])
    return rows


def load_ff(args):
    if args.ff_file:
        return json.loads(Path(args.ff_file).read_text())
    data = []
    for urls in (FF_URLS, FF_NEXT_URLS):
        r = http_get(urls)
        if r is not None:
            try:
                data += r.json()
            except Exception:
                log("  Forex Factory javobi JSON emas.")
    return data


def run_forexfactory(args):
    day = date.fromisoformat(args.date) if args.date else datetime.now(UZ).date()
    data = load_ff(args)
    if not data:
        log("Forex Factory ma'lumoti olinmadi.")
        return 1
    rows = [r for r in parse_ff(data) if r["uz"].date() == day]
    log(f"Forex Factory: {day} uchun {len(rows)} ta USD high impact voqea.")
    if not rows:
        log("Bugun post yo'q.")
        return 0
    ai = ai_events(rows, f"{day.day} {MONTH[day.month]}")
    names = (ai or {}).get("names")
    if not isinstance(names, list) or len(names) != len(rows):
        names = [r["title"] for r in rows]
    L = [f"📅 {WEEKDAY[day.weekday()]}, {day.day} {MONTH[day.month]}: AQSH uchun yuqori ta'sirli voqealar",
         "🕔 Vaqt: O'zbekiston vaqti", ""]
    for r, nm in zip(rows, names):
        L.append(f"{r['uz']:%H:%M}  {nm}")
        L.append(f"        Prognoz: {r['forecast'] or '-'} · Oldingi: {r['previous'] or '-'}")
    if ai and ai.get("summary"):
        L += ["", "AI xulosa", ai["summary"].strip()]
    else:
        L += ["", "(AI tarjima o'chiq: nomlar original inglizcha)"]
    L += ["", f"Manba: Forex Factory\n{FF_LINK}"]
    caption = "\n".join(L)
    image = ff_card(day, rows, names)
    ok = emit("forexfactory", str(day), image, caption, args.dry_run or bool(args.ff_file))
    return 0 if ok else 1


# ---------------- DOIMIY REJIM ----------------
def run_loop(args):
    """Serverda doimiy ishlaydi: har 5 daqiqada FXStreet, har kuni 00:00 da Forex Factory."""
    st = load_state() or {"seen": []}
    last_ff = st.get("ff_date")
    log("Doimiy rejim boshlandi. To'xtatish: Ctrl+C")
    while True:
        try:
            run_fxstreet(args)
            now = datetime.now(UZ)
            if now.hour == 0 and now.minute < 30 and last_ff != str(now.date()):
                a = argparse.Namespace(**{**vars(args), "date": None, "ff_file": None})
                if run_forexfactory(a) == 0:
                    last_ff = str(now.date())
                    st = load_state() or {"seen": []}
                    st["ff_date"] = last_ff
                    save_state(st)
        except Exception as e:
            log(f"Xato: {type(e).__name__}")
        time.sleep(300)


# ---------------- ASOSIY ----------------
def main():
    global NO_AI
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fxstreet", "forexfactory", "loop"])
    ap.add_argument("--dry-run", action="store_true", help="Telegramga yubormaydi, out/ ga yozadi")
    ap.add_argument("--no-ai", action="store_true", help="AI tarjimasiz")
    ap.add_argument("--rss-file", help="sinash uchun: RSS fayl")
    ap.add_argument("--ff-file", help="sinash uchun: Forex Factory JSON fayl")
    ap.add_argument("--date", help="Forex Factory uchun sana YYYY-MM-DD")
    a = ap.parse_args()
    NO_AI = a.no_ai
    fn = {"fxstreet": run_fxstreet, "forexfactory": run_forexfactory, "loop": run_loop}[a.cmd]
    return fn(a)


if __name__ == "__main__":
    sys.exit(main())
