import os
import json
import logging
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "data.json"
GOAL_DATE = date(2026, 5, 27)
START_DATE = date(2026, 4, 21)

# ── States ────────────────────────────────────────────────────────
(SETUP_SW, SETUP_TW, SETUP_SG) = range(3)
(CI_DATE, CI_WEIGHT, CI_STEPS, CI_WORKOUT, CI_RATING) = range(10, 15)
(SL_DATE, SL_START, SL_END, SL_WAKEUPS) = range(20, 24)

# ── Storage ───────────────────────────────────────────────────────
def load(): 
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def user(data, uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"settings": {}, "checkins": {}, "sleep": {}, "xp": 0, "achievements": []}
    return data[uid]

def today(): return date.today().isoformat()

def dstr(d): return d.isoformat() if isinstance(d, date) else d

def days_left(): return max(0, (GOAL_DATE - date.today()).days)

# ── Helpers ───────────────────────────────────────────────────────
def pbar(val, mx, length=8):
    f = int((val / max(mx,1)) * length)
    return "▓"*f + "░"*(length-f)

def streak(checkins):
    d = date.today()
    if not checkins.get(dstr(d), {}).get("done"): d -= timedelta(1)
    n = 0
    for _ in range(200):
        if checkins.get(dstr(d), {}).get("done"): n += 1; d -= timedelta(1)
        else: break
    return n

def sleep_hrs(s, e):
    sh,sm = map(int,s.split(":")); eh,em = map(int,e.split(":"))
    sm2 = sh*60+sm; em2 = eh*60+em
    if em2 <= sm2: em2 += 1440
    return (em2-sm2)/60

def sleep_score(hrs, wakes):
    d = 50 if 7<=hrs<=9 else 40 if hrs>=6.5 else 28 if hrs>=6 else 15 if hrs>=5 else 5
    w = [50,38,26,16,10,5,0][min(wakes,6)]
    t = d+w
    if t>=85: return t,"🌟 Отличный"
    if t>=70: return t,"😴 Хороший"
    if t>=50: return t,"😐 Средний"
    if t>=30: return t,"😮 Плохой"
    return t,"💀 Критично"

def level(xp):
    lvls = [(0,"⚡ Новичок"),(100,"🥊 Боец"),(250,"🏃 Атлет"),(500,"🏆 Чемпион"),(1000,"🤖 Машина")]
    r = lvls[0]
    for mn,nm in lvls:
        if xp>=mn: r=(mn,nm)
    return r

def main_kb():
    return ReplyKeyboardMarkup([
        ["🏠 Главная", "✅ Чек-ин"],
        ["🌙 Сон",     "📅 Календарь"],
        ["📈 Аналитика","🎮 Игра"],
        ["⚙️ Настройки"],
    ], resize_keyboard=True)

def date_select_kb(prefix, offset=0):
    """Keyboard to pick which day (today, yesterday, 2 days ago, custom)"""
    today_d = date.today()
    rows = []
    day_btns = []
    for i in range(5):
        d = today_d - timedelta(days=i)
        label = ["Сегодня","Вчера","2 дня назад","3 дня назад","4 дня назад"][i]
        day_btns.append(InlineKeyboardButton(f"{label} ({d.strftime('%d.%m')})", callback_data=f"{prefix}_day_{dstr(d)}"))
        if len(day_btns) == 1 or i % 2 == 0:
            rows.append([day_btns[-1]])
        else:
            rows[-1].append(day_btns[-1])
    return InlineKeyboardMarkup(rows)

# ── /start & setup ────────────────────────────────────────────────
async def cmd_start(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id); save(data)
    if u["settings"].get("startWeight"):
        await update.message.reply_text("👋 С возвращением!", reply_markup=main_kb())
        return ConversationHandler.END
    await update.message.reply_text(
        "🔥 *FIT до 27 МАЯ*\n\nТвой личный трекер похудения.\n\nВведи текущий вес (кг), например: `82.5`",
        parse_mode="Markdown")
    return SETUP_SW

async def setup_sw(update: Update, ctx):
    try:
        w = float(update.message.text.replace(",","."))
        assert 30 < w < 300
        ctx.user_data["sw"] = w
        await update.message.reply_text(f"✅ Стартовый вес: *{w} кг*\n\nТеперь целевой вес (кг):", parse_mode="Markdown")
        return SETUP_TW
    except: await update.message.reply_text("❌ Введи число, например: 82.5"); return SETUP_SW

