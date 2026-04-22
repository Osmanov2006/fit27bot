import os, json, logging, math
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler)
from telegram.error import BadRequest
 
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "data.json"
 
# ── States ────────────────────────────────────────────────────────
(SETUP_GOAL, SETUP_NAME, SETUP_AGE, SETUP_HEIGHT, SETUP_SW,
 SETUP_TW, SETUP_DATE, SETUP_ACTIVITY, SETUP_SG) = range(9)
W_DATE, W_VAL       = range(20, 22)
S_DATE, S_VAL       = range(30, 32)
WO_DATE, WO_VAL     = range(40, 42)
SL_DATE, SL_START, SL_END, SL_WAKES = range(50, 54)
RT_DATE, RT_VAL     = range(60, 62)
 
# ── Storage ───────────────────────────────────────────────────────
def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}
 
def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
 
def get_user(data, uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"settings":{}, "days":{}, "sleep":{}, "xp":0, "achievements":[]}
    if "checkins" in data[uid] and "days" not in data[uid]:
        data[uid]["days"] = data[uid].pop("checkins")
    return data[uid]
 
def get_day(u, ds): 
    if ds not in u["days"]: u["days"][ds] = {}
    return u["days"][ds]
 
def today_s(): return date.today().isoformat()
def ds(d): return d.isoformat() if isinstance(d, date) else d
 
# ── Delete helper ─────────────────────────────────────────────────
async def safe_delete(bot, chat_id, msg_id):
    try: await bot.delete_message(chat_id, msg_id)
    except: pass
 
async def delete_prev(ctx, chat_id):
    mid = ctx.user_data.get("last_bot_msg")
    if mid: await safe_delete(ctx.bot, chat_id, mid)
 
async def send_and_track(update_or_query, ctx, text, keyboard=None, parse_mode="Markdown"):
    """Send message, delete previous bot message, track new one"""
    is_query = hasattr(update_or_query, 'message') and hasattr(update_or_query, 'answer')
    if is_query:
        chat_id = update_or_query.message.chat_id
    else:
        chat_id = update_or_query.effective_chat.id
    await delete_prev(ctx, chat_id)
    kwargs = {"text": text, "parse_mode": parse_mode}
    if keyboard: kwargs["reply_markup"] = keyboard
    if is_query:
        msg = await update_or_query.message.reply_text(**kwargs)
    else:
        msg = await update_or_query.message.reply_text(**kwargs)
    ctx.user_data["last_bot_msg"] = msg.message_id
    return msg
 
# ── Helpers ───────────────────────────────────────────────────────
def pbar(val, mx, length=8):
    f = int((val/max(mx,1))*length)
    return "▓"*f + "░"*(length-f)
 
def streak(days):
    d = date.today()
    if not days.get(ds(d),{}).get("weight"): d -= timedelta(1)
    n = 0
    for _ in range(200):
        if days.get(ds(d),{}).get("weight"): n+=1; d-=timedelta(1)
        else: break
    return n
 
def sleep_hrs(s, e):
    sh,sm=map(int,s.split(":")); eh,em=map(int,e.split(":"))
    s2=sh*60+sm; e2=eh*60+em
    if e2<=s2: e2+=1440
    return (e2-s2)/60
 
def sleep_score(hrs, wakes):
    d=50 if 7<=hrs<=9 else 40 if hrs>=6.5 else 28 if hrs>=6 else 15 if hrs>=5 else 5
    w=[50,38,26,16,10,5,0][min(wakes,6)]
    t=d+w
    if t>=85: return t,"🌟 Отличный"
    if t>=70: return t,"😴 Хороший"
    if t>=50: return t,"😐 Средний"
    return t,"😮 Плохой"
 
def get_level(xp):
    for mn,nm in reversed([(0,"⚡ Новичок"),(150,"🥊 Боец"),(350,"🏃 Атлет"),(700,"🏆 Чемпион"),(1500,"🤖 Машина")]):
        if xp>=mn: return mn,nm
    return 0,"⚡ Новичок"
 
def dlabel(ds_str):
    d2 = datetime.strptime(ds_str,"%Y-%m-%d").date()
    if d2==date.today(): return "сегодня"
    if d2==date.today()-timedelta(1): return "вчера"
    return d2.strftime("%d.%m.%Y")
 
def days_left(s):
    try:
        goal_d = datetime.strptime(s["goalDate"], "%Y-%m-%d").date()
        return max(0,(goal_d-date.today()).days)
    except: return 0
 
def calc_nutrition(s):
    """
    Mifflin-St Jeor BMR → TDEE → target calories.
    Дефицит считается исходя из срока и кол-ва кг — реалистично.
    """
    age      = s.get("age", 25)
    height   = s.get("height", 175)
    weight   = s.get("currentWeight", s.get("startWeight", 75))
    gender   = s.get("gender", "male")
    activity = s.get("activity", 1.55)
    goal     = s.get("goal", "lose")
    diff_kg  = abs(s.get("startWeight", 75) - s.get("targetWeight", 70))
    dl       = days_left(s) if days_left(s) > 0 else 60
 
    # Mifflin-St Jeor BMR
    if gender == "male":
        bmr = 10*weight + 6.25*height - 5*age + 5
    else:
        bmr = 10*weight + 6.25*height - 5*age - 161
 
    tdee = bmr * activity
 
    if goal == "lose":
        # 1 кг жира = ~7700 ккал. Считаем нужный дефицит под срок.
        needed_deficit_total = diff_kg * 7700
        daily_deficit = needed_deficit_total / dl
        # Ограничиваем: минимум 200, максимум 1000 ккал дефицита
        daily_deficit = max(200, min(1000, daily_deficit))
        kcal = max(1300 if gender=="female" else 1500, int(tdee - daily_deficit))
    elif goal == "gain":
        needed_surplus_total = diff_kg * 7700
        daily_surplus = needed_surplus_total / dl
        daily_surplus = max(150, min(600, daily_surplus))
        kcal = int(tdee + daily_surplus)
    else:
        kcal = int(tdee)
 
    return {"bmr": int(bmr), "tdee": int(tdee), "kcal": kcal}
 
def goal_emoji(goal):
    return {"lose":"📉 Похудение","gain":"📈 Набор массы","maintain":"⚖️ Поддержание"}[goal]
 
def activity_name(a):
    return {1.2:"🛋 Минимум",1.375:"🚶 Лёгкая",1.55:"🏃 Средняя",1.725:"💪 Высокая",1.9:"🏋️ Очень высокая"}[a]
 
def main_kb():
    return ReplyKeyboardMarkup([
        ["🏠 Главная",    "📅 Календарь"],
        ["⚖️ Вес",        "👟 Шаги"],
        ["💪 Тренировка", "🌙 Сон"],
        ["⭐ Оценка дня", "🍎 Калории"],
        ["📈 Аналитика",  "🎮 Игра"],
        ["⚙️ Настройки"],
    ], resize_keyboard=True)
 
def day_kb(prefix, days=5):
    today_d = date.today()
    labels = ["Сегодня","Вчера","2 дня назад","3 дня назад","4 дня назад"]
    rows = [[InlineKeyboardButton(
        f"{labels[i]} ({(today_d-timedelta(i)).strftime('%d.%m')})",
        callback_data=f"{prefix}_{ds(today_d-timedelta(i))}")] for i in range(days)]
    return InlineKeyboardMarkup(rows)
 
