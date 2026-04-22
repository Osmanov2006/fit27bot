import os
import json
import logging
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8624040894:AAFfYCX2prfjgfq2GdZwL0luA-23IQ0SiWs")
DATA_FILE = "data.json"

# ─── Conversation states ───────────────────────────────────────────
SETUP_START_WEIGHT, SETUP_TARGET_WEIGHT, SETUP_STEPS_GOAL = range(3)
CI_WEIGHT, CI_STEPS, CI_WORKOUT, CI_RATING = range(10, 14)
SLEEP_START, SLEEP_END, SLEEP_WAKEUPS = range(20, 23)

GOAL_DATE = date(2026, 5, 27)

# ─── DATA LAYER ───────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"settings": {}, "checkins": {}, "sleep": {}, "xp": 0, "achievements": []}
    return data[uid]

def today_str():
    return date.today().isoformat()

# ─── HELPERS ──────────────────────────────────────────────────────
def days_left():
    delta = GOAL_DATE - date.today()
    return max(0, delta.days)

def calc_streak(checkins):
    streak = 0
    d = date.today()
    if not checkins.get(d.isoformat(), {}).get("done"):
        d -= timedelta(days=1)
    for _ in range(200):
        if checkins.get(d.isoformat(), {}).get("done"):
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak

def calc_sleep_score(hours, wakeups):
    if hours >= 7 and hours <= 9:
        dur = 50
    elif hours >= 6.5:
        dur = 40
    elif hours >= 6:
        dur = 28
    elif hours >= 5:
        dur = 15
    else:
        dur = 5

    wake_scores = [50, 38, 26, 16, 10, 5, 0]
    wake = wake_scores[min(wakeups, 6)]
    total = dur + wake

    if total >= 85: return total, "🌟 Отличный"
    if total >= 70: return total, "😴 Хороший"
    if total >= 50: return total, "😐 Средний"
    if total >= 30: return total, "😮 Плохой"
    return total, "💀 Критично"

def get_level(xp):
    levels = [
        (0,    "⚡ Новичок"),
        (100,  "🥊 Боец"),
        (250,  "🏃 Атлет"),
        (500,  "🏆 Чемпион"),
        (1000, "🤖 Машина"),
    ]
    level = levels[0]
    for min_xp, name in levels:
        if xp >= min_xp:
            level = (min_xp, name)
    return level

def progress_bar(value, total, length=10):
    filled = int((value / max(total, 1)) * length)
    return "█" * filled + "░" * (length - filled)

def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📊 Главная",    "✅ Чек-ин"],
        ["🌙 Сон",        "📅 Календарь"],
        ["📈 Аналитика",  "🎮 Игра"],
        ["⚙️ Настройки"],
    ], resize_keyboard=True)

# ─── /start ───────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    save_data(data)

    if user["settings"].get("startWeight"):
        await update.message.reply_text(
            "👋 С возвращением! Выбери раздел:",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "🔥 *Привет! Я твой тренер до 27 мая.*\n\n"
            "Буду следить за весом, шагами, сном и мотивировать каждый день.\n\n"
            "Давай настроим цели. Введи свой *текущий вес* (кг):",
            parse_mode="Markdown"
        )
        return SETUP_START_WEIGHT

async def setup_start_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        w = float(update.message.text.replace(",", "."))
        if w < 30 or w > 300: raise ValueError
        ctx.user_data["setup_start"] = w
        await update.message.reply_text(
            f"✅ Стартовый вес: *{w} кг*\n\nТеперь введи *целевой вес* (кг):",
            parse_mode="Markdown"
        )
        return SETUP_TARGET_WEIGHT
    except:
        await update.message.reply_text("❌ Введи число, например: 82.5")
        return SETUP_START_WEIGHT

async def setup_target_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        w = float(update.message.text.replace(",", "."))
        if w < 30 or w > 300 or w >= ctx.user_data["setup_start"]: raise ValueError
        ctx.user_data["setup_target"] = w
        await update.message.reply_text(
            f"✅ Цель: *{w} кг*\n\nСколько *шагов в день* хочешь делать? (например: 10000)",
            parse_mode="Markdown"
        )
        return SETUP_STEPS_GOAL
    except:
        await update.message.reply_text("❌ Цель должна быть меньше текущего веса")
        return SETUP_TARGET_WEIGHT

