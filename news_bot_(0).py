"""Trading yangiliklari boti.

  FXStreet     : oltin, neft va dollar (DXY, Fed, FOMC) yangiliklari. Har 5 daqiqada tekshiriladi, yangi chiqsa darhol kanalga yuboriladi.
  Forex Factory: AQSH (USD) uchun yuqori ta'sirli (high impact) voqealar. Har kuni 00:00 da (Toshkent vaqti) bitta post.

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
    GEMINI_API_KEY                         (ixtiyoriy, BEPUL reja bor: tarjima va xulosa uchun, aistudio.google.com)
    ANTHROPIC_API_KEY                      (ixtiyoriy, pullik: ikkalasi bo'lsa Claude ishlatiladi)
    GEMINI_MODEL / ANTHROPIC_MODEL         (ixtiyoriy; standart: gemini-2.5-flash-lite / claude-haiku-5-5)
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
UZ = timezone(timedelta(hours=5))             # Toshkent vaqti
HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "")
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-flash-latest"]

RSS_URLS = ["https://xml.fxstreet.com/news/forex-news/index.xml",
            "https://www.fxstreet.com/rss/news"]
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]
FF_NEXT_URLS = ["https://nfs.faireconomy.media/ff_calendar_nextweek.json",
                "https://cdn-nfs.faireconomy.media/ff_calendar_nextweek.json"]
FF_LINK = "https://www.forexfactory.com/calendar"
INV_URLS = ["https://sslecal2.investing.com/?columns=exc_currency,exc_importance,exc_actual,exc_forecast,exc_previous"
            "&importance=2,3&countries=5&calType=week&timeZone=55&lang=1"]
INV_LINK = "https://www.investing.com/economic-calendar/"
IMG_MODELS = ["gemini-2.5-flash-image", "gemini-2.0-flash-preview-image-generation"]

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
    ("usd", "Dollar va Fed", "💵", re.compile(
        r"\b(dxy|usdx|(?:u\.?s\.?\s)?dollar index|fed|federal reserve|fomc)", re.I),
     "#04342c", "#5dcaa5"),
]
UP_WORDS = re.compile(r"\b(rebounds?|rises?|rises|gains?|climbs?|jumps?|surges?|rallies|rally|higher|recovers?|advances?|extends gains)\b", re.I)
DOWN_WORDS = re.compile(r"\b(drops?|falls?|slips?|tumbles?|plunges?|declines?|lower|slumps?|retreats?|dips?|tanks?|weakens?|depreciates?)\b", re.I)
PAIR = re.compile(r"\b(?!XAU/|XAG/)[A-Z]{3}/[A-Z]{3}\b")
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
def _parse_json(text):
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])


def _ai_claude(prompt, max_tokens, key):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    if not r.ok:
        log(f"  AI (Claude) xatosi: HTTP {r.status_code}")
        return None
    return _parse_json("".join(b.get("text", "") for b in r.json().get("content", [])))


def _ai_gemini(prompt, max_tokens, key):
    models = [GEMINI_MODEL] if os.environ.get("GEMINI_MODEL") else GEMINI_MODELS
    for model in models:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "content-type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json",
                                       "maxOutputTokens": max(max_tokens, 2048), "temperature": 0.2}},
            timeout=60)
        if r.status_code == 404:                       # model topilmadi: keyingisini sinaymiz
            log(f"  AI (Gemini): {model} topilmadi, keyingi model sinab ko'riladi")
            continue
        if not r.ok:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = ""
            log(f"  AI (Gemini) xatosi: HTTP {r.status_code} {msg.replace(key, '***')[:200]}")
            return None
        data = r.json()
        cands = data.get("candidates") or []
        if not cands or not cands[0].get("content"):
            log(f"  AI (Gemini): javob bo'sh ({data.get('promptFeedback') or cands})")
            return None
        parts = cands[0]["content"].get("parts", [])
        return _parse_json("".join(p.get("text", "") for p in parts))
    log("  AI (Gemini): hech bir model ishlamadi")
    return None


def ai_json(prompt, max_tokens=1200):
    """Kalit bo'yicha tanlaydi: ANTHROPIC_API_KEY (pullik) yoki GEMINI_API_KEY (bepul reja bor)."""
    if NO_AI:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        log("  AI: kalit topilmadi (GEMINI_API_KEY secret kiritilmagan yoki nomi noto'g'ri)")
        return None
    try:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return _ai_claude(prompt, max_tokens, os.environ["ANTHROPIC_API_KEY"])
        if os.environ.get("GEMINI_API_KEY"):
            return _ai_gemini(prompt, max_tokens, os.environ["GEMINI_API_KEY"])
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
    p = f"""Sen iqtisodiy kalendar tarjimonisan. Quyida {day_label} kuni AQSH (USD) uchun yuqori va o'rta ta'sirli voqealar ro'yxati.
Qoidalar:
- "names": har bir voqea nomini o'zbek tiliga (lotin yozuvi) tarjima qil, mashhur qisqartmani qavsda qoldir (masalan "NFP", "CPI"). Tartib va soni ro'yxatdagidek bo'lsin.
- "notes": har bir voqea uchun qisqa izoh (12 so'zgacha): bu ko'rsatkich nimani o'lchaydi yoki voqea nima. Faqat umumiy ma'no, raqam yoki natijani taxmin qilma. Bilmasang bo'sh qator ("") qoldir. Soni ro'yxatdagidek bo'lsin.
- "summary": 1-2 gap: bugun nimalar kutilayotganini umumiy ayt. Faqat ro'yxatdan foydalan, bozor qaysi tomonga ketishini bashorat qilma, maslahat berma.
Faqat JSON qaytar: {{"names": ["..."], "notes": ["..."], "summary": "..."}}

{lst}"""
    return ai_json(p)