async def setup_tw(update: Update, ctx):
    try:
        w = float(update.message.text.replace(",","."))
        assert 30 < w < ctx.user_data["sw"]
        ctx.user_data["tw"] = w
        await update.message.reply_text(f"✅ Цель: *{w} кг*\n\nНорма шагов в день (например: `10000`):", parse_mode="Markdown")
        return SETUP_SG
    except: await update.message.reply_text("❌ Цель должна быть меньше текущего веса"); return SETUP_TW

async def setup_sg(update: Update, ctx):
    try:
        s = int(update.message.text.replace(" ",""))
        assert 500 <= s <= 50000
        data = load(); u = user(data, update.effective_user.id)
        u["settings"] = {"startWeight": ctx.user_data["sw"], "targetWeight": ctx.user_data["tw"], "stepsGoal": s}
        save(data)
        diff = ctx.user_data["sw"] - ctx.user_data["tw"]
        await update.message.reply_text(
            f"🎯 *Настройки сохранены!*\n\n"
            f"⚖️ Старт: *{ctx.user_data['sw']} кг*\n"
            f"🏁 Цель: *{ctx.user_data['tw']} кг*\n"
            f"📉 Нужно сбросить: *{diff:.1f} кг*\n"
            f"👟 Шаги/день: *{s:,}*\n"
            f"📅 Осталось: *{days_left()} дней*\n\n"
            f"Начнём! Жми *✅ Чек-ин* каждый день 💪",
            parse_mode="Markdown", reply_markup=main_kb())
        return ConversationHandler.END
    except: await update.message.reply_text("❌ Введи число, например: 10000"); return SETUP_SG

# ── Home ──────────────────────────────────────────────────────────
async def show_home(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    s = u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала настрой цели — /start"); return

    checkins = u["checkins"]
    ws = sorted([(k,v["weight"]) for k,v in checkins.items() if v.get("weight")], reverse=True)
    cur_w = ws[0][1] if ws else s["startWeight"]
    lost = max(0, s["startWeight"] - cur_w)
    to_goal = max(0, cur_w - s["targetWeight"])
    dl = days_left()
    total = (GOAL_DATE - START_DATE).days
    elapsed = (date.today() - START_DATE).days
    pct = min(100, max(0, int(elapsed/total*100)))
    strk = streak(checkins)
    xp = u.get("xp",0); _,lvl = level(xp)
    td_entry = checkins.get(today(),{})
    status = "✅ Отмечен" if td_entry.get("done") else "⚠️ Не отмечен — жми ✅ Чек-ин!"

    # forecast
    fc = ""
    if len(ws) >= 3:
        rate = (ws[-1][1] - ws[0][1]) / max(len(ws)-1,1)
        if rate < 0:
            dn = int(to_goal/abs(rate))
            fc = f"\n🔮 Цель через *{dn} дн.* {'✓ до дедлайна!' if dn<=dl else '— нужно ускориться!'}"

    warn = ""
    if all(not checkins.get(dstr(date.today()-timedelta(i)),{}).get("done") for i in range(1,3)):
        warn = "\n\n💀 *2+ дня пропуска — срочно отметься!*"

    await update.message.reply_text(
        f"🏠 *{date.today().strftime('%d.%m.%Y')}*\n\n"
        f"⏳ До 27 мая: *{dl} дней*\n"
        f"{pbar(elapsed,total,12)} {pct}%\n\n"
        f"⚖️ *{cur_w:.1f} кг* · -{lost:.1f} кг · до цели {to_goal:.1f} кг\n\n"
        f"🔥 Стрик: *{strk} дней*\n"
        f"Сегодня: {status}\n\n"
        f"{lvl} · {xp} XP{fc}{warn}",
        parse_mode="Markdown", reply_markup=main_kb())

# ── Check-in ──────────────────────────────────────────────────────
async def start_ci(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    if not u["settings"].get("startWeight"):
        await update.message.reply_text("Сначала настрой цели — /start"); return ConversationHandler.END
    await update.message.reply_text(
        "✅ *Чек-ин*\n\nЗа какой день?",
        parse_mode="Markdown",
        reply_markup=date_select_kb("ci"))
    return CI_DATE

async def ci_date(update: Update, ctx):
    q = update.callback_query; await q.answer()
    ds = q.data.replace("ci_day_","")
    ctx.user_data["ci_date"] = ds
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    label = "сегодня" if d == date.today() else d.strftime("%d.%m.%Y")

    data = load(); u = user(data, update.effective_user.id)
    existing = u["checkins"].get(ds,{})
    prev_w = existing.get("weight","")
    hint = f"\nПрошлое значение: *{prev_w} кг*" if prev_w else ""

    # Weight buttons
    sw = u["settings"].get("startWeight", 80)
    base = existing.get("weight", sw)
    weights = [round(base - 0.5 + i*0.1, 1) for i in range(11)]
    rows = []
    row = []
    for w in weights:
        row.append(InlineKeyboardButton(f"{w}", callback_data=f"ciw_{w}"))
        if len(row) == 4: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data="ciw_manual")])

    await q.edit_message_text(
        f"✅ Чек-ин за *{label}*{hint}\n\n⚖️ Выбери вес (кг):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return CI_WEIGHT

async def ci_weight_btn(update: Update, ctx):
    q = update.callback_query; await q.answer()
    if q.data == "ciw_manual":
        await q.edit_message_text("⚖️ Введи вес вручную (кг), например: `76.3`", parse_mode="Markdown")
        return CI_WEIGHT
    w = float(q.data.replace("ciw_",""))
    ctx.user_data["ci_weight"] = w
    await q.edit_message_text(
        f"⚖️ Вес: *{w} кг* ✓\n\n👟 Сколько шагов?",
        parse_mode="Markdown",
        reply_markup=steps_kb())
    return CI_STEPS

async def ci_weight_text(update: Update, ctx):
    try:
        w = float(update.message.text.replace(",","."))
        assert 30 < w < 300
        ctx.user_data["ci_weight"] = w
        await update.message.reply_text(
            f"⚖️ Вес: *{w} кг* ✓\n\n👟 Сколько шагов?",
            parse_mode="Markdown", reply_markup=steps_kb())
        return CI_STEPS
    except:
        await update.message.reply_text("❌ Введи число, например: 76.3"); return CI_WEIGHT

def steps_kb():
    presets = [0, 2000, 4000, 6000, 8000, 10000, 12000, 15000, 20000]
    rows = []
    row = []
    for s in presets:
        label = f"{s//1000}к" if s >= 1000 else "0"
        row.append(InlineKeyboardButton(label, callback_data=f"cis_{s}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести точно", callback_data="cis_manual")])
    return InlineKeyboardMarkup(rows)