async def setup_steps_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        steps = int(update.message.text.replace(" ", ""))
        if steps < 500 or steps > 50000: raise ValueError

        data = load_data()
        user = get_user(data, update.effective_user.id)
        user["settings"] = {
            "startWeight": ctx.user_data["setup_start"],
            "targetWeight": ctx.user_data["setup_target"],
            "stepsGoal": steps
        }
        save_data(data)

        sw = ctx.user_data["setup_start"]
        tw = ctx.user_data["setup_target"]
        diff = sw - tw

        await update.message.reply_text(
            f"🎯 *Настройки сохранены!*\n\n"
            f"⚖️ Старт: *{sw} кг*\n"
            f"🏁 Цель: *{tw} кг*\n"
            f"📉 Нужно сбросить: *{diff:.1f} кг*\n"
            f"👟 Шаги/день: *{steps:,}*\n"
            f"📅 Осталось дней: *{days_left()}*\n\n"
            f"Поехали! Каждый день отмечайся ✅",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Введи число, например: 10000")
        return SETUP_STEPS_GOAL

# ─── MAIN DASHBOARD ───────────────────────────────────────────────
async def show_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    s = user["settings"]

    if not s.get("startWeight"):
        await update.message.reply_text("Сначала настрой цели — /start")
        return

    checkins = user["checkins"]
    td = today_str()
    streak = calc_streak(checkins)
    dl = days_left()

    # Current weight
    weights = [(k, v["weight"]) for k, v in checkins.items() if v.get("weight")]
    weights.sort(reverse=True)
    cur_w = weights[0][1] if weights else s["startWeight"]
    lost = max(0, s["startWeight"] - cur_w)
    to_goal = max(0, cur_w - s["targetWeight"])

    # Progress
    from datetime import date as dt
    start_date = dt(2026, 4, 21)
    total_days = (GOAL_DATE - start_date).days
    elapsed = (dt.today() - start_date).days
    pct = min(100, max(0, int(elapsed / total_days * 100)))
    pbar = progress_bar(elapsed, total_days, 12)

    # Today done?
    today_entry = checkins.get(td, {})
    today_status = "✅ Отмечен!" if today_entry.get("done") else "⚠️ Не отмечен"

    # XP & Level
    xp = user.get("xp", 0)
    _, level_name = get_level(xp)

    # Forecast
    forecast_txt = ""
    if len(weights) >= 3:
        recent = weights[:7]
        if len(recent) >= 2:
            rate = (recent[-1][1] - recent[0][1]) / max(len(recent)-1, 1)  # negative = losing
            if rate < 0:
                days_needed = int(to_goal / abs(rate))
                if days_needed <= dl:
                    forecast_txt = f"\n🔮 Прогноз: успеешь до 27 мая ✓"
                else:
                    forecast_txt = f"\n🔮 Прогноз: нужно ещё {days_needed - dl} дн. — ускоряйся!"

    # Missed streak warning
    miss_warn = ""
    last_2 = [(date.today() - timedelta(days=i)).isoformat() for i in range(1, 3)]
    if not any(checkins.get(d, {}).get("done") for d in last_2):
        miss_warn = "\n\n💀 *2+ дня без отметки. Ты сходишь с дистанции!*"

    text = (
        f"📊 *Главная — {date.today().strftime('%d.%m.%Y')}*\n\n"
        f"⏳ До 27 мая: *{dl} дней*\n"
        f"{pbar} {pct}%\n\n"
        f"⚖️ Вес: *{cur_w:.1f} кг*\n"
        f"📉 Сброшено: *{lost:.1f} кг*\n"
        f"🎯 До цели: *{to_goal:.1f} кг*\n\n"
        f"🔥 Стрик: *{streak} дней подряд*\n"
        f"Сегодня: {today_status}\n\n"
        f"{level_name} · *{xp} XP*"
        f"{forecast_txt}{miss_warn}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# ─── CHECK-IN ─────────────────────────────────────────────────────
async def start_checkin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    td = today_str()

    if user["checkins"].get(td, {}).get("done"):
        entry = user["checkins"][td]
        await update.message.reply_text(
            f"✅ *Сегодня уже отмечен!*\n\n"
            f"⚖️ Вес: {entry.get('weight','—')} кг\n"
            f"👟 Шаги: {entry.get('steps', 0):,}\n"
            f"💪 Тренировка: {'Да' if entry.get('workout') else 'Нет'}\n"
            f"⭐ Оценка: {entry.get('rating', '—')}/10\n\n"
            f"Хочешь изменить? Напиши /checkin",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ *Чек-ин на сегодня*\n\nВведи свой вес (кг):",
        parse_mode="Markdown"
    )
    return CI_WEIGHT

async def ci_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        w = float(update.message.text.replace(",", "."))
        if w < 30 or w > 300: raise ValueError
        ctx.user_data["ci_weight"] = w
        await update.message.reply_text(
            f"⚖️ Вес: *{w} кг* ✓\n\nСколько шагов сегодня? (введи число)",
            parse_mode="Markdown"
        )
        return CI_STEPS
    except:
        await update.message.reply_text("❌ Введи число, например: 76.5")
        return CI_WEIGHT

async def ci_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        steps = int(update.message.text.replace(" ", "").replace(",", ""))
        if steps < 0 or steps > 100000: raise ValueError
        ctx.user_data["ci_steps"] = steps
        data = load_data()
        user = get_user(data, update.effective_user.id)
        goal = user["settings"].get("stepsGoal", 10000)
        pct = min(100, int(steps/goal*100))
        pbar = progress_bar(steps, goal, 10)
        status = "✓ Норма!" if steps >= goal else f"{pct}% от нормы"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💪 Да", callback_data="workout_yes"),
             InlineKeyboardButton("😴 Нет", callback_data="workout_no")]
        ])
        await update.message.reply_text(
            f"👟 Шаги: *{steps:,}* {pbar} {status}\n\nБыла тренировка сегодня?",
            parse_mode="Markdown", reply_markup=kb
        )
        return CI_WORKOUT
    except:
        await update.message.reply_text("❌ Введи число шагов, например: 8500")
        return CI_STEPS