def ai_image(prompt):
    """Gemini bilan rasm yaratadi (GEMINI_API_KEY kerak). Ishlamasa None: shunda kartochka ishlatiladi."""
    key = os.environ.get("GEMINI_API_KEY")
    if NO_AI or not key:
        return None
    import base64
    for model in IMG_MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
                timeout=90)
            if not r.ok:
                log(f"  AI rasm ({model}): HTTP {r.status_code}")
                continue
            for c in r.json().get("candidates") or []:
                for part in (c.get("content") or {}).get("parts", []):
                    inl = part.get("inlineData") or part.get("inline_data")
                    if inl and inl.get("data"):
                        from PIL import Image
                        img = Image.open(io.BytesIO(base64.b64decode(inl["data"]))).convert("RGB")
                        img.thumbnail((1280, 1280))
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=88)
                        return buf.getvalue()
            log(f"  AI rasm ({model}): javobda rasm yo'q")
        except Exception as e:
            log(f"  AI rasm ({model}): {type(e).__name__}")
    return None


def events_image_prompt(rows, names):
    topics = "; ".join(names[:6])
    return ("Create a clean, modern, dark-blue financial illustration for a Telegram post about today's US economic "
            f"calendar. Today's key US events: {topics}. Show abstract symbolic visuals (US dollar, charts, "
            "calendar, data graphs, trading screens). Absolutely no text, letters, numbers or logos in the image. "
            "Landscape 4:3.")


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
        if topic == "usd":
            m = re.search(r"\b(?:dxy|dollar index|usdx)\b[^.\d]{0,80}?(\d{2,3}\.\d{1,3})\b", text, re.I)
            if m and 70 <= float(m.group(1)) <= 130:
                return m.group(1)
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
    tag = {"gold": "OLTIN · XAU/USD", "oil": "NEFT", "usd": "DOLLAR · DXY · FED"}[code]
    f_tag = _font(12 * S, True)
    tw = d.textlength(tag, font=f_tag)
    d.rounded_rectangle([16 * S, 16 * S, 16 * S + tw + 24 * S, 40 * S], radius=12 * S, fill=tag_bg)
    d.text((16 * S + 12 * S, 28 * S), tag, font=f_tag, fill=tag_fg, anchor="lm")
    price = _price(code, orig_title, orig_summary)
    big = price or ("DXY · FED" if code == "usd" else name.upper())
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
    d.text((16 * S, 182 * S), f"{pub_uz:%H:%M} · {pub_uz.day} {MONTH[pub_uz.month]} · Toshkent vaqti",
           font=f_s, fill="#b4b2a9", anchor="ls")
    d.text((324 * S, 182 * S), "FXStreet", font=f_s, fill="#b4b2a9", anchor="rs")
    return _png(img)


def ff_card(day, rows, names):
    from PIL import Image, ImageDraw
    S, W, H = 3, 340, 240
    img = Image.new("RGB", (W * S, H * S), "#14172e")
    d = ImageDraw.Draw(img)
    f_tag = _font(12 * S, True)
    tag = "AQSH · YUQORI VA O'RTA TA'SIR"
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
        d.text((16 * S, y), f"{r['uz']:%H:%M}", font=f_rb,
               fill="#f09595" if r.get("imp") == "high" else "#ef9f27", anchor="ls")
        line = nm
        while d.textlength(line, font=f_r) > 252 * S and " " in line:
            line = line.rsplit(" ", 1)[0]
        d.text((68 * S, y), line, font=f_r, fill="#f1efe8", anchor="ls")
    if len(rows) > 5:
        d.text((68 * S, (128 + 22 * 5) * S), f"+ yana {len(rows) - 5} ta", font=f_r, fill="#b4b2a9", anchor="ls")
    d.text((16 * S, 228 * S), "Toshkent vaqti · Forex Factory · Investing.com", font=_font(11 * S), fill="#b4b2a9", anchor="ls")
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
    pair = PAIR.search(item["title"])          # EUR/USD, USD/JPY kabi valyuta juftliklari kerak emas
    for t in TOPICS:
        if t[0] == "usd" and pair:
            continue
        if t[3].search(item["title"]):
            return t[0]
    for t in TOPICS:
        if t[0] != "usd" and t[3].search(text):     # dollar mavzusi faqat sarlavha bo'yicha
            return t[0]
    return None