async def ci_steps_btn(update: Update, ctx):
    q = update.callback_query; await q.answer()
    if q.data == "cis_manual":
        await q.edit_message_text("👟 Введи количество шагов:", parse_mode="Markdown")
        return CI_STEPS
    s = int(q.data.replace("cis_",""))
    ctx.user_data["ci_steps"] = s
    data = load(); u = user(data, update.effective_user.id)
    goal = u["settings"].get("stepsGoal",10000)
    pb = pbar(s, goal, 8)
    tag = "✅ Норма!" if s>=goal else f"{int(s/goal*100)}%"
    await q.edit_message_text(
        f"👟 Шаги: *{s:,}* {pb} {tag}\n\n💪 Была тренировка?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💪 Да", callback_data="ciwo_yes"),
            InlineKeyboardButton("😴 Нет", callback_data="ciwo_no")
        ]]))
    return CI_WORKOUT

async def ci_steps_text(update: Update, ctx):
    try:
        s = int(update.message.text.replace(" ","").replace(",",""))
        assert 0 <= s <= 100000
        ctx.user_data["ci_steps"] = s
        data = load(); u = user(data, update.effective_user.id)
        goal = u["settings"].get("stepsGoal",10000)
        pb = pbar(s, goal, 8)
        tag = "✅ Норма!" if s>=goal else f"{int(s/goal*100)}%"
        await update.message.reply_text(
            f"👟 Шаги: *{s:,}* {pb} {tag}\n\n💪 Была тренировка?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💪 Да", callback_data="ciwo_yes"),
                InlineKeyboardButton("😴 Нет", callback_data="ciwo_no")
            ]]))
        return CI_WORKOUT
    except:
        await update.message.reply_text("❌ Введи число шагов"); return CI_STEPS