async def ci_workout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["ci_workout"] = query.data == "workout_yes"
    workout_txt = "Да 💪" if ctx.user_data["ci_workout"] else "Нет 😴"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(i), callback_data=f"rating_{i}") for i in range(1, 6)
    ], [
        InlineKeyboardButton(str(i), callback_data=f"rating_{i}") for i in range(6, 11)
    ]])
    await query.edit_message_text(
        f"💪 Тренировка: *{workout_txt}*\n\nОцени день от 1 до 10:",
        parse_mode="Markdown", reply_markup=kb
    )
    return CI_RATING

async def ci_rating(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rating = int(query.data.split("_")[1])
    ctx.user_data["ci_rating"] = rating

    data = load_data()
    user = get_user(data, update.effective_user.id)
    td = today_str()
    s = user["settings"]

    weight = ctx.user_data["ci_weight"]
    steps = ctx.user_data["ci_steps"]
    workout = ctx.user_data["ci_workout"]

    user["checkins"][td] = {
        "done": True,
        "weight": weight,
        "steps": steps,
        "workout": workout,
        "rating": rating,
        "ts": datetime.now().isoformat()
    }

    # XP
    xp = 20
    if workout: xp += 15
    if steps >= s.get("stepsGoal", 10000): xp += 10
    if rating >= 8: xp += 5
    streak = calc_streak(user["checkins"])
    if streak >= 7: xp += 50
    user["xp"] = user.get("xp", 0) + xp

    # Achievements
    _check_achievements(user)
    save_data(data)

    # Weight diff
    prev_weights = [(k, v["weight"]) for k, v in user["checkins"].items()
                    if k < td and v.get("weight")]
    prev_weights.sort(reverse=True)
    diff_txt = ""
    if prev_weights:
        diff = weight - prev_weights[0][1]
        if diff < 0: diff_txt = f" ({diff:+.1f} кг ↓)"
        elif diff > 0: diff_txt = f" ({diff:+.1f} кг ↑)"
        else: diff_txt = " (не изменился)"

    goal_steps = s.get("stepsGoal", 10000)
    steps_ok = "✅" if steps >= goal_steps else "❌"

    await query.edit_message_text(
        f"🎉 *День закрыт! +{xp} XP*\n\n"
        f"⚖️ Вес: *{weight} кг*{diff_txt}\n"
        f"👟 Шаги: *{steps:,}* {steps_ok}\n"
        f"💪 Тренировка: {'Да' if workout else 'Нет'}\n"
        f"⭐ Оценка: *{rating}/10*\n\n"
        f"🔥 Стрик: *{streak} дней подряд*",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── SLEEP ────────────────────────────────────────────────────────
async def start_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    td = today_str()

    if user["sleep"].get(td, {}).get("saved"):
        sl = user["sleep"][td]
        hrs = _sleep_hours(sl["start"], sl["end"])
        score, label = calc_sleep_score(hrs, sl.get("wakeups", 0))
        hh, mm = int(hrs), int((hrs % 1) * 60)
        await update.message.reply_text(
            f"🌙 *Сон уже сохранён!*\n\n"
            f"😴 Лёг: *{sl['start']}*\n"
            f"⏰ Встал: *{sl['end']}*\n"
            f"⏱ Длительность: *{hh}ч {mm}мин*\n"
            f"🌃 Пробуждений: *{sl.get('wakeups',0)}*\n"
            f"📊 Оценка: *{score}/100 — {label}*\n\n"
            f"Хочешь изменить? Напиши /sleep",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🌙 *Трекер сна*\n\n"
        "Во сколько лёг спать?\n"
        "Напиши в формате *ЧЧ:ММ* (например: 23:30 или 01:15)",
        parse_mode="Markdown"
    )
    return SLEEP_START

async def sleep_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t = update.message.text.strip()
        datetime.strptime(t, "%H:%M")
        ctx.user_data["sleep_start"] = t
        await update.message.reply_text(
            f"😴 Лёг в *{t}*\n\nВо сколько проснулся? (ЧЧ:ММ)",
            parse_mode="Markdown"
        )
        return SLEEP_END
    except:
        await update.message.reply_text("❌ Формат: ЧЧ:ММ, например 23:30")
        return SLEEP_START

async def sleep_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t = update.message.text.strip()
        datetime.strptime(t, "%H:%M")
        start = ctx.user_data["sleep_start"]
        hrs = _sleep_hours(start, t)
        if hrs < 1:
            await update.message.reply_text("❌ Слишком мало. Проверь время.")
            return SLEEP_END
        if hrs > 18:
            await update.message.reply_text("❌ Больше 18 часов? Проверь время.")
            return SLEEP_END

        ctx.user_data["sleep_end"] = t
        hh, mm = int(hrs), int((hrs % 1) * 60)
        pbar = progress_bar(min(hrs, 10), 10, 10)

        color = "🟢" if 7 <= hrs <= 9 else "🟡" if hrs >= 6 else "🔴"
        await update.message.reply_text(
            f"⏰ Встал в *{t}*\n"
            f"⏱ Сон: *{hh}ч {mm}мин* {color}\n"
            f"{pbar}\n\n"
            f"Сколько раз просыпался ночью? (введи число: 0, 1, 2...)",
            parse_mode="Markdown"
        )
        return SLEEP_WAKEUPS
    except:
        await update.message.reply_text("❌ Формат: ЧЧ:ММ, например 07:30")
        return SLEEP_END

async def sleep_wakeups(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        w = int(update.message.text.strip())
        if w < 0 or w > 20: raise ValueError

        start = ctx.user_data["sleep_start"]
        end = ctx.user_data["sleep_end"]
        hrs = _sleep_hours(start, end)
        score, label = calc_sleep_score(hrs, w)
        hh, mm = int(hrs), int((hrs % 1) * 60)

        data = load_data()
        user = get_user(data, update.effective_user.id)
        td = today_str()
        user["sleep"][td] = {
            "start": start, "end": end, "wakeups": w,
            "score": score, "saved": True, "ts": datetime.now().isoformat()
        }

        # XP
        xp = 0
        if 7 <= hrs <= 9: xp += 10
        if w == 0: xp += 5
        if xp > 0: user["xp"] = user.get("xp", 0) + xp

        save_data(data)

        wakeup_txt = {0: "Без пробуждений — отлично! 🌟", 1: "1 раз — почти идеально", 2: "2 раза — неплохо"}.get(w, f"{w} раз — многовато")
        xp_txt = f"\n\n+{xp} XP за хороший сон! 🎯" if xp > 0 else ""

        # Sleep advice
        if score >= 85:
            advice = "💪 Идеальный сон. Тело восстановилось полностью."
        elif score >= 70:
            advice = "👍 Хороший отдых. Небольшие отклонения от нормы."
        elif score >= 50:
            advice = "⚠️ Неполноценный сон влияет на похудение."
        else:
            advice = "🚨 Недосып повышает кортизол — жир не уходит!"

        await update.message.reply_text(
            f"🌙 *Сон сохранён!*\n\n"
            f"😴 {start} → ⏰ {end}\n"
            f"⏱ *{hh}ч {mm}мин*\n"
            f"🌃 Пробуждений: *{wakeup_txt}*\n\n"
            f"📊 Оценка: *{score}/100 — {label}*\n"
            f"{advice}{xp_txt}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Введи число: 0, 1, 2...")
        return SLEEP_WAKEUPS

def _sleep_hours(start_str, end_str):
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    start_m = sh * 60 + sm
    end_m = eh * 60 + em
    if end_m <= start_m:
        end_m += 24 * 60
    return (end_m - start_m) / 60

# ─── CALENDAR ─────────────────────────────────────────────────────
async def show_calendar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    checkins = user["checkins"]
    sleep = user["sleep"]

    today = date.today()
    start = date(2026, 4, 21)

    lines = ["📅 *Календарь апрель–май*\n"]
    lines.append("`Пн Вт Ср Чт Пт Сб Вс`")

    months = [(2026, 4), (2026, 5)]
    month_names = {4: "Апрель", 5: "Май"}

    for year, month in months:
        lines.append(f"\n*{month_names[month]} {year}*")
        first_day = date(year, month, 1)
        dow = first_day.weekday()  # 0=Mon
        days_in_month = (date(year, month + 1, 1) - timedelta(days=1)).day if month < 12 else 31

        row = "  " * dow
        for d in range(1, days_in_month + 1):
            cur = date(year, month, d)
            ds = cur.isoformat()
            is_goal = cur == GOAL_DATE
            is_today = cur == today
            is_future = cur > today

            if is_goal:
                cell = "🎯"
            elif is_future:
                cell = "⬜"
            elif cur < start:
                cell = "  "
            else:
                done = checkins.get(ds, {}).get("done")
                has_sleep = sleep.get(ds, {}).get("saved")
                if done and has_sleep:
                    cell = "💚"
                elif done:
                    cell = "✅"
                else:
                    cell = "❌"

            if is_today:
                cell = f"[{cell}]"

            row += cell + " "
            if cur.weekday() == 6:
                lines.append(row.rstrip())
                row = ""

        if row.strip():
            lines.append(row.rstrip())

    # Legend
    lines.append("\n✅ чек-ин · 💚 +сон · ❌ пропуск · 🎯 цель")

    # Stats
    total_done = sum(1 for v in checkins.values() if v.get("done"))
    streak = calc_streak(checkins)
    lines.append(f"\n📊 Отмечено: *{total_done}* дней · Стрик: *{streak}* 🔥")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown",
                                     reply_markup=get_main_keyboard())

# ─── ANALYTICS ────────────────────────────────────────────────────
async def show_analytics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    checkins = user["checkins"]
    sleep_data = user["sleep"]
    s = user["settings"]

    today = date.today()
    last_14 = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]

    # Weight trend
    weights = [(k, v["weight"]) for k, v in checkins.items() if v.get("weight")]
    weights.sort()

    weight_txt = "Нет данных"
    if len(weights) >= 2:
        first_w = weights[0][1]
        last_w = weights[-1][1]
        diff = last_w - first_w
        trend = "↓" if diff < 0 else "↑" if diff > 0 else "→"
        weight_txt = f"{last_w:.1f} кг ({diff:+.1f} кг {trend})"

    # Steps
    steps_14 = [checkins.get(d, {}).get("steps", 0) for d in last_14]
    steps_valid = [s for s in steps_14 if s > 0]
    avg_steps = int(sum(steps_valid) / len(steps_valid)) if steps_valid else 0
    goal_s = s.get("stepsGoal", 10000)
    goal_days = sum(1 for st in steps_valid if st >= goal_s)

    # Discipline
    start_date = date(2026, 4, 21)
    total_past = max(1, (today - start_date).days + 1)
    total_done = sum(1 for v in checkins.values() if v.get("done"))
    disc_rate = int(total_done / total_past * 100)
    streak = calc_streak(checkins)

    # Sleep stats
    sleep_entries = [(k, v) for k, v in sleep_data.items() if v.get("saved")]
    sleep_entries.sort(reverse=True)
    sleep_14 = [v for k, v in sleep_entries if k in last_14]
    avg_sleep_hrs = 0
    avg_sleep_score = 0
    if sleep_14:
        sleep_hrs_list = [_sleep_hours(e["start"], e["end"]) for e in sleep_14]
        avg_sleep_hrs = sum(sleep_hrs_list) / len(sleep_hrs_list)
        avg_sleep_score = int(sum(e.get("score", 0) for e in sleep_14) / len(sleep_14))

    # Week comparison
    this_week = [checkins.get((today - timedelta(days=i)).isoformat(), {}) for i in range(7)]
    last_week = [checkins.get((today - timedelta(days=i+7)).isoformat(), {}) for i in range(7)]
    this_steps = [e.get("steps", 0) for e in this_week if e.get("steps")]
    last_steps = [e.get("steps", 0) for e in last_week if e.get("steps")]
    this_avg = int(sum(this_steps)/len(this_steps)) if this_steps else 0
    last_avg = int(sum(last_steps)/len(last_steps)) if last_steps else 0
    steps_diff = this_avg - last_avg
    steps_diff_txt = f"{steps_diff:+,}" if steps_diff != 0 else "0"

    # Mini steps chart (last 7 days)
    chart = ""
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        st = checkins.get(d, {}).get("steps", 0)
        pct = min(1.0, st / max(goal_s, 1))
        bar = "█" if pct >= 1 else "▇" if pct >= 0.8 else "▅" if pct >= 0.5 else "▂" if pct > 0 else "░"
        chart += bar
    chart_days = " ".join([(today - timedelta(days=i)).strftime("%d") for i in range(6, -1, -1)])

    await update.message.reply_text(
        f"📈 *Аналитика*\n\n"
        f"⚖️ Вес: *{weight_txt}*\n"
        f"📉 Сброшено: *{max(0, s.get('startWeight',0) - (weights[-1][1] if weights else s.get('startWeight',0))):.1f} кг*\n\n"
        f"👟 Шаги (14 дн.)\n"
        f"Среднее: *{avg_steps:,}* · Норма: *{goal_days}* дней\n"
        f"`{chart}`\n"
        f"`{chart_days}`\n\n"
        f"📊 Дисциплина: *{disc_rate}%* · Стрик: *{streak}* 🔥\n"
        f"Отмечено: *{total_done}* из {total_past} дней\n\n"
        f"🌙 Сон (14 дн.)\n"
        f"Среднее: *{avg_sleep_hrs:.1f}ч* · Оценка: *{avg_sleep_score}/100*\n\n"
        f"📅 Эта неделя vs прошлая\n"
        f"Шаги: *{this_avg:,}* ({steps_diff_txt})\n"
        f"Чек-ины: *{sum(1 for e in this_week if e.get('done'))}* vs *{sum(1 for e in last_week if e.get('done'))}*",
        parse_mode="Markdown", reply_markup=get_main_keyboard()
    )

# ─── GAME ─────────────────────────────────────────────────────────
async def show_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    xp = user.get("xp", 0)
    checkins = user["checkins"]
    achievements = user.get("achievements", [])

    levels = [(0,"⚡ Новичок",100),(100,"🥊 Боец",250),(250,"🏃 Атлет",500),(500,"🏆 Чемпион",1000),(1000,"🤖 Машина",9999)]
    cur_level = levels[0]
    next_level = levels[1]
    for i, (min_xp, name, _) in enumerate(levels):
        if xp >= min_xp:
            cur_level = levels[i]
            next_level = levels[i+1] if i+1 < len(levels) else None

    pbar = progress_bar(xp - cur_level[0], (next_level[0] if next_level else xp) - cur_level[0], 12)
    next_txt = f"До {next_level[1]}: {next_level[0]-xp} XP" if next_level else "Максимальный уровень! 🏆"

    streak = calc_streak(checkins)

    # Achievements display
    all_achievements = [
        ("first_checkin", "🎯", "Первый шаг"),
        ("streak7",       "🔥", "7 дней подряд"),
        ("streak14",      "💪", "14 дней подряд"),
        ("minus1",        "⚖️", "-1 кг"),
        ("minus3",        "🎉", "-3 кг"),
        ("steps10k",      "👟", "10к шагов"),
        ("steps5days",    "🦵", "5×10к шагов"),
        ("workout7",      "🏋️", "7 тренировок"),
        ("good_sleep",    "🌙", "Отличный сон"),
    ]

    ach_txt = ""
    for aid, icon, name in all_achievements:
        if aid in achievements:
            ach_txt += f"✅ {icon} {name}\n"
        else:
            ach_txt += f"🔒 {name}\n"

    # Challenges
    entries = [v for v in checkins.values() if v.get("done")]
    workout_count = sum(1 for e in entries if e.get("workout"))
    step10_streak = 0
    today = date.today()
    for i in range(30):
        d = (today - timedelta(days=i)).isoformat()
        if checkins.get(d, {}).get("steps", 0) >= 10000:
            step10_streak += 1
        else:
            break

    await update.message.reply_text(
        f"🎮 *Игра*\n\n"
        f"*{cur_level[1]}*\n"
        f"{pbar} *{xp} XP*\n"
        f"{next_txt}\n\n"
        f"*Достижения:*\n{ach_txt}\n"
        f"*Челленджи:*\n"
        f"{'✅' if streak >= 7 else '🔄'} 7 дней подряд: {min(streak,7)}/7\n"
        f"{'✅' if step10_streak >= 5 else '🔄'} 10к шагов 5 дней: {min(step10_streak,5)}/5\n"
        f"{'✅' if workout_count >= 7 else '🔄'} 7 тренировок: {min(workout_count,7)}/7\n\n"
        f"*XP за действия:*\n"
        f"+20 чек-ин · +15 тренировка\n"
        f"+10 норма шагов · +5 оценка 8+\n"
        f"+50 стрик 7 дней · +10 сон 7-9ч",
        parse_mode="Markdown", reply_markup=get_main_keyboard()
    )

# ─── SETTINGS ─────────────────────────────────────────────────────
async def show_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = get_user(data, update.effective_user.id)
    s = user["settings"]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Изменить цели", callback_data="reset_settings")],
        [InlineKeyboardButton("📤 Экспорт данных", callback_data="export_data")],
    ])

    total_xp = user.get("xp", 0)
    total_done = sum(1 for v in user["checkins"].values() if v.get("done"))
    total_sleep = sum(1 for v in user["sleep"].values() if v.get("saved"))

    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"⚖️ Стартовый вес: *{s.get('startWeight','—')} кг*\n"
        f"🎯 Целевой вес: *{s.get('targetWeight','—')} кг*\n"
        f"👟 Норма шагов: *{s.get('stepsGoal',10000):,}*\n\n"
        f"📊 Данные:\n"
        f"Чек-инов: *{total_done}*\n"
        f"Записей сна: *{total_sleep}*\n"
        f"Всего XP: *{total_xp}*\n\n"
        f"💾 Данные хранятся на сервере — не пропадут!",
        parse_mode="Markdown", reply_markup=kb
    )