# ── SETUP ────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); save(data)
    if u["settings"].get("startWeight"):
        msg = await update.message.reply_text("👋 С возвращением!", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Похудеть", callback_data="goal_lose")],
        [InlineKeyboardButton("📈 Набрать массу", callback_data="goal_gain")],
        [InlineKeyboardButton("⚖️ Оставаться в форме", callback_data="goal_maintain")],
    ])
    msg = await update.message.reply_text(
        "🔥 *FIT TRACKER*\n\nДобро пожаловать! Я помогу тебе достичь цели.\n\n*Что хочешь?*",
        parse_mode="Markdown", reply_markup=kb)
    ctx.user_data["last_bot_msg"] = msg.message_id
    return SETUP_GOAL
 
async def setup_goal(update: Update, ctx):
    q=update.callback_query; await q.answer()
    goal = q.data.replace("goal_","")
    ctx.user_data["goal"] = goal
    await q.edit_message_text(
        f"Отлично! *{goal_emoji(goal)}*\n\nКак тебя зовут?",
        parse_mode="Markdown")
    return SETUP_NAME
 
async def setup_name(update: Update, ctx):
    name = update.message.text.strip()[:20]
    ctx.user_data["name"] = name
    await delete_prev(ctx, update.effective_chat.id)
    await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
    msg = await update.message.reply_text(f"Привет, *{name}*! 👋\n\nСколько тебе лет?", parse_mode="Markdown")
    ctx.user_data["last_bot_msg"] = msg.message_id
    return SETUP_AGE
 
async def setup_age(update: Update, ctx):
    try:
        age = int(update.message.text.strip()); assert 10<=age<=100
        ctx.user_data["age"] = age
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨 Мужской", callback_data="gen_male"),
             InlineKeyboardButton("👩 Женский", callback_data="gen_female")]
        ])
        msg = await update.message.reply_text(f"*{age} лет* ✓\n\nПол:", parse_mode="Markdown", reply_markup=kb)
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_HEIGHT
    except:
        msg = await update.message.reply_text("❌ Введи число от 10 до 100")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_AGE
 
async def setup_gender(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ctx.user_data["gender"] = q.data.replace("gen_","")
    gen_txt = "👨 Мужской" if ctx.user_data["gender"]=="male" else "👩 Женский"
    await q.edit_message_text(f"*{gen_txt}* ✓\n\nВведи свой рост (см), например: `178`", parse_mode="Markdown")
    return SETUP_HEIGHT
 
async def setup_height(update: Update, ctx):
    try:
        h = int(update.message.text.strip()); assert 100<=h<=250
        ctx.user_data["height"] = h
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        msg = await update.message.reply_text(f"📏 *{h} см* ✓\n\nТекущий вес (кг), например: `82.5`", parse_mode="Markdown")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_SW
    except:
        msg = await update.message.reply_text("❌ Введи рост от 100 до 250")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_HEIGHT
 
async def setup_sw(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<300
        ctx.user_data["sw"]=w
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        goal = ctx.user_data.get("goal","lose")
        prompt = "📉 Целевой вес (кг):" if goal=="lose" else "📈 Целевой вес (кг):" if goal=="gain" else None
        if prompt:
            msg = await update.message.reply_text(f"⚖️ *{w} кг* ✓\n\n{prompt}", parse_mode="Markdown")
            ctx.user_data["last_bot_msg"] = msg.message_id
            return SETUP_TW
        else:
            ctx.user_data["tw"] = w
            return await _ask_date(update, ctx)
    except:
        msg = await update.message.reply_text("❌ Введи число, например: 82.5")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_SW
 
async def setup_tw(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<300
        goal = ctx.user_data.get("goal","lose")
        if goal=="lose": assert w<ctx.user_data["sw"]
        if goal=="gain": assert w>ctx.user_data["sw"]
        ctx.user_data["tw"]=w
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        return await _ask_date(update, ctx)
    except:
        msg = await update.message.reply_text("❌ Для похудения: цель < текущего. Для набора: цель > текущего.")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_TW
 
async def _ask_date(update, ctx):
    today_d = date.today()
    presets = [
        (today_d+timedelta(weeks=4), "1 месяц"),
        (today_d+timedelta(weeks=8), "2 месяца"),
        (today_d+timedelta(weeks=12), "3 месяца"),
        (today_d+timedelta(weeks=24), "6 месяцев"),
        (date(2026,5,27), "27 мая 2026"),
        (date(2026,12,31), "Конец 2026"),
    ]
    rows = []
    for d2, lbl in presets:
        rows.append([InlineKeyboardButton(f"{lbl} ({d2.strftime('%d.%m.%Y')})", callback_data=f"gdate_{ds(d2)}")])
    rows.append([InlineKeyboardButton("✏️ Ввести свою дату", callback_data="gdate_manual")])
    msg = await update.message.reply_text("📅 *До какой даты цель?*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return SETUP_DATE
 
async def setup_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="gdate_manual":
        await q.edit_message_text("📅 Введи дату цели в формате *ДД.ММ.ГГГГ*, например: `31.12.2026`", parse_mode="Markdown")
        return SETUP_DATE
    ctx.user_data["goalDate"] = q.data.replace("gdate_","")
    await q.edit_message_text("📅 Дата цели сохранена ✓\n\nКакой у тебя уровень активности?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛋 Сижу дома", callback_data="act_1.2")],
            [InlineKeyboardButton("🚶 Лёгкая (1-2 тренировки/нед)", callback_data="act_1.375")],
            [InlineKeyboardButton("🏃 Средняя (3-5 тренировок/нед)", callback_data="act_1.55")],
            [InlineKeyboardButton("💪 Высокая (6-7 тренировок/нед)", callback_data="act_1.725")],
            [InlineKeyboardButton("🏋️ Очень высокая (2×день)", callback_data="act_1.9")],
        ]))
    return SETUP_ACTIVITY
 
async def setup_date_text(update: Update, ctx):
    try:
        d2 = datetime.strptime(update.message.text.strip(), "%d.%m.%Y").date()
        assert d2 > date.today()
        ctx.user_data["goalDate"] = ds(d2)
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        msg = await update.message.reply_text("📅 Дата сохранена ✓\n\nУровень активности?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛋 Сижу дома", callback_data="act_1.2")],
                [InlineKeyboardButton("🚶 Лёгкая", callback_data="act_1.375")],
                [InlineKeyboardButton("🏃 Средняя (3-5/нед)", callback_data="act_1.55")],
                [InlineKeyboardButton("💪 Высокая (6-7/нед)", callback_data="act_1.725")],
                [InlineKeyboardButton("🏋️ Очень высокая", callback_data="act_1.9")],
            ]))
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_ACTIVITY
    except:
        msg = await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ, например: 31.12.2026")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_DATE
 
async def setup_activity(update: Update, ctx):
    q=update.callback_query; await q.answer()
    activity = float(q.data.replace("act_",""))
    ctx.user_data["activity"] = activity
    await q.edit_message_text(f"*{activity_name(activity)}* ✓\n\nНорма шагов в день (например: `10000`):", parse_mode="Markdown")
    return SETUP_SG
 