async def ci_workout(update: Update, ctx):
    q = update.callback_query; await q.answer()
    ctx.user_data["ci_workout"] = q.data == "ciwo_yes"
    wo = "💪 Да" if ctx.user_data["ci_workout"] else "😴 Нет"
    # Rating 1-10 as buttons
    rows = [
        [InlineKeyboardButton(str(i), callback_data=f"cir_{i}") for i in range(1,6)],
        [InlineKeyboardButton(str(i), callback_data=f"cir_{i}") for i in range(6,11)],
    ]
    await q.edit_message_text(
        f"💪 Тренировка: *{wo}*\n\n⭐ Оцени день (1–10):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return CI_RATING

async def ci_rating(update: Update, ctx):
    q = update.callback_query; await q.answer()
    r = int(q.data.replace("cir_",""))
    ctx.user_data["ci_rating"] = r

    data = load(); u = user(data, update.effective_user.id)
    ds = ctx.user_data["ci_date"]
    s = u["settings"]
    w = ctx.user_data["ci_weight"]
    steps = ctx.user_data["ci_steps"]
    wo = ctx.user_data["ci_workout"]

    u["checkins"][ds] = {"done":True,"weight":w,"steps":steps,"workout":wo,"rating":r,"ts":datetime.now().isoformat()}

    xp_earn = 20
    if wo: xp_earn += 15
    if steps >= s.get("stepsGoal",10000): xp_earn += 10
    if r >= 8: xp_earn += 5
    strk = streak(u["checkins"])
    if strk >= 7: xp_earn += 50
    u["xp"] = u.get("xp",0) + xp_earn
    check_achievements(u)
    save(data)

    d = datetime.strptime(ds, "%Y-%m-%d").date()
    label = "сегодня" if d==date.today() else d.strftime("%d.%m")
    goal_ok = "✅" if steps >= s.get("stepsGoal",10000) else "❌"

    await q.edit_message_text(
        f"🎉 *Чек-ин за {label} сохранён! +{xp_earn} XP*\n\n"
        f"⚖️ Вес: *{w} кг*\n"
        f"👟 Шаги: *{steps:,}* {goal_ok}\n"
        f"💪 Тренировка: {'Да' if wo else 'Нет'}\n"
        f"⭐ Оценка: *{r}/10*\n\n"
        f"🔥 Стрик: *{strk} дней*",
        parse_mode="Markdown")
    return ConversationHandler.END

# ── Sleep ─────────────────────────────────────────────────────────
async def start_sleep(update: Update, ctx):
    await update.message.reply_text(
        "🌙 *Трекер сна*\n\nЗа какой день?",
        parse_mode="Markdown",
        reply_markup=date_select_kb("sl"))
    return SL_DATE

async def sl_date(update: Update, ctx):
    q = update.callback_query; await q.answer()
    ds = q.data.replace("sl_day_","")
    ctx.user_data["sl_date"] = ds
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    label = "сегодня" if d==date.today() else d.strftime("%d.%m.%Y")

    # Show preset bedtimes
    presets = ["21:00","22:00","22:30","23:00","23:30","00:00","00:30","01:00","02:00"]
    rows = []
    row = []
    for t in presets:
        row.append(InlineKeyboardButton(t, callback_data=f"sls_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое время", callback_data="sls_manual")])

    await q.edit_message_text(
        f"🌙 Сон за *{label}*\n\n😴 Во сколько лёг спать?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return SL_START

async def sl_start_btn(update: Update, ctx):
    q = update.callback_query; await q.answer()
    if q.data == "sls_manual":
        await q.edit_message_text("😴 Введи время сна (ЧЧ:ММ), например: `23:45`", parse_mode="Markdown")
        return SL_START
    t = q.data.replace("sls_","")
    ctx.user_data["sl_start"] = t
    await q.edit_message_text(
        f"😴 Лёг в *{t}*\n\n⏰ Во сколько проснулся?",
        parse_mode="Markdown",
        reply_markup=wake_kb())
    return SL_END

async def sl_start_text(update: Update, ctx):
    try:
        t = update.message.text.strip()
        datetime.strptime(t, "%H:%M")
        ctx.user_data["sl_start"] = t
        await update.message.reply_text(
            f"😴 Лёг в *{t}*\n\n⏰ Во сколько проснулся?",
            parse_mode="Markdown", reply_markup=wake_kb())
        return SL_END
    except:
        await update.message.reply_text("❌ Формат: ЧЧ:ММ, например 23:30"); return SL_START

def wake_kb():
    presets = ["05:00","05:30","06:00","06:30","07:00","07:30","08:00","08:30","09:00","10:00"]
    rows = []
    row = []
    for t in presets:
        row.append(InlineKeyboardButton(t, callback_data=f"sle_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое время", callback_data="sle_manual")])
    return InlineKeyboardMarkup(rows)

async def sl_end_btn(update: Update, ctx):
    q = update.callback_query; await q.answer()
    if q.data == "sle_manual":
        await q.edit_message_text("⏰ Введи время пробуждения (ЧЧ:ММ), например: `07:30`", parse_mode="Markdown")
        return SL_END
    t = q.data.replace("sle_","")
    return await _process_sl_end(q, ctx, t, is_query=True)