def build_news_post(item, topic):
    pub_uz = item["pub"].astimezone(UZ)
    _c, name, emoji, *_ = next(t for t in TOPICS if t[0] == topic)
    ai = ai_news(item["title"], item["summary"])
    head = (f"{emoji} {name} · FXStreet\n"
            f"🕔 Saytga qo'yilgan vaqt: {pub_uz:%H:%M}, {pub_uz.day}-{MONTH[pub_uz.month]} (Toshkent vaqti)\n")
    if ai and ai.get("text"):
        title_uz = ai.get("title") or item["title"]
        body = f"\nTarjima\n{ai['text'].strip()}\n"
        if ai.get("summary"):
            body += f"\nAI xulosa\n{ai['summary'].strip()}\n"
    else:
        title_uz = item["title"]
        body = f"\n{item['title']}\n{item['summary']}\n"
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
def parse_ff(data, imps=("high",)):
    rows = []
    for e in data:
        cur = (e.get("country") or e.get("currency") or "").upper()
        imp = (e.get("impact") or "").strip().lower()
        if cur != "USD" or imp not in imps:
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
                     "uz": dt.astimezone(UZ), "imp": imp, "src": "FF"})
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


def _clean(t):
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#039;", "'").split())


def parse_investing(html):
    """Investing.com kalendar vidjeti HTML idan USD, o'rta va yuqori ta'sirli voqealarni oladi.
    Vaqt hali mintaqasiz ('naive'): load_investing/calibrate uni Toshkent vaqtiga aylantiradi."""
    rows = []
    for m in re.finditer(r"<tr[^>]*eventRowId[^>]*>(.*?)</tr>", html, re.S | re.I):
        head = m.group(0)[:m.group(0).find(">") + 1]
        body = m.group(1)
        dt = re.search(r'data-event-datetime="([^"]+)"', head)
        if not dt or "USD" not in _clean(body).upper():
            continue
        try:
            naive = datetime.strptime(dt.group(1).strip(), "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        bull = re.search(r'data-img_key="bull(\d)"', body)
        n = int(bull.group(1)) if bull else 0
        imp = "high" if n >= 3 else "medium" if n == 2 else None
        if not imp:
            continue
        ev = re.search(r'<td[^>]*class="[^"]*\bevent\b[^"]*"[^>]*>(.*?)</td>', body, re.S | re.I)
        title = _clean(ev.group(1)) if ev else ""
        title = re.sub(r"\s*\((?:MoM|YoY|QoQ)\)\s*$", "", title).strip() or title
        def cell(kind):
            c = re.search(r'<td[^>]*(?:id="event%s_\d+"|class="[^"]*\b%s\b[^"]*")[^>]*>(.*?)</td>' % (kind.capitalize(), kind[:4]),
                          body, re.S | re.I)
            v = _clean(c.group(1)) if c else ""
            return "" if v in ("", "\xa0") else v
        if not title:
            continue
        rows.append({"title": title, "forecast": cell("forecast"), "previous": cell("previous"),
                     "naive": naive, "imp": imp, "src": "INV"})
    return rows


def _words(t):
    return {w for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in ("the", "and", "for")}


def calibrate(inv_rows, ff_rows):
    """Investing vaqt mintaqasi noaniq bo'lgani uchun uni Forex Factory bilan bir xil voqealar orqali aniqlaydi.
    Topilmasa UTC deb oladi."""
    diffs = []
    for r in inv_rows:
        for f in ff_rows:
            if len(_words(r["title"]) & _words(f["title"])) >= 2:
                diffs.append(round((f["uz"].astimezone(timezone.utc).replace(tzinfo=None) - r["naive"]).total_seconds() / 1800) / 2)
    if not diffs:
        log("  Investing vaqti: Forex Factory bilan solishtirib bo'lmadi, UTC deb olindi.")
        return timedelta(0)
    best = max(set(diffs), key=diffs.count)
    log(f"  Investing vaqti: Forex Factory bo'yicha {best:+.1f} soat tuzatildi ({diffs.count(best)}/{len(diffs)} voqea mos).")
    return timedelta(hours=best)


def load_investing(args):
    if args.inv_file:
        return Path(args.inv_file).read_text(encoding="utf-8")
    for u in INV_URLS:
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Referer": "https://www.investing.com/economic-calendar/"},
                             timeout=25)
            if r.ok and "eventRowId" in r.text:
                return r.text
            log(f"  Investing.com: HTTP {r.status_code}, voqealar topilmadi")
        except Exception as e:
            log(f"  Investing.com: {type(e).__name__}")
    return None