async def setup_sg(update: Update, ctx):
    try:
        steps=int(update.message.text.replace(" ","")); assert 500<=steps<=50000
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
 
        data=load(); u=get_user(data,update.effective_user.id)
        s = {
            "name":       ctx.user_data.get("name",""),
            "goal":       ctx.user_data.get("goal","lose"),
            "age":        ctx.user_data.get("age",25),
            "gender":     ctx.user_data.get("gender","male"),
            "height":     ctx.user_data.get("height",175),
            "startWeight":ctx.user_data.get("sw",80),
            "targetWeight":ctx.user_data.get("tw",70),
            "currentWeight":ctx.user_data.get("sw",80),
            "goalDate":   ctx.user_data.get("goalDate", ds(date.today()+timedelta(weeks=12))),
            "activity":   ctx.user_data.get("activity",1.55),
            "stepsGoal":  steps,
            "setupDate":  today_s(),
        }
        u["settings"] = s
        nut = calc_nutrition(s)
        save(data)
 
        goal_d = datetime.strptime(s["goalDate"],"%Y-%m-%d").date()
        dl = (goal_d - date.today()).days
        diff = abs(s["startWeight"]-s["targetWeight"])
        goal_txt = goal_emoji(s["goal"])
 
        msg = await update.message.reply_text(
            f"🎯 *Всё настроено, {s['name']}!*\n\n"
            f"*Цель:* {goal_txt}\n"
            f"⚖️ {s['startWeight']} → {s['targetWeight']} кг ({'+' if s['goal']=='gain' else '-'}{diff:.1f} кг)\n"
            f"📅 Дедлайн: *{goal_d.strftime('%d.%m.%Y')}* ({dl} дней)\n\n"
            f"*🍎 Норма калорий:*\n"
            f"🔥 *{nut['kcal']} ккал/день*\n"
            f"(базовый обмен {nut['bmr']} + активность = {nut['tdee']}, цель = {nut['kcal']})\n"
            f"👟 Шаги: *{steps:,}/день*\n\n"
            f"Используй кнопки ниже 👇",
            parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
        return ConversationHandler.END
    except:
        msg = await update.message.reply_text("❌ Например: 10000")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SETUP_SG
 
# ── HOME ──────────────────────────────────────────────────────────
async def show_home(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    s=u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала /start"); return
 
    await delete_prev(ctx, update.effective_chat.id)
 
    days_d=u["days"]; td=today_s(); td_day=days_d.get(td,{})
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")],reverse=True)
    cur_w=ws[0][1] if ws else s["startWeight"]
    s["currentWeight"]=cur_w
 
    diff = cur_w - s["targetWeight"]
    diff_abs = abs(diff)
    if s.get("goal")=="gain":
        progress_txt = f"📈 Набрано: *+{max(0,cur_w-s['startWeight']):.1f} кг* · осталось: *{diff_abs:.1f} кг*"
    else:
        lost=max(0,s["startWeight"]-cur_w)
        progress_txt = f"📉 Сброшено: *{lost:.1f} кг* · осталось: *{diff_abs:.1f} кг*"
 
    dl = days_left(s)
    try:
        goal_d=datetime.strptime(s["goalDate"],"%Y-%m-%d").date()
        start_d=datetime.strptime(s.get("setupDate",today_s()),"%Y-%m-%d").date()
        total=(goal_d-start_d).days; elapsed=(date.today()-start_d).days
        pct=min(100,max(0,int(elapsed/max(total,1)*100)))
        pb=pbar(elapsed,total,12)
    except: pct=0; pb="░"*12
 
    nut=calc_nutrition(s)
    strk=streak(days_d); xp=u.get("xp",0); _,lvl=get_level(xp)
 
    w_today  = f"⚖️ {td_day['weight']} кг" if td_day.get("weight") else "⚖️ вес не введён"
    s_today  = f"👟 {td_day['steps']:,}" if td_day.get("steps") is not None else "👟 шаги не введены"
    wo_today = "💪 тренировка: " + ("да ✅" if td_day.get("workout") else "нет") if "workout" in td_day else "💪 не отмечена"
    sl_today = u["sleep"].get(td,{})
    sl_txt=""
    if sl_today.get("saved"):
        hrs=sleep_hrs(sl_today["start"],sl_today["end"])
        sc,lbl=sleep_score(hrs,sl_today.get("wakeups",0))
        sl_txt=f"\n🌙 Сон: {int(hrs)}ч {int((hrs%1)*60)}мин — {lbl}"
 
    goal_d_fmt = datetime.strptime(s["goalDate"],"%Y-%m-%d").strftime("%d.%m.%Y")
 
    msg = await update.message.reply_text(
        f"🏠 *{s.get('name','')} · {date.today().strftime('%d.%m.%Y')}*\n\n"
        f"{goal_emoji(s.get('goal','lose'))} · до {goal_d_fmt}: *{dl} дн.*\n"
        f"{pb} {pct}%\n\n"
        f"⚖️ Вес: *{cur_w:.1f} кг*\n"
        f"{progress_txt}\n\n"
        f"🔥 Стрик: *{strk} дней* · {lvl} · {xp} XP\n\n"
        f"*Сегодня:*\n{w_today}\n{s_today}\n{wo_today}{sl_txt}\n\n"
        f"🍎 Норма: *{nut['kcal']} ккал/день*",
        parse_mode="Markdown", reply_markup=main_kb())
    ctx.user_data["last_bot_msg"] = msg.message_id
 
# ── CALORIES ─────────────────────────────────────────────────────
FOOD_DATE, FOOD_VAL = range(70, 72)
 
async def show_calories(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    s=u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала /start"); return
    await delete_prev(ctx, update.effective_chat.id)
 
    ws=sorted([(k,v["weight"]) for k,v in u["days"].items() if v.get("weight")],reverse=True)
    if ws: s["currentWeight"]=ws[0][1]
    nut=calc_nutrition(s)
    gender_txt="👨 Мужчина" if s.get("gender")=="male" else "👩 Женщина"
    goal_txt=goal_emoji(s.get("goal","lose"))
 
    # Today's eaten calories
    td=today_s(); today_kcal=u["days"].get(td,{}).get("kcal_eaten",0)
    remaining=nut["kcal"]-today_kcal
    remain_txt=f"✅ В норме! Осталось: *{remaining} ккал*" if remaining>=0 else f"⚠️ Превышение на *{abs(remaining)} ккал*"
 
    # Last 7 days log
    today_d = date.today()
    history = ""
    for i in range(6, -1, -1):
        d2 = (today_d - timedelta(i)).isoformat()
        eaten = u["days"].get(d2, {}).get("kcal_eaten")
        if eaten is not None:
            diff = eaten - nut["kcal"]
            mark = "✅" if diff <= 0 else "⚠️"
            history += f"\n  {mark} {dlabel(d2)}: *{eaten}* ккал ({diff:+d})"
 
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Внести калории за сегодня", callback_data="food_today")],
        [InlineKeyboardButton("📅 За другой день", callback_data="food_other")]
    ])
 
    text = (
        f"🍎 *Калории*\n\n"
        f"{gender_txt} · {s.get('age')} лет · {s.get('height')} см · {s.get('currentWeight', s.get('startWeight'))} кг\n"
        f"Активность: {activity_name(s.get('activity', 1.55))}\n\n"
        f"🧬 Базовый обмен (BMR): *{nut['bmr']} ккал*\n"
        f"⚡ С учётом активности (TDEE): *{nut['tdee']} ккал*\n\n"
        f"🎯 {goal_txt}\n"
        f"🔥 *Твоя норма: {nut['kcal']} ккал/день*\n\n"
        f"*Сегодня съедено:* {today_kcal if today_kcal else '—'} ккал\n"
        + (f"{remain_txt}\n" if today_kcal else "")
        + (f"\n*История:*{history}" if history else "")
    )
 
    msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    ctx.user_data["last_bot_msg"] = msg.message_id
 
async def food_log_start(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="food_today":
        ctx.user_data["food_date"]=today_s()
        await q.edit_message_text(
            "🍎 Сколько калорий съел сегодня?\n\nВведи число (например: `1850`):",
            parse_mode="Markdown")
    else:
        await q.edit_message_text("📅 За какой день?", reply_markup=day_kb("food"))
    return FOOD_DATE
 
async def food_date_select(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("food_",""); ctx.user_data["food_date"]=ds_val
    data=load(); u=get_user(data,q.from_user.id)
    existing=u["days"].get(ds_val,{}).get("kcal_eaten")
    hint=f"\nТекущее: *{existing} ккал*" if existing else ""
    await q.edit_message_text(
        f"🍎 Калории за *{dlabel(ds_val)}*{hint}\n\nВведи сколько съел (ккал):",
        parse_mode="Markdown")
    return FOOD_VAL
 
async def food_val(update: Update, ctx):
    try:
        kcal=int(update.message.text.replace(" ","").replace(",","")); assert 0<=kcal<=10000
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        ds_val=ctx.user_data.get("food_date", today_s())
        data=load(); u=get_user(data,update.effective_user.id)
        s=u["settings"]
        ws=sorted([(k,v["weight"]) for k,v in u["days"].items() if v.get("weight")],reverse=True)
        if ws: s["currentWeight"]=ws[0][1]
        nut=calc_nutrition(s)
        get_day(u,ds_val)["kcal_eaten"]=kcal
        save(data)
        diff=kcal-nut["kcal"]
        if diff<=0:
            verdict=f"✅ В норме! (норма {nut['kcal']} ккал, ты в дефиците {abs(diff)} ккал)"
        elif diff<=200:
            verdict=f"⚠️ Чуть больше нормы (+{diff} ккал)"
        else:
            verdict=f"❌ Превышение нормы на {diff} ккал"
        await delete_prev(ctx, update.effective_chat.id)
        msg=await update.message.reply_text(
            f"✅ *{kcal} ккал* за *{dlabel(ds_val)}* сохранено!\n\n{verdict}",
            parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"]=msg.message_id
        return ConversationHandler.END
    except:
        msg=await update.message.reply_text("❌ Введи число калорий, например: 1850")
        ctx.user_data["last_bot_msg"]=msg.message_id
        return FOOD_VAL
 
# ── WEIGHT ────────────────────────────────────────────────────────
async def start_weight(update: Update, ctx):
    await delete_prev(ctx, update.effective_chat.id)
    msg = await update.message.reply_text("⚖️ *Вес*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("wd"))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return W_DATE
 
async def weight_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("wd_",""); ctx.user_data["w_date"]=ds_val
    data=load(); u=get_user(data,q.from_user.id)
    existing=u["days"].get(ds_val,{}).get("weight")
    sw=u["settings"].get("startWeight",80)
    base=existing if existing else sw
    weights=[round(base-0.5+i*0.1,1) for i in range(11)]
    rows=[]; row=[]
    for w in weights:
        row.append(InlineKeyboardButton(f"{w}", callback_data=f"wv_{w}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data="wv_manual")])
    hint=f"\nТекущее: *{existing} кг*" if existing else ""
    await q.edit_message_text(f"⚖️ Вес за *{dlabel(ds_val)}*{hint}\n\nВыбери:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    return W_VAL
 
async def weight_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="wv_manual":
        await q.edit_message_text("⚖️ Введи вес (кг):", parse_mode="Markdown")
        return W_VAL
    w=float(q.data.replace("wv_",""))
    return await _save_weight(q, ctx, w, True)
 
async def weight_val_text(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<300
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        return await _save_weight(update, ctx, w, False)
    except:
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        msg = await update.message.reply_text("❌ Введи число, например: 76.3")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return W_VAL
 
async def _save_weight(obj, ctx, w, is_query):
    ds_val=ctx.user_data["w_date"]
    uid=obj.from_user.id
    data=load(); u=get_user(data,uid)
    day=get_day(u,ds_val)
    prev=day.get("weight"); day["weight"]=w
    u["settings"]["currentWeight"]=w
    u["xp"]=u.get("xp",0)+5
    check_achievements(u); save(data)
    diff_txt=""
    if prev: d2=w-prev; diff_txt=f" ({d2:+.1f} кг {'📉' if d2<0 else '📈'})"
 
    s=u["settings"]
    target=s.get("targetWeight",w)
    to_go=abs(w-target)
    nut=calc_nutrition(s)
 
    text=(f"✅ *Вес {w} кг* за *{dlabel(ds_val)}* сохранён{diff_txt}\n"
          f"До цели: *{to_go:.1f} кг* · Норма: *{nut['kcal']} ккал/день*")
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown")
    else:
        msg = await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
    return ConversationHandler.END
 
# ── STEPS ────────────────────────────────────────────────────────
async def start_steps(update: Update, ctx):
    await delete_prev(ctx, update.effective_chat.id)
    msg = await update.message.reply_text("👟 *Шаги*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("sd"))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return S_DATE
 
async def steps_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("sd_",""); ctx.user_data["s_date"]=ds_val
    presets=[0,2000,4000,5000,6000,7000,8000,9000,10000,12000,15000,20000]
    rows=[]; row=[]
    for s in presets:
        row.append(InlineKeyboardButton(f"{s//1000}к" if s>=1000 else "0", callback_data=f"sv_{s}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести точно", callback_data="sv_manual")])
    await q.edit_message_text(f"👟 Шаги за *{dlabel(ds_val)}*\n\nВыбери:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    return S_VAL
 
async def steps_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="sv_manual":
        await q.edit_message_text("👟 Введи количество шагов:", parse_mode="Markdown")
        return S_VAL
    return await _save_steps(q, ctx, int(q.data.replace("sv_","")), True)
 
async def steps_val_text(update: Update, ctx):
    try:
        s=int(update.message.text.replace(" ","").replace(",","")); assert 0<=s<=100000
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        return await _save_steps(update, ctx, s, False)
    except:
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        msg = await update.message.reply_text("❌ Введи число шагов")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return S_VAL
 
async def _save_steps(obj, ctx, s, is_query):
    ds_val=ctx.user_data["s_date"]; uid=obj.from_user.id
    data=load(); u=get_user(data,uid)
    get_day(u,ds_val)["steps"]=s
    goal=u["settings"].get("stepsGoal",10000)
    xp_earn=10 if s>=goal else 3
    u["xp"]=u.get("xp",0)+xp_earn
    # Bonus: walking burns calories
    burned=int(s*0.04)  # ~0.04 kcal per step
    check_achievements(u); save(data)
    pb=pbar(s,goal,8); tag="✅ Норма!" if s>=goal else f"{int(s/goal*100)}%"
    text=f"✅ *Шаги {s:,}* за *{dlabel(ds_val)}*\n{pb} {tag}\n🔥 Сожжено ~{burned} ккал · +{xp_earn} XP"
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown")
    else:
        msg = await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
    return ConversationHandler.END
 
# ── WORKOUT ──────────────────────────────────────────────────────
async def start_workout(update: Update, ctx):
    await delete_prev(ctx, update.effective_chat.id)
    msg = await update.message.reply_text("💪 *Тренировка*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("wod"))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return WO_DATE
 
async def workout_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("wod_",""); ctx.user_data["wo_date"]=ds_val
    await q.edit_message_text(f"💪 Тренировка за *{dlabel(ds_val)}*\n\nБыла?", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💪 Да, была!", callback_data="wov_yes"),
            InlineKeyboardButton("😴 Нет", callback_data="wov_no")]]))
    return WO_VAL
 
async def workout_val(update: Update, ctx):
    q=update.callback_query; await q.answer()
    wo=q.data=="wov_yes"; ds_val=ctx.user_data["wo_date"]
    data=load(); u=get_user(data,q.from_user.id)
    get_day(u,ds_val)["workout"]=wo
    xp_earn=15 if wo else 0
    if xp_earn: u["xp"]=u.get("xp",0)+xp_earn
    check_achievements(u); save(data)
    txt="💪 Тренировка отмечена! 🔥" if wo else "😴 Без тренировки"
    xp_txt=f" +{xp_earn} XP" if xp_earn else ""
    await q.edit_message_text(f"✅ *{txt}* за *{dlabel(ds_val)}*{xp_txt}", parse_mode="Markdown")
    return ConversationHandler.END
 
# ── RATING ───────────────────────────────────────────────────────
async def start_rating(update: Update, ctx):
    await delete_prev(ctx, update.effective_chat.id)
    msg = await update.message.reply_text("⭐ *Оценка дня*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("rtd"))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return RT_DATE
 
async def rating_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("rtd_",""); ctx.user_data["rt_date"]=ds_val
    rows=[[InlineKeyboardButton(str(i), callback_data=f"rtv_{i}") for i in range(1,6)],
          [InlineKeyboardButton(str(i), callback_data=f"rtv_{i}") for i in range(6,11)]]
    await q.edit_message_text(f"⭐ Оценка за *{dlabel(ds_val)}* (1–10):",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    return RT_VAL
 
async def rating_val(update: Update, ctx):
    q=update.callback_query; await q.answer()
    r=int(q.data.replace("rtv_","")); ds_val=ctx.user_data["rt_date"]
    data=load(); u=get_user(data,q.from_user.id)
    get_day(u,ds_val)["rating"]=r
    xp_earn=5 if r>=8 else 0
    if xp_earn: u["xp"]=u.get("xp",0)+xp_earn
    check_achievements(u); save(data)
    stars="⭐"*r
    await q.edit_message_text(f"✅ *{stars}* ({r}/10) за *{dlabel(ds_val)}*"+(f" +{xp_earn} XP" if xp_earn else ""),
        parse_mode="Markdown")
    return ConversationHandler.END
 
# ── SLEEP ────────────────────────────────────────────────────────
async def start_sleep(update: Update, ctx):
    await delete_prev(ctx, update.effective_chat.id)
    msg = await update.message.reply_text("🌙 *Сон*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("sld"))
    ctx.user_data["last_bot_msg"] = msg.message_id
    return SL_DATE
 
async def sleep_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("sld_",""); ctx.user_data["sl_date"]=ds_val
    presets=["21:00","22:00","22:30","23:00","23:30","00:00","00:30","01:00","02:00"]
    rows=[]; row=[]
    for t in presets:
        row.append(InlineKeyboardButton(t, callback_data=f"slst_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое", callback_data="slst_manual")])
    await q.edit_message_text(f"🌙 Сон за *{dlabel(ds_val)}*\n\n😴 Лёг в:", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return SL_START
 
async def sleep_start_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="slst_manual":
        await q.edit_message_text("😴 Введи время (ЧЧ:ММ):", parse_mode="Markdown"); return SL_START
    t=q.data.replace("slst_",""); ctx.user_data["sl_start"]=t
    await q.edit_message_text(f"😴 Лёг в *{t}*\n\n⏰ Проснулся в:", parse_mode="Markdown", reply_markup=_wake_kb())
    return SL_END
 
async def sleep_start_text(update: Update, ctx):
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        ctx.user_data["sl_start"]=t
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        await delete_prev(ctx, update.effective_chat.id)
        msg = await update.message.reply_text(f"😴 Лёг в *{t}*\n\n⏰ Проснулся в:", parse_mode="Markdown", reply_markup=_wake_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SL_END
    except:
        msg = await update.message.reply_text("❌ Формат ЧЧ:ММ")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SL_START
 
def _wake_kb():
    presets=["05:00","05:30","06:00","06:30","07:00","07:30","08:00","08:30","09:00","10:00"]
    rows=[]; row=[]
    for t in presets:
        row.append(InlineKeyboardButton(t, callback_data=f"slet_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое", callback_data="slet_manual")])
    return InlineKeyboardMarkup(rows)
 
async def sleep_end_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="slet_manual":
        await q.edit_message_text("⏰ Введи время пробуждения (ЧЧ:ММ):", parse_mode="Markdown"); return SL_END
    return await _proc_end(q, ctx, q.data.replace("slet_",""), True)
 
async def sleep_end_text(update: Update, ctx):
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
        return await _proc_end(update, ctx, t, False)
    except:
        msg = await update.message.reply_text("❌ Формат ЧЧ:ММ")
        ctx.user_data["last_bot_msg"] = msg.message_id
        return SL_END
 
async def _proc_end(obj, ctx, t, is_query):
    start=ctx.user_data["sl_start"]; hrs=sleep_hrs(start,t)
    if hrs<1 or hrs>18:
        msg_txt="❌ Слишком мало." if hrs<1 else "❌ Больше 18ч? Проверь."
        if is_query: await obj.edit_message_text(msg_txt)
        else:
            msg = await obj.message.reply_text(msg_txt)
            ctx.user_data["last_bot_msg"] = msg.message_id
        return SL_END
    ctx.user_data["sl_end"]=t
    hh=int(hrs); mm=int((hrs%1)*60)
    color="🟢" if 7<=hrs<=9 else "🟡" if hrs>=6 else "🔴"
    rows=[[InlineKeyboardButton("0 — без пробуждений 🌟", callback_data="slw_0")],
          [InlineKeyboardButton("1",callback_data="slw_1"),InlineKeyboardButton("2",callback_data="slw_2"),InlineKeyboardButton("3",callback_data="slw_3")],
          [InlineKeyboardButton("4",callback_data="slw_4"),InlineKeyboardButton("5+",callback_data="slw_5")]]
    text=f"⏰ Встал в *{t}* · *{hh}ч {mm}мин* {color}\n\n🌃 Сколько раз просыпался?"
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else:
        msg = await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        ctx.user_data["last_bot_msg"] = msg.message_id
    return SL_WAKES
 
async def sleep_wakes(update: Update, ctx):
    q=update.callback_query; await q.answer()
    w=int(q.data.replace("slw_","")); ds_val=ctx.user_data["sl_date"]
    start=ctx.user_data["sl_start"]; end=ctx.user_data["sl_end"]
    hrs=sleep_hrs(start,end); sc,lbl=sleep_score(hrs,w)
    hh=int(hrs); mm=int((hrs%1)*60)
    data=load(); u=get_user(data,q.from_user.id)
    u["sleep"][ds_val]={"start":start,"end":end,"wakeups":w,"score":sc,"saved":True,"ts":datetime.now().isoformat()}
    xp_earn=0
    if 7<=hrs<=9: xp_earn+=10
    if w==0: xp_earn+=5
    if xp_earn: u["xp"]=u.get("xp",0)+xp_earn
    check_achievements(u); save(data)
    wt=["Без пробуждений 🌟","1 раз","2 раза","3 раза","4 раза","5+ раз"][min(w,5)]
    advice="💪 Идеально!" if sc>=85 else "👍 Хорошо" if sc>=70 else "⚠️ Недосып мешает прогрессу!" if sc<50 else "👌 Неплохо"
    await q.edit_message_text(
        f"🌙 *Сон за {dlabel(ds_val)}*\n\n😴 {start} → ⏰ {end} · *{hh}ч {mm}мин*\n"
        f"🌃 {wt} · *{sc}/100 {lbl}*\n{advice}"+(f"\n+{xp_earn} XP" if xp_earn else ""),
        parse_mode="Markdown")
    return ConversationHandler.END
 
# ── CALENDAR ─────────────────────────────────────────────────────
async def show_calendar(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days_d=u["days"]; sl=u["sleep"]; s=u["settings"]
    today_d=date.today()
    await delete_prev(ctx, update.effective_chat.id)
 
    # ── Часть 1: последние 7 дней подробно ──
    lines=["📅 *Последние 7 дней*\n━━━━━━━━━━━━━━━"]
    weekdays=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
 
    # Считаем ожидаемый вес на каждый день (линейная прогрессия)
    start_w = s.get("startWeight", 0)
    target_w = s.get("targetWeight", 0)
    goal_mode = s.get("goal","lose")
    try:
        setup_d = datetime.strptime(s.get("setupDate", today_s()), "%Y-%m-%d").date()
        goal_d  = datetime.strptime(s["goalDate"], "%Y-%m-%d").date()
        total_days = max((goal_d - setup_d).days, 1)
        total_diff = target_w - start_w  # negative for lose, positive for gain
    except:
        setup_d = today_d; total_days = 60; total_diff = 0
 
    for i in range(6,-1,-1):
        d = today_d - timedelta(i)
        ds_val = ds(d)
        day = days_d.get(ds_val, {})
        sl_day = sl.get(ds_val, {})
        dow = weekdays[d.weekday()]
        is_today = d == today_d
        mark = "📍" if is_today else "  "
 
        # Expected weight on this day
        elapsed_i = max((d - setup_d).days, 0)
        expected_w = round(start_w + total_diff * elapsed_i / total_days, 1) if total_days > 0 else None
 
        lines.append(f"\n{mark} *{dow} {d.strftime('%d.%m')}*{'  ← сегодня' if is_today else ''}")
 
        actual_w = day.get("weight")
        if actual_w and expected_w:
            diff_w = round(actual_w - expected_w, 1)
            if goal_mode == "lose":
                status = "✅ впереди плана!" if diff_w < -0.2 else ("⚠️ отстаёшь" if diff_w > 0.3 else "👍 по плану")
            else:
                status = "✅ впереди!" if diff_w > 0.2 else ("⚠️ отстаёшь" if diff_w < -0.3 else "👍 по плану")
            lines.append(f"  ⚖️ {actual_w} кг (план: {expected_w}) {status}")
        elif actual_w:
            lines.append(f"  ⚖️ {actual_w} кг")
        elif expected_w and d <= today_d:
            lines.append(f"  ⚖️ нет данных (план: {expected_w} кг)")
 
        if day.get("steps") is not None:
            goal_s = s.get("stepsGoal", 10000)
            ok = "✅" if day["steps"] >= goal_s else "❌"
            lines.append(f"  👟 {day['steps']:,} шагов {ok}")
        if "workout" in day:
            lines.append(f"  💪 {'Тренировка ✅' if day['workout'] else 'Без тренировки'}")
        if day.get("kcal_eaten"):
            nut = calc_nutrition(s)
            diff_k = day["kcal_eaten"] - nut["kcal"]
            kmark = "✅" if diff_k <= 0 else "⚠️"
            lines.append(f"  🍎 {day['kcal_eaten']} ккал {kmark}")
        if sl_day.get("saved"):
            hrs = sleep_hrs(sl_day["start"], sl_day["end"])
            _, lbl = sleep_score(hrs, sl_day.get("wakeups", 0))
            lines.append(f"  🌙 {int(hrs)}ч {int((hrs%1)*60)}м — {lbl}")
        if not any([day.get("weight"), day.get("steps") is not None, "workout" in day, sl_day.get("saved")]):
            lines.append("  · нет данных")
        lines.append("  ──────────────")
 
    # ── Часть 2: мини-календарь до дедлайна ──
    lines.append("\n📆 *До цели*")
    try:
        goal_d2 = datetime.strptime(s["goalDate"], "%Y-%m-%d").date()
        dl = (goal_d2 - today_d).days
        lines.append(f"Осталось *{dl} дней* до {goal_d2.strftime('%d.%m.%Y')}\n")
 
        # Рисуем мини-календарь: следующие недели до дедлайна
        # Найдём понедельник текущей недели
        start_cal = today_d - timedelta(today_d.weekday())
        end_cal = goal_d2
 
        dow_labels = "Пн Вт Ср Чт Пт Сб Вс"
        lines.append(f"`{dow_labels}`")
 
        cur = start_cal
        row_cells = []
        # Pad to Monday
        while cur <= min(end_cal, today_d + timedelta(weeks=8)):
            cell_date = cur
            if cell_date < today_d:
                day_entry = days_d.get(ds(cell_date), {})
                if day_entry.get("weight"):
                    cell = "✅"
                elif cell_date >= setup_d:
                    cell = "❌"
                else:
                    cell = "· "
            elif cell_date == today_d:
                cell = "📍"
            elif cell_date == goal_d2:
                cell = "🎯"
            else:
                cell = "⬜"
            row_cells.append(cell)
            if len(row_cells) == 7:
                lines.append("`" + " ".join(row_cells) + "`")
                row_cells = []
            cur += timedelta(1)
        if row_cells:
            while len(row_cells) < 7:
                row_cells.append("  ")
            lines.append("`" + " ".join(row_cells) + "`")
 
        lines.append(f"\n✅ был вес · ❌ пропуск · 📍 сегодня · 🎯 дедлайн")
    except:
        pass
 
    # Stats
    done = sum(1 for v in days_d.values() if v.get("weight"))
    strk = streak(days_d)
 
    # Progress vs plan
    ws = sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    plan_txt = ""
    if ws and expected_w:
        actual_last = ws[-1][1]
        exp_today = round(start_w + total_diff * (today_d - setup_d).days / total_days, 1)
        diff_plan = round(actual_last - exp_today, 1)
        if goal_mode == "lose":
            plan_txt = f"\n{'✅ Опережаешь план на ' + str(abs(diff_plan)) + ' кг!' if diff_plan < -0.2 else ('⚠️ Отстаёшь от плана на ' + str(abs(diff_plan)) + ' кг' if diff_plan > 0.3 else '👍 Идёшь по плану')}"
        else:
            plan_txt = f"\n{'✅ Опережаешь!' if diff_plan > 0.2 else ('⚠️ Отстаёшь на ' + str(abs(diff_plan)) + ' кг' if diff_plan < -0.3 else '👍 По плану')}"
 
    lines.append(f"\n📊 Стрик: *{strk}* 🔥 · Дней с данными: *{done}*{plan_txt}")
 
    # Send in parts if too long
    text = "\n".join(lines)
    if len(text) > 4000:
        mid = len(lines)//2
        msg = await update.message.reply_text("\n".join(lines[:mid]), parse_mode="Markdown")
        ctx.user_data["last_bot_msg"] = msg.message_id
        msg = await update.message.reply_text("\n".join(lines[mid:]), parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
    else:
        msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
 
# ── ANALYTICS ────────────────────────────────────────────────────
async def show_analytics(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days_d=u["days"]; sl=u["sleep"]; s=u["settings"]
    today_d=date.today()
    await delete_prev(ctx, update.effective_chat.id)
 
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    if ws:
        diff=ws[-1][1]-ws[0][1]
        trend="📉" if diff<0 else "📈"
        w_txt=f"*{ws[-1][1]:.1f} кг* ({diff:+.1f} кг {trend})"
    else: w_txt="нет данных"
    lost=max(0,s.get("startWeight",0)-(ws[-1][1] if ws else s.get("startWeight",0)))
    goal_mode=s.get("goal","lose")
    if goal_mode=="gain":
        gained=max(0,(ws[-1][1] if ws else s.get("startWeight",0))-s.get("startWeight",0))
        progress_txt=f"📈 Набрано: *+{gained:.1f} кг*"
    else:
        progress_txt=f"📉 Сброшено: *{lost:.1f} кг*"
 
    last14=[(today_d-timedelta(i)).isoformat() for i in range(13,-1,-1)]
    steps14=[days_d.get(d,{}).get("steps",0) for d in last14 if days_d.get(d,{}).get("steps") is not None]
    avg_s=int(sum(steps14)/len(steps14)) if steps14 else 0
    goal_s=s.get("stepsGoal",10000)
    goal_days=sum(1 for s2 in steps14 if s2>=goal_s)
    chart="".join(["█" if (days_d.get(d,{}).get("steps",0) or 0)>=goal_s
        else "▇" if (days_d.get(d,{}).get("steps",0) or 0)>=goal_s*0.8
        else "▅" if (days_d.get(d,{}).get("steps",0) or 0)>=goal_s*0.5
        else "▂" if (days_d.get(d,{}).get("steps",0) or 0)>0 else "░"
        for d in last14[-7:]])
 
    elapsed=(today_d-datetime.strptime(s.get("setupDate",today_s()),"%Y-%m-%d").date()).days+1
    done=sum(1 for v in days_d.values() if v.get("weight"))
    disc=int(done/max(elapsed,1)*100)
    strk=streak(days_d)
 
    sl_entries=[v for v in sl.values() if v.get("saved")]
    if sl_entries:
        hrs_l=[sleep_hrs(e["start"],e["end"]) for e in sl_entries]
        avg_sl=sum(hrs_l)/len(hrs_l)
        avg_sc=int(sum(e.get("score",0) for e in sl_entries)/len(sl_entries))
        sl_txt=f"*{avg_sl:.1f}ч* · оценка *{avg_sc}/100*"
    else: sl_txt="нет данных"
 
    nut=calc_nutrition(s)
    tw=[days_d.get((today_d-timedelta(i)).isoformat(),{}) for i in range(7)]
    lw=[days_d.get((today_d-timedelta(i+7)).isoformat(),{}) for i in range(7)]
    tw_s=[e.get("steps",0) for e in tw if e.get("steps")]
    lw_s=[e.get("steps",0) for e in lw if e.get("steps")]
    tw_a=int(sum(tw_s)/len(tw_s)) if tw_s else 0
    lw_a=int(sum(lw_s)/len(lw_s)) if lw_s else 0
 
    # Forecast
    fc=""
    if len(ws)>=3:
        rate=(ws[-1][1]-ws[0][1])/max(len(ws)-1,1)
        to_go=abs(ws[-1][1]-s.get("targetWeight",ws[-1][1]))
        if abs(rate)>0.001:
            dn=int(to_go/abs(rate))
            dl=days_left(s)
            fc=f"\n🔮 Прогноз: цель через *{dn} дн.* {'✓' if dn<=dl else '— нужно ускориться!'}"
 
    msg = await update.message.reply_text(
        f"📈 *Аналитика · {goal_emoji(goal_mode)}*\n\n"
        f"⚖️ Вес: {w_txt}\n{progress_txt}{fc}\n\n"
        f"🍎 Норма: *{nut['kcal']} ккал/день*\n\n"
        f"👟 Шаги (14 дн.) · среднее *{avg_s:,}* · норма *{goal_days}* дней\n"
        f"`{chart}` ← 7 дней\n\n"
        f"📊 Дисциплина *{disc}%* · Стрик *{strk}* 🔥\n\n"
        f"🌙 Сон: {sl_txt}\n\n"
        f"Эта неделя vs прошлая\n"
        f"Шаги: *{tw_a:,}* vs *{lw_a:,}* ({tw_a-lw_a:+,})\n"
        f"Дней с данными: *{sum(1 for e in tw if e.get('weight'))}* vs *{sum(1 for e in lw if e.get('weight'))}*",
        parse_mode="Markdown", reply_markup=main_kb())
    ctx.user_data["last_bot_msg"] = msg.message_id
 
# ── GAME ─────────────────────────────────────────────────────────
async def show_game(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    xp=u.get("xp",0); days_d=u["days"]; unlocked=u.get("achievements",[])
    await delete_prev(ctx, update.effective_chat.id)
 
    lvls=[(0,"⚡ Новичок",150),(150,"🥊 Боец",350),(350,"🏃 Атлет",700),(700,"🏆 Чемпион",1500),(1500,"🤖 Машина",9999)]
    cur=lvls[0]; nxt=lvls[1]
    for i,(mn,nm,_) in enumerate(lvls):
        if xp>=mn: cur=lvls[i]; nxt=lvls[i+1] if i+1<len(lvls) else None
    pb=pbar(xp-cur[0],(nxt[0] if nxt else xp)-cur[0],10)
    nxt_txt=f"До *{nxt[1]}*: {nxt[0]-xp} XP" if nxt else "🏆 Максимальный уровень!"
    strk=streak(days_d)
    entries=list(days_d.values())
    wo_count=sum(1 for e in entries if e.get("workout"))
    step_strk=0
    for i in range(30):
        d2=ds(date.today()-timedelta(i))
        if days_d.get(d2,{}).get("steps",0)>=10000: step_strk+=1
        else: break
    ach_def=[("first_weight","⚖️","Первое взвешивание"),("streak7","🔥","7 дней подряд"),
             ("streak14","💪","14 дней подряд"),("minus1","📉","-1 кг"),("minus3","🎉","-3 кг"),
             ("steps10k","👟","10к шагов"),("steps5days","🦵","5×10к шагов подряд"),
             ("workout7","🏋️","7 тренировок"),("good_sleep","🌙","Отличный сон 85+")]
    ach_txt="".join(f"{'✅' if a in unlocked else '🔒'} {ic} {nm}\n" for a,ic,nm in ach_def)
    msg = await update.message.reply_text(
        f"🎮 *Игра*\n\n*{cur[1]}*\n{pb} *{xp} XP*\n{nxt_txt}\n\n"
        f"*Достижения:*\n{ach_txt}\n"
        f"*Челленджи:*\n"
        f"{'✅' if strk>=7 else f'{strk}/7 🔄'} 7 дней подряд\n"
        f"{'✅' if step_strk>=5 else f'{step_strk}/5 🔄'} 10к шагов × 5 дней\n"
        f"{'✅' if wo_count>=7 else f'{wo_count}/7 🔄'} 7 тренировок\n\n"
        f"*Как получить XP:*\n+5 вес · +10 шаги (норма) · +15 тренировка\n+5 оценка 8+ · +50 стрик 7д · +10 сон",
        parse_mode="Markdown", reply_markup=main_kb())
    ctx.user_data["last_bot_msg"] = msg.message_id
 
# ── SETTINGS ─────────────────────────────────────────────────────
async def show_settings(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); s=u["settings"]
    await delete_prev(ctx, update.effective_chat.id)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Изменить цели и данные", callback_data="reset_setup")],
        [InlineKeyboardButton("📤 Экспорт данных (JSON)", callback_data="export_data")],
    ])
    done=sum(1 for v in u["days"].values() if v.get("weight"))
    goal_d_fmt=""
    try: goal_d_fmt=datetime.strptime(s["goalDate"],"%Y-%m-%d").strftime("%d.%m.%Y")
    except: pass
    msg = await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"👤 Имя: *{s.get('name','—')}*\n"
        f"📏 Рост: *{s.get('height','—')} см* · Возраст: *{s.get('age','—')} лет*\n"
        f"⚖️ Старт: *{s.get('startWeight','—')} кг* → цель: *{s.get('targetWeight','—')} кг*\n"
        f"🎯 Цель: *{goal_emoji(s.get('goal','lose'))}* до *{goal_d_fmt}*\n"
        f"🏃 Активность: *{activity_name(s.get('activity',1.55))}*\n"
        f"👟 Шаги/день: *{s.get('stepsGoal',10000):,}*\n\n"
        f"Дней с данными: *{done}* · XP: *{u.get('xp',0)}*\n"
        f"💾 Данные хранятся на сервере Railway",
        parse_mode="Markdown", reply_markup=kb)
    ctx.user_data["last_bot_msg"] = msg.message_id
 
async def settings_cb(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="export_data":
        data=load(); u=get_user(data,q.from_user.id)
        js=json.dumps(u,ensure_ascii=False,indent=2)
        await q.message.reply_document(document=js.encode("utf-8"),
            filename=f"fittracker_{today_s()}.json", caption="📤 Твои данные")
    elif q.data=="reset_setup":
        await q.edit_message_text("Начнём заново. Какая цель?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📉 Похудеть", callback_data="goal_lose")],
                [InlineKeyboardButton("📈 Набрать массу", callback_data="goal_gain")],
                [InlineKeyboardButton("⚖️ Оставаться в форме", callback_data="goal_maintain")],
            ]))
        return SETUP_GOAL
 
# ── Achievements ─────────────────────────────────────────────────
def check_achievements(u):
    ul=u.get("achievements",[]); days_d=u["days"]; s=u["settings"]
    def unlock(a):
        if a not in ul: ul.append(a)
    entries=list(days_d.values())
    if any(v.get("weight") for v in entries): unlock("first_weight")
    strk=streak(days_d)
    if strk>=7: unlock("streak7")
    if strk>=14: unlock("streak14")
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    if ws and s.get("startWeight"):
        diff=s["startWeight"]-ws[-1][1]
        if abs(diff)>=1: unlock("minus1")
        if abs(diff)>=3: unlock("minus3")
    if any(e.get("steps",0)>=10000 for e in entries): unlock("steps10k")
    cs=0
    for i in range(30):
        d2=ds(date.today()-timedelta(i))
        if days_d.get(d2,{}).get("steps",0)>=10000: cs+=1
        else: break
    if cs>=5: unlock("steps5days")
    if sum(1 for e in entries if e.get("workout"))>=7: unlock("workout7")
    if any(v.get("score",0)>=85 for v in u.get("sleep",{}).values() if v.get("saved")): unlock("good_sleep")
    u["achievements"]=ul
 
# ── Text router ───────────────────────────────────────────────────
async def handle_text(update: Update, ctx):
    t=update.message.text
    await safe_delete(ctx.bot, update.effective_chat.id, update.message.message_id)
    if any(x in t for x in ["Главная","🏠"]): await show_home(update,ctx)
    elif any(x in t for x in ["Календарь","📅"]): await show_calendar(update,ctx)
    elif any(x in t for x in ["Аналитика","📈"]): await show_analytics(update,ctx)
    elif any(x in t for x in ["Игра","🎮"]): await show_game(update,ctx)
    elif any(x in t for x in ["Настройки","⚙️"]): await show_settings(update,ctx)
    elif any(x in t for x in ["Калории","🍎"]): await show_calories(update,ctx)
    else:
        msg = await update.message.reply_text("Выбери раздел 👇", reply_markup=main_kb())
        ctx.user_data["last_bot_msg"] = msg.message_id
 
# ── Main ─────────────────────────────────────────────────────────
def main():
    app=Application.builder().token(TOKEN).build()
 
    def conv(entries, states):
        return ConversationHandler(entry_points=entries, states=states,
            fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)])
 
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start",cmd_start)],
        states={
            SETUP_GOAL:     [CallbackQueryHandler(setup_goal, pattern="^goal_")],
            SETUP_NAME:     [MessageHandler(filters.TEXT&~filters.COMMAND, setup_name)],
            SETUP_AGE:      [MessageHandler(filters.TEXT&~filters.COMMAND, setup_age),
                             CallbackQueryHandler(setup_gender, pattern="^gen_")],
            SETUP_HEIGHT:   [CallbackQueryHandler(setup_gender, pattern="^gen_"),
                             MessageHandler(filters.TEXT&~filters.COMMAND, setup_height)],
            SETUP_SW:       [MessageHandler(filters.TEXT&~filters.COMMAND, setup_sw)],
            SETUP_TW:       [MessageHandler(filters.TEXT&~filters.COMMAND, setup_tw)],
            SETUP_DATE:     [CallbackQueryHandler(setup_date, pattern="^gdate_"),
                             MessageHandler(filters.TEXT&~filters.COMMAND, setup_date_text)],
            SETUP_ACTIVITY: [CallbackQueryHandler(setup_activity, pattern="^act_")],
            SETUP_SG:       [MessageHandler(filters.TEXT&~filters.COMMAND, setup_sg)],
        },
        fallbacks=[CommandHandler("start",cmd_start)]))
 
    app.add_handler(conv(
        [CommandHandler("weight",start_weight), MessageHandler(filters.Regex("⚖️|Вес"),start_weight)],
        {W_DATE:[CallbackQueryHandler(weight_date,pattern="^wd_")],
         W_VAL:[CallbackQueryHandler(weight_val_btn,pattern="^wv_"),
                MessageHandler(filters.TEXT&~filters.COMMAND,weight_val_text)]}))
 
    app.add_handler(conv(
        [CommandHandler("steps",start_steps), MessageHandler(filters.Regex("👟|Шаги"),start_steps)],
        {S_DATE:[CallbackQueryHandler(steps_date,pattern="^sd_")],
         S_VAL:[CallbackQueryHandler(steps_val_btn,pattern="^sv_"),
                MessageHandler(filters.TEXT&~filters.COMMAND,steps_val_text)]}))
 
    app.add_handler(conv(
        [CommandHandler("workout",start_workout), MessageHandler(filters.Regex("💪|Тренировка"),start_workout)],
        {WO_DATE:[CallbackQueryHandler(workout_date,pattern="^wod_")],
         WO_VAL:[CallbackQueryHandler(workout_val,pattern="^wov_")]}))
 
    app.add_handler(conv(
        [CommandHandler("rating",start_rating), MessageHandler(filters.Regex("⭐|Оценка"),start_rating)],
        {RT_DATE:[CallbackQueryHandler(rating_date,pattern="^rtd_")],
         RT_VAL:[CallbackQueryHandler(rating_val,pattern="^rtv_")]}))
 
    app.add_handler(conv(
        [CommandHandler("sleep",start_sleep), MessageHandler(filters.Regex("🌙|Сон"),start_sleep)],
        {SL_DATE:[CallbackQueryHandler(sleep_date,pattern="^sld_")],
         SL_START:[CallbackQueryHandler(sleep_start_btn,pattern="^slst_"),
                   MessageHandler(filters.TEXT&~filters.COMMAND,sleep_start_text)],
         SL_END:[CallbackQueryHandler(sleep_end_btn,pattern="^slet_"),
                 MessageHandler(filters.TEXT&~filters.COMMAND,sleep_end_text)],
         SL_WAKES:[CallbackQueryHandler(sleep_wakes,pattern="^slw_")]}))
 
    app.add_handler(conv(
        [CallbackQueryHandler(food_log_start, pattern="^food_(today|other)$"),
         CommandHandler("food", show_calories)],
        {FOOD_DATE:[CallbackQueryHandler(food_date_select, pattern="^food_"),
                    MessageHandler(filters.TEXT&~filters.COMMAND, food_val)],
         FOOD_VAL:[MessageHandler(filters.TEXT&~filters.COMMAND, food_val)]}))
 
    app.add_handler(CallbackQueryHandler(settings_cb, pattern="^(reset_setup|export_data)$"))
    app.add_handler(CallbackQueryHandler(settings_cb, pattern="^goal_"))
    app.add_handler(CommandHandler("home",show_home))
    app.add_handler(CommandHandler("calories",show_calories))
    app.add_handler(CommandHandler("calendar",show_calendar))
    app.add_handler(CommandHandler("analytics",show_analytics))
    app.add_handler(CommandHandler("game",show_game))
    app.add_handler(CommandHandler("settings",show_settings))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)
 
if __name__=="__main__":
    main()