async def sl_end_text(update: Update, ctx):
    try:
        t = update.message.text.strip()
        datetime.strptime(t, "%H:%M")
        return await _process_sl_end(update, ctx, t, is_query=False)
    except:
        await update.message.reply_text("❌ Формат: ЧЧ:ММ, например 07:30"); return SL_END

async def _process_sl_end(obj, ctx, t, is_query):
    start = ctx.user_data["sl_start"]
    hrs = sleep_hrs(start, t)
    if hrs < 1:
        msg = "❌ Слишком мало. Проверь время."
        if is_query: await obj.edit_message_text(msg)
        else: await obj.message.reply_text(msg)
        return SL_END
    if hrs > 18:
        msg = "❌ Больше 18 часов? Проверь время."
        if is_query: await obj.edit_message_text(msg)
        else: await obj.message.reply_text(msg)
        return SL_END

    ctx.user_data["sl_end"] = t
    hh = int(hrs); mm = int((hrs%1)*60)
    color = "🟢" if 7<=hrs<=9 else "🟡" if hrs>=6 else "🔴"
    pb = pbar(min(hrs,10), 10, 8)

    rows = [
        [InlineKeyboardButton("0 — без пробуждений", callback_data="slw_0")],
        [InlineKeyboardButton("1 раз", callback_data="slw_1"),
         InlineKeyboardButton("2 раза", callback_data="slw_2"),
         InlineKeyboardButton("3 раза", callback_data="slw_3")],
        [InlineKeyboardButton("4 раза", callback_data="slw_4"),
         InlineKeyboardButton("5+ раз", callback_data="slw_5")],
    ]
    text = (f"⏰ Встал в *{t}*\n"
            f"🛏 Сон: *{hh}ч {mm}мин* {color}\n"
            f"{pb}\n\n🌃 Сколько раз просыпался?")
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else: await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    return SL_WAKEUPS

async def sl_wakeups(update: Update, ctx):
    q = update.callback_query; await q.answer()
    w = int(q.data.replace("slw_",""))
    start = ctx.user_data["sl_start"]
    end = ctx.user_data["sl_end"]
    ds = ctx.user_data["sl_date"]
    hrs = sleep_hrs(start, end)
    sc, label = sleep_score(hrs, w)
    hh = int(hrs); mm = int((hrs%1)*60)

    data = load(); u = user(data, update.effective_user.id)
    u["sleep"][ds] = {"start":start,"end":end,"wakeups":w,"score":sc,"saved":True,"ts":datetime.now().isoformat()}

    xp_earn = 0
    if 7<=hrs<=9: xp_earn += 10
    if w==0: xp_earn += 5
    if xp_earn: u["xp"] = u.get("xp",0) + xp_earn
    save(data)

    d = datetime.strptime(ds, "%Y-%m-%d").date()
    dlabel = "сегодня" if d==date.today() else d.strftime("%d.%m")
    wake_txt = ["Без пробуждений 🌟","1 раз","2 раза","3 раза","4 раза","5+ раз"][min(w,5)]
    xp_txt = f"\n+{xp_earn} XP 🎯" if xp_earn else ""

    advice = {
        True: "💪 Отличный сон — тело восстановилось!",
    }.get(sc>=85, "⚠️ Недосып мешает похудению!" if sc<50 else "👍 Неплохо, но можно лучше")

    await q.edit_message_text(
        f"🌙 *Сон за {dlabel} сохранён!*{xp_txt}\n\n"
        f"😴 {start} → ⏰ {end}\n"
        f"⏱ *{hh}ч {mm}мин* · {wake_txt}\n"
        f"📊 Оценка: *{sc}/100 — {label}*\n\n"
        f"{advice}",
        parse_mode="Markdown")
    return ConversationHandler.END