async def settings_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "reset_settings":
        await query.edit_message_text(
            "Введи новый стартовый вес для пересчёта целей:"
        )
        return SETUP_START_WEIGHT

    if query.data == "export_data":
        data = load_data()
        user = get_user(data, update.effective_user.id)
        export = json.dumps(user, ensure_ascii=False, indent=2)
        await query.message.reply_document(
            document=export.encode("utf-8"),
            filename=f"fit27_data_{today_str()}.json",
            caption="📤 Твои данные — можешь сохранить как резервную копию"
        )

# ─── ACHIEVEMENTS CHECK ───────────────────────────────────────────
def _check_achievements(user):
    unlocked = user.get("achievements", [])
    checkins = user["checkins"]
    s = user["settings"]

    def unlock(aid):
        if aid not in unlocked:
            unlocked.append(aid)

    entries = [v for v in checkins.values() if v.get("done")]
    if entries: unlock("first_checkin")

    streak = calc_streak(checkins)
    if streak >= 7: unlock("streak7")
    if streak >= 14: unlock("streak14")

    weights = sorted([(k,v["weight"]) for k,v in checkins.items() if v.get("weight")])
    if weights and s.get("startWeight"):
        lost = s["startWeight"] - weights[-1][1]
        if lost >= 1: unlock("minus1")
        if lost >= 3: unlock("minus3")

    if any(e.get("steps", 0) >= 10000 for e in entries):
        unlock("steps10k")

    today = date.today()
    consec = 0
    for i in range(30):
        d = (today - timedelta(days=i)).isoformat()
        if checkins.get(d, {}).get("steps", 0) >= 10000:
            consec += 1
        else:
            break
    if consec >= 5: unlock("steps5days")

    if sum(1 for e in entries if e.get("workout")) >= 7:
        unlock("workout7")

    user["achievements"] = unlocked

