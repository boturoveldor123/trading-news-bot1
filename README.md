# Trading news bot

- **FXStreet**: oltin, neft, dollar yangiliklari. Har 5 daqiqada tekshiriladi, yangisi chiqsa darhol yuboriladi.
  Rasm: maqolaning o'z rasmi (olinsa), aks holda kartochka. Matn: tarjima + AI xulosa + manba.
- **Forex Factory**: AQSH (USD) high impact voqealari. Har kuni 00:00 (O'zbekiston vaqti) da bitta post.
- Investing.com hozircha yo'q.

## 1. Sinab ko'rish (Telegramga yubormaydi)
    pip install -r requirements.txt
    python news_bot.py fxstreet --dry-run --rss-file fixtures/sample_fxstreet.xml
    python news_bot.py forexfactory --dry-run                # haqiqiy oqimdan bugungi voqealar
Natija `out/` papkada. `Forex Factory: N ta USD high impact voqea` qatorini tekshiring.

## 2. Sozlamalar (yashirin)
- `TELEGRAM_BOT_TOKEN`: bot tokeni
- `TELEGRAM_CHANNEL`: kanal ID si (`-100...`)
- `ANTHROPIC_API_KEY`: tarjima va xulosa uchun (ixtiyoriy; bo'lmasa original inglizcha matn ketadi)

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