def _same_event(a, b):
    return abs((a["uz"] - b["uz"]).total_seconds()) < 600 and len(_words(a["title"]) & _words(b["title"])) >= 1


def run_forexfactory(args):
    """00:00 posti: AQSH (USD) uchun Forex Factory high + Investing.com medium/high voqealari."""
    day = date.fromisoformat(args.date) if args.date else datetime.now(UZ).date()
    data = load_ff(args)
    ff_all = parse_ff(data, ("high", "medium")) if data else []
    ff_high = [r for r in ff_all if r["imp"] == "high"]
    html = load_investing(args)
    inv = parse_investing(html) if html else []
    used_ff_medium = False
    if inv:
        shift = calibrate(inv, ff_high)
        for r in inv:
            r["uz"] = (r["naive"].replace(tzinfo=timezone.utc) + shift).astimezone(UZ)
    elif html is None:
        log("Investing.com ma'lumoti olinmadi: o'rta ta'sirli voqealar vaqtincha Forex Factory dan olinadi.")
        inv = [r for r in ff_all if r["imp"] == "medium"]
        used_ff_medium = True
    if not data and not inv:
        log("Kalendar ma'lumoti olinmadi.")
        return 1
    rows = [r for r in ff_high if r["uz"].date() == day]
    for r in inv:
        if r["uz"].date() == day and not any(_same_event(r, x) for x in rows):
            rows.append(r)
    for r in rows:                                   # bir voqea ikkala manbada bo'lsa, ikkalasi ko'rsatiladi
        r["srcs"] = {r["src"]} | {"FF" if x["src"] == "FF" else "INV" for x in ff_high + inv
                                  if x is not r and r["uz"].date() == day and _same_event(r, x)}
    rows.sort(key=lambda r: r["uz"])
    log(f"Kalendar: {day} uchun {len(rows)} ta USD voqea "
        f"(high: {sum(r['imp'] == 'high' for r in rows)}, medium: {sum(r['imp'] == 'medium' for r in rows)}).")
    if not rows:
        log("Bugun post yo'q.")
        return 0
    ai = ai_events(rows, f"{day.day} {MONTH[day.month]}")
    names = (ai or {}).get("names")
    if not isinstance(names, list) or len(names) != len(rows):
        names = [r["title"] for r in rows]
    notes = (ai or {}).get("notes")
    if not isinstance(notes, list) or len(notes) != len(rows):
        notes = [""] * len(rows)
    L = [f"📅 {WEEKDAY[day.weekday()]}, {day.day} {MONTH[day.month]}: AQSH uchun muhim voqealar",
         "🔴 yuqori ta'sir · 🟠 o'rta ta'sir", "🕔 Vaqtlar Toshkent vaqti bilan", ""]
    for r, nm, nt in zip(rows, names, notes):
        L.append(f"{'🔴' if r['imp'] == 'high' else '🟠'} {r['uz']:%H:%M}  {nm}")
        L.append(f"        Prognoz: {r['forecast'] or '-'} · Oldingi: {r['previous'] or '-'}")
        if nt:
            L.append(f"        ℹ️ {nt}")
    if ai and ai.get("summary"):
        L += ["", "AI xulosa", ai["summary"].strip()]
    srcs = set().union(*(r["srcs"] for r in rows))
    L += [""]
    if "FF" in srcs:
        L.append(f"Forex Factory: {FF_LINK}")
    if "INV" in srcs:
        L.append(f"Investing.com: {INV_LINK}")
    if used_ff_medium:
        L.append("(Investing.com ulanmadi, o'rta ta'sirli voqealar Forex Factory dan)")
    caption = "\n".join(L)
    image = ai_image(events_image_prompt(rows, names))
    if image:
        log("  Rasm: AI yaratdi.")
    else:
        log("  Rasm: AI rasm olinmadi, kartochka ishlatildi.")
        image = ff_card(day, rows, names)
    test = args.dry_run or bool(args.ff_file) or bool(args.inv_file)
    ok = emit("forexfactory", str(day), image, caption, test)
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
                a = argparse.Namespace(**{**vars(args), "date": None, "ff_file": None, "inv_file": None})
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
    ap.add_argument("--inv-file", help="sinash uchun: Investing.com HTML fayl")
    ap.add_argument("--date", help="Forex Factory uchun sana YYYY-MM-DD")
    a = ap.parse_args()
    NO_AI = a.no_ai
    fn = {"fxstreet": run_fxstreet, "forexfactory": run_forexfactory, "loop": run_loop}[a.cmd]
    return fn(a)


if __name__ == "__main__":
    sys.exit(main())