# ─── TEXT ROUTER ──────────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "Главная" in text or "📊" in text:
        await show_home(update, ctx)
    elif "Чек-ин" in text or "✅" in text:
        await start_checkin(update, ctx)
    elif "Сон" in text or "🌙" in text:
        await start_sleep(update, ctx)
    elif "Календарь" in text or "📅" in text:
        await show_calendar(update, ctx)
    elif "Аналитика" in text or "📈" in text:
        await show_analytics(update, ctx)
    elif "Игра" in text or "🎮" in text:
        await show_game(update, ctx)
    elif "Настройки" in text or "⚙️" in text:
        await show_settings(update, ctx)
    else:
        await update.message.reply_text(
            "Выбери раздел ниже 👇",
            reply_markup=get_main_keyboard()
        )

# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    # Setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            SETUP_START_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_start_weight)],
            SETUP_TARGET_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_target_weight)],
            SETUP_STEPS_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_steps_goal)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    )

    # Checkin conversation
    checkin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("checkin", start_checkin),
            MessageHandler(filters.Regex("Чек-ин|✅"), start_checkin),
        ],
        states={
            CI_WEIGHT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ci_weight)],
            CI_STEPS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ci_steps)],
            CI_WORKOUT: [CallbackQueryHandler(ci_workout, pattern="^workout_")],
            CI_RATING:  [CallbackQueryHandler(ci_rating, pattern="^rating_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    # Sleep conversation
    sleep_conv = ConversationHandler(
        entry_points=[
            CommandHandler("sleep", start_sleep),
            MessageHandler(filters.Regex("Сон|🌙"), start_sleep),
        ],
        states={
            SLEEP_START:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sleep_start)],
            SLEEP_END:     [MessageHandler(filters.TEXT & ~filters.COMMAND, sleep_end)],
            SLEEP_WAKEUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, sleep_wakeups)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    app.add_handler(setup_conv)
    app.add_handler(checkin_conv)
    app.add_handler(sleep_conv)
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^(reset_settings|export_data)$"))
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