# ── Calendar ──────────────────────────────────────────────────────
async def show_calendar(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    checkins = u["checkins"]; sleep_d = u["sleep"]
    today_d = date.today()

    lines = ["📅 *Календарь апрель–май 2026*\n"]
    lines.append("`Пн  Вт  Ср  Чт  Пт  Сб  Вс`")

    for year, month, mname in [(2026,4,"🌱 Апрель"),(2026,5,"☀️ Май")]:
        lines.append(f"\n*{mname}*")
        first = date(year, month, 1)
        dow = first.weekday()
        last_day = (date(year, month%12+1, 1) - timedelta(1)).day if month<12 else 31

        row = "  " * dow
        col = dow
        for d in range(1, last_day+1):
            cur = date(year, month, d)
            ds = dstr(cur)
            is_goal = cur == GOAL_DATE
            is_today = cur == today_d
            is_future = cur > today_d
            is_before_start = cur < START_DATE

            if is_goal: cell = "🎯"
            elif is_before_start: cell = "  "
            elif is_future: cell = "⬜"
            else:
                done = checkins.get(ds,{}).get("done")
                has_sleep = sleep_d.get(ds,{}).get("saved")
                if done and has_sleep: cell = "💚"
                elif done: cell = "✅"
                else: cell = "❌"

            if is_today: cell = f"[{cell}]" if len(cell.strip())>0 else "[  ]"

            row += cell + " "
            col += 1
            if col % 7 == 0:
                lines.append("`" + row.rstrip() + "`")
                row = ""
        if row.strip():
            lines.append("`" + row.rstrip() + "`")

    # Stats
    done_days = sum(1 for v in checkins.values() if v.get("done"))
    sleep_days = sum(1 for v in sleep_d.values() if v.get("saved"))
    strk = streak(checkins)
    elapsed = (today_d - START_DATE).days + 1
    disc = int(done_days/max(elapsed,1)*100)

    lines.append(f"\n✅ чек-ин  💚 +сон  ❌ пропуск  🎯 27 мая")
    lines.append(f"\n📊 Чек-инов: *{done_days}* · Сон: *{sleep_days}* · Стрик: *{strk}* 🔥")
    lines.append(f"Дисциплина: *{disc}%* | До цели: *{days_left()} дней*")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── Analytics ─────────────────────────────────────────────────────
async def show_analytics(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    checkins = u["checkins"]; sl = u["sleep"]; s = u["settings"]
    today_d = date.today()

    # Weight
    ws = sorted([(k,v["weight"]) for k,v in checkins.items() if v.get("weight")])
    if ws:
        w_first = ws[0][1]; w_last = ws[-1][1]
        diff = w_last - w_first
        trend = "↓" if diff<0 else "↑" if diff>0 else "→"
        w_txt = f"*{w_last:.1f} кг* ({diff:+.1f} {trend})"
    else: w_txt = "нет данных"

    # Steps last 14 days
    last14 = [(today_d-timedelta(i)).isoformat() for i in range(13,-1,-1)]
    steps14 = [checkins.get(d,{}).get("steps",0) for d in last14]
    valid_steps = [s2 for s2 in steps14 if s2>0]
    avg_s = int(sum(valid_steps)/len(valid_steps)) if valid_steps else 0
    goal_s = s.get("stepsGoal",10000)
    goal_days = sum(1 for s2 in valid_steps if s2>=goal_s)

    # Chart
    chart = ""
    for s2 in steps14[-7:]:
        p = min(1.0, s2/max(goal_s,1))
        chart += "█" if p>=1 else "▇" if p>=0.8 else "▅" if p>=0.5 else "▂" if p>0 else "░"

    # Discipline
    elapsed = (today_d-START_DATE).days+1
    done = sum(1 for v in checkins.values() if v.get("done"))
    disc = int(done/max(elapsed,1)*100)
    strk = streak(checkins)

    # Sleep
    sl_entries = [v for k,v in sl.items() if v.get("saved")]
    if sl_entries:
        hrs_list = [sleep_hrs(e["start"],e["end"]) for e in sl_entries]
        avg_sl = sum(hrs_list)/len(hrs_list)
        avg_sc = int(sum(e.get("score",0) for e in sl_entries)/len(sl_entries))
        sl_txt = f"*{avg_sl:.1f}ч* · оценка *{avg_sc}/100*"
    else: sl_txt = "нет данных"

    # Week comparison
    tw = [checkins.get(dstr(today_d-timedelta(i)),{}) for i in range(7)]
    lw = [checkins.get(dstr(today_d-timedelta(i+7)),{}) for i in range(7)]
    tw_s = [e.get("steps",0) for e in tw if e.get("steps")]
    lw_s = [e.get("steps",0) for e in lw if e.get("steps")]
    tw_avg = int(sum(tw_s)/len(tw_s)) if tw_s else 0
    lw_avg = int(sum(lw_s)/len(lw_s)) if lw_s else 0
    sdiff = tw_avg - lw_avg

    lost = max(0, s.get("startWeight",0) - (ws[-1][1] if ws else s.get("startWeight",0)))

    await update.message.reply_text(
        f"📈 *Аналитика*\n\n"
        f"⚖️ Вес: {w_txt}\n"
        f"📉 Сброшено: *{lost:.1f} кг*\n\n"
        f"👟 Шаги (14 дн.)\n"
        f"Среднее: *{avg_s:,}* · Норма: *{goal_days}* дней\n"
        f"`{chart}` (последние 7 дн.)\n\n"
        f"📊 Дисциплина: *{disc}%* · Стрик: *{strk}* 🔥\n"
        f"Отмечено: *{done}* из {elapsed} дней\n\n"
        f"🌙 Сон: {sl_txt}\n\n"
        f"📅 Эта неделя vs прошлая\n"
        f"Шаги: *{tw_avg:,}* vs *{lw_avg:,}* ({sdiff:+,})\n"
        f"Чек-ины: *{sum(1 for e in tw if e.get('done'))}* vs *{sum(1 for e in lw if e.get('done'))}*",
        parse_mode="Markdown", reply_markup=main_kb())

# ── Game ──────────────────────────────────────────────────────────
async def show_game(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    xp = u.get("xp",0); checkins = u["checkins"]
    unlocked = u.get("achievements",[])

    lvls = [(0,"⚡ Новичок",100),(100,"🥊 Боец",250),(250,"🏃 Атлет",500),(500,"🏆 Чемпион",1000),(1000,"🤖 Машина",9999)]
    cur = lvls[0]; nxt = lvls[1]
    for i,(mn,nm,_) in enumerate(lvls):
        if xp>=mn: cur=lvls[i]; nxt=lvls[i+1] if i+1<len(lvls) else None

    pb = pbar(xp-cur[0], (nxt[0] if nxt else xp)-cur[0], 10)
    nxt_txt = f"До {nxt[1]}: {nxt[0]-xp} XP" if nxt else "🏆 MAX!"

    strk = streak(checkins)
    entries = [v for v in checkins.values() if v.get("done")]
    wo_count = sum(1 for e in entries if e.get("workout"))

    step_strk = 0
    for i in range(30):
        d = dstr(date.today()-timedelta(i))
        if checkins.get(d,{}).get("steps",0)>=10000: step_strk+=1
        else: break

    all_ach = [
        ("first_checkin","🎯","Первый шаг","1 чек-ин"),
        ("streak7","🔥","7 дней","7 дней подряд"),
        ("streak14","💪","14 дней","14 дней подряд"),
        ("minus1","⚖️","-1 кг","сбросил 1 кг"),
        ("minus3","🎉","-3 кг","сбросил 3 кг"),
        ("steps10k","👟","10к шагов","за 1 день"),
        ("steps5days","🦵","5×10к","5 дней подряд"),
        ("workout7","🏋️","7 трен.","7 тренировок"),
        ("good_sleep","🌙","Хороший сон","оценка 85+"),
    ]
    ach_txt = ""
    for aid,icon,nm,desc in all_ach:
        mark = "✅" if aid in unlocked else "🔒"
        ach_txt += f"{mark} {icon} {nm} — {desc}\n"

    await update.message.reply_text(
        f"🎮 *Игра*\n\n"
        f"*{cur[1]}*\n"
        f"{pb} *{xp} XP*\n"
        f"{nxt_txt}\n\n"
        f"*Достижения:*\n{ach_txt}\n"
        f"*Челленджи:*\n"
        f"{'✅' if strk>=7 else '🔄'} 7 дней подряд: {min(strk,7)}/7\n"
        f"{'✅' if step_strk>=5 else '🔄'} 10к × 5 дней: {min(step_strk,5)}/5\n"
        f"{'✅' if wo_count>=7 else '🔄'} 7 тренировок: {min(wo_count,7)}/7\n\n"
        f"*XP:* +20 чек-ин · +15 трен · +10 шаги\n+5 оценка 8+ · +50 стрик 7д · +10 сон",
        parse_mode="Markdown", reply_markup=main_kb())

# ── Settings ──────────────────────────────────────────────────────
async def show_settings(update: Update, ctx):
    data = load(); u = user(data, update.effective_user.id)
    s = u["settings"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Изменить цели", callback_data="reset_setup")],
        [InlineKeyboardButton("📤 Экспорт данных (JSON)", callback_data="export_data")],
    ])
    done = sum(1 for v in u["checkins"].values() if v.get("done"))
    sl_cnt = sum(1 for v in u["sleep"].values() if v.get("saved"))
    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"⚖️ Стартовый вес: *{s.get('startWeight','—')} кг*\n"
        f"🎯 Целевой вес: *{s.get('targetWeight','—')} кг*\n"
        f"👟 Норма шагов: *{s.get('stepsGoal',10000):,}*\n\n"
        f"💾 Чек-инов: *{done}* · Сон: *{sl_cnt}* · XP: *{u.get('xp',0)}*\n\n"
        f"🔒 Данные хранятся на сервере Railway",
        parse_mode="Markdown", reply_markup=kb)

async def settings_cb(update: Update, ctx):
    q = update.callback_query; await q.answer()
    if q.data == "export_data":
        data = load(); u = user(data, update.effective_user.id)
        js = json.dumps(u, ensure_ascii=False, indent=2)
        await q.message.reply_document(
            document=js.encode("utf-8"),
            filename=f"fit27_{today()}.json",
            caption="📤 Резервная копия данных")
    elif q.data == "reset_setup":
        await q.edit_message_text("Введи новый стартовый вес (кг):")
        return SETUP_SW

# ── Achievements check ────────────────────────────────────────────
def check_achievements(u):
    ul = u.get("achievements",[])
    def unlock(a):
        if a not in ul: ul.append(a)
    checkins = u["checkins"]; s = u["settings"]
    entries = [v for v in checkins.values() if v.get("done")]
    if entries: unlock("first_checkin")
    strk = streak(checkins)
    if strk>=7: unlock("streak7")
    if strk>=14: unlock("streak14")
    ws = sorted([(k,v["weight"]) for k,v in checkins.items() if v.get("weight")])
    if ws and s.get("startWeight"):
        lost = s["startWeight"] - ws[-1][1]
        if lost>=1: unlock("minus1")
        if lost>=3: unlock("minus3")
    if any(e.get("steps",0)>=10000 for e in entries): unlock("steps10k")
    cs = 0
    for i in range(30):
        d = dstr(date.today()-timedelta(i))
        if checkins.get(d,{}).get("steps",0)>=10000: cs+=1
        else: break
    if cs>=5: unlock("steps5days")
    if sum(1 for e in entries if e.get("workout"))>=7: unlock("workout7")
    sl = u.get("sleep",{})
    if any(v.get("score",0)>=85 for v in sl.values() if v.get("saved")): unlock("good_sleep")
    u["achievements"] = ul

# ── Text router ───────────────────────────────────────────────────
async def handle_text(update: Update, ctx):
    t = update.message.text
    if any(x in t for x in ["Главная","🏠"]): await show_home(update, ctx)
    elif any(x in t for x in ["Календарь","📅"]): await show_calendar(update, ctx)
    elif any(x in t for x in ["Аналитика","📈"]): await show_analytics(update, ctx)
    elif any(x in t for x in ["Игра","🎮"]): await show_game(update, ctx)
    elif any(x in t for x in ["Настройки","⚙️"]): await show_settings(update, ctx)
    else: await update.message.reply_text("Выбери раздел 👇", reply_markup=main_kb())

# ── Main ──────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            SETUP_SW: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_sw)],
            SETUP_TW: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_tw)],
            SETUP_SG: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_sg)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    )

    ci_conv = ConversationHandler(
        entry_points=[
            CommandHandler("checkin", start_ci),
            MessageHandler(filters.Regex("Чек-ин|✅"), start_ci),
        ],
        states={
            CI_DATE:    [CallbackQueryHandler(ci_date, pattern="^ci_day_")],
            CI_WEIGHT:  [CallbackQueryHandler(ci_weight_btn, pattern="^ciw_"),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, ci_weight_text)],
            CI_STEPS:   [CallbackQueryHandler(ci_steps_btn, pattern="^cis_"),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, ci_steps_text)],
            CI_WORKOUT: [CallbackQueryHandler(ci_workout, pattern="^ciwo_")],
            CI_RATING:  [CallbackQueryHandler(ci_rating, pattern="^cir_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    sl_conv = ConversationHandler(
        entry_points=[
            CommandHandler("sleep", start_sleep),
            MessageHandler(filters.Regex("Сон|🌙"), start_sleep),
        ],
        states={
            SL_DATE:    [CallbackQueryHandler(sl_date, pattern="^sl_day_")],
            SL_START:   [CallbackQueryHandler(sl_start_btn, pattern="^sls_"),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, sl_start_text)],
            SL_END:     [CallbackQueryHandler(sl_end_btn, pattern="^sle_"),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, sl_end_text)],
            SL_WAKEUPS: [CallbackQueryHandler(sl_wakeups, pattern="^slw_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    app.add_handler(setup_conv)
    app.add_handler(ci_conv)
    app.add_handler(sl_conv)
    app.add_handler(CallbackQueryHandler(settings_cb, pattern="^(reset_setup|export_data)$"))
    app.add_handler(CommandHandler("home", show_home))
    app.add_handler(CommandHandler("calendar", show_calendar))
    app.add_handler(CommandHandler("analytics", show_analytics))
    app.add_handler(CommandHandler("game", show_game))
    app.add_handler(CommandHandler("settings", show_settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
