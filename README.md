# Trading news bot

- **FXStreet**: oltin, neft va dollar indeksi (DXY) yangiliklari. Har 5 daqiqada tekshiriladi, yangisi chiqsa darhol yuboriladi.
  Rasm: maqolaning o'z rasmi (olinsa), aks holda kartochka. Matn: tarjima + AI xulosa + manba.
- **00:00 kalendar posti** (O'zbekiston vaqti, bitta post): AQSH (USD) uchun Forex Factory high impact + Investing.com o'rta va yuqori ta'sirli voqealar. 🔴 yuqori, 🟠 o'rta. Rasm: AI yaratadi (GEMINI_API_KEY bilan), bo'lmasa kartochka.
  Investing.com ulanmasa, o'rta ta'sirli voqealar vaqtincha Forex Factory dan olinadi va postda shu yoziladi.

## 1. Sinab ko'rish (Telegramga yubormaydi)
    pip install -r requirements.txt
    python news_bot.py fxstreet --dry-run --rss-file fixtures/sample_fxstreet.xml
    python news_bot.py forexfactory --dry-run                # haqiqiy oqimdan bugungi voqealar
    python news_bot.py forexfactory --dry-run --ff-file fixtures/sample_ff_SYNTHETIC.json --inv-file fixtures/sample_investing_SYNTHETIC.html --date 2026-11-04
Natija `out/` papkada. `Kalendar: N ta USD voqea` va `Investing vaqti: ...` qatorlarini tekshiring: vaqt Forex Factory bilan solishtirilib avtomatik to'g'rilanadi.

## 2. Sozlamalar (yashirin)
- `TELEGRAM_BOT_TOKEN`: bot tokeni
- `TELEGRAM_CHANNEL`: kanal ID si (`-100...`)
- `GEMINI_API_KEY`: BEPUL tarjima va xulosa uchun (aistudio.google.com, API key). Ixtiyoriy.
- `ANTHROPIC_API_KEY`: pullik muqobil (Claude). Ikkalasi bo'lsa Claude ishlatiladi.
- Hech biri bo'lmasa original inglizcha matn ketadi.

## 3A. GitHub Actions orqali (kompyutersiz)
1. Repository oching, hamma fayllarni yuklang (`.github` papkasi ham).
2. Settings, Secrets and variables, Actions: yuqoridagi uchta sozlamani qo'shing.
3. Repository **public** bo'lsa, daqiqalar bepul va cheksiz. Private bo'lsa, har 5 daqiqalik ishga tushirish bepul limitdan (oyiga 2000 daqiqa) oshib ketadi.
4. Actions bo'limida "FXStreet yangiliklari" va "Forex Factory (kunlik)" ni "Run workflow" bilan sinang.
Eslatma: GitHub `*/5` jadvalni kafolatlamaydi, band paytlarda 10-15 daqiqaga kechikishi mumkin.

## 3B. Doim yoniq kompyuter/server (aniq 5 daqiqa)
    python news_bot.py loop
Sozlamalarni muhit o'zgaruvchisi qilib bering. Har 5 daqiqada FXStreet, 00:00 da Forex Factory.

Birinchi ishga tushirishda bot eski yangiliklarni yubormaydi, faqat eslab qoladi (`state.json`).
