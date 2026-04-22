import os, json, logging
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler)

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "data.json"
GOAL_DATE  = date(2026, 5, 27)
START_DATE = date(2026, 4, 21)

# ── States ────────────────────────────────────────────────────────
SETUP_SW, SETUP_TW, SETUP_SG = range(3)
W_DATE, W_VAL                = range(10, 12)
S_DATE, S_VAL                = range(20, 22)
WO_DATE, WO_VAL              = range(30, 32)
SL_DATE, SL_START, SL_END, SL_WAKES = range(40, 44)
RT_DATE, RT_VAL              = range(50, 52)

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
    # migrate old checkins key
    if "checkins" in data[uid] and "days" not in data[uid]:
        data[uid]["days"] = data[uid].pop("checkins")
    return data[uid]

def get_day(u, ds):
    if ds not in u["days"]: u["days"][ds] = {}
    return u["days"][ds]

def today_s(): return date.today().isoformat()
def ds(d): return d.isoformat() if isinstance(d, date) else d

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
    if t>=30: return t,"😮 Плохой"
    return t,"💀 Критично"

def get_level(xp):
    for mn,nm in reversed([(0,"⚡ Новичок"),(100,"🥊 Боец"),(250,"🏃 Атлет"),(500,"🏆 Чемпион"),(1000,"🤖 Машина")]):
        if xp>=mn: return mn,nm
    return 0,"⚡ Новичок"

def days_left(): return max(0,(GOAL_DATE-date.today()).days)

def main_kb():
    return ReplyKeyboardMarkup([
        ["🏠 Главная",   "📅 Календарь"],
        ["⚖️ Вес",       "👟 Шаги"],
        ["💪 Тренировка","🌙 Сон"],
        ["⭐ Оценка дня","📈 Аналитика"],
        ["🎮 Игра",      "⚙️ Настройки"],
    ], resize_keyboard=True)

def day_kb(prefix, days=5):
    today_d = date.today()
    labels = ["Сегодня","Вчера","2 дня назад","3 дня назад","4 дня назад"]
    rows = []
    for i in range(days):
        d2 = today_d - timedelta(i)
        rows.append([InlineKeyboardButton(
            f"{labels[i]} ({d2.strftime('%d.%m')})",
            callback_data=f"{prefix}_{ds(d2)}")])
    return InlineKeyboardMarkup(rows)

def dlabel(ds_str):
    d2 = datetime.strptime(ds_str,"%Y-%m-%d").date()
    if d2==date.today(): return "сегодня"
    if d2==date.today()-timedelta(1): return "вчера"
    return d2.strftime("%d.%m.%Y")

# ── /start & setup ────────────────────────────────────────────────
async def cmd_start(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); save(data)
    if u["settings"].get("startWeight"):
        await update.message.reply_text("👋 С возвращением!", reply_markup=main_kb())
        return ConversationHandler.END
    await update.message.reply_text(
        "🔥 *FIT до 27 МАЯ*\n\nВведи текущий вес (кг), например: `82.5`",
        parse_mode="Markdown")
    return SETUP_SW

async def setup_sw(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<300
        ctx.user_data["sw"]=w
        await update.message.reply_text(f"✅ Стартовый вес: *{w} кг*\n\nЦелевой вес (кг):", parse_mode="Markdown")
        return SETUP_TW
    except: await update.message.reply_text("❌ Например: 82.5"); return SETUP_SW

async def setup_tw(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<ctx.user_data["sw"]
        ctx.user_data["tw"]=w
        await update.message.reply_text(f"✅ Цель: *{w} кг*\n\nНорма шагов в день:", parse_mode="Markdown")
        return SETUP_SG
    except: await update.message.reply_text("❌ Цель должна быть меньше текущего веса"); return SETUP_TW

async def setup_sg(update: Update, ctx):
    try:
        s=int(update.message.text.replace(" ","")); assert 500<=s<=50000
        data=load(); u=get_user(data,update.effective_user.id)
        u["settings"]={"startWeight":ctx.user_data["sw"],"targetWeight":ctx.user_data["tw"],"stepsGoal":s}
        save(data)
        diff=ctx.user_data["sw"]-ctx.user_data["tw"]
        await update.message.reply_text(
            f"🎯 *Готово!*\n\n⚖️ Старт: *{ctx.user_data['sw']} кг*\n"
            f"🏁 Цель: *{ctx.user_data['tw']} кг* (−{diff:.1f} кг)\n"
            f"👟 Шаги/день: *{s:,}* · До 27 мая: *{days_left()} дней*\n\n"
            f"Используй кнопки ниже 👇\n"
            f"*⚖️ Вес* — утром\n*👟 Шаги* — вечером\n*💪 Тренировка* — после неё",
            parse_mode="Markdown", reply_markup=main_kb())
        return ConversationHandler.END
    except: await update.message.reply_text("❌ Например: 10000"); return SETUP_SG

# ── Home ──────────────────────────────────────────────────────────
async def show_home(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    s=u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала /start"); return

    days=u["days"]; td=today_s()
    today_day=days.get(td,{})

    ws=sorted([(k,v["weight"]) for k,v in days.items() if v.get("weight")],reverse=True)
    cur_w=ws[0][1] if ws else s["startWeight"]
    lost=max(0,s["startWeight"]-cur_w)
    to_goal=max(0,cur_w-s["targetWeight"])
    dl=days_left()
    elapsed=(date.today()-START_DATE).days
    total=(GOAL_DATE-START_DATE).days
    pct=min(100,max(0,int(elapsed/total*100)))
    strk=streak(days)
    xp=u.get("xp",0); _,lvl=get_level(xp)

    # Today summary
    w_today  = f"⚖️ {today_day['weight']} кг" if today_day.get("weight") else "⚖️ вес не введён"
    s_today  = f"👟 {today_day['steps']:,}" if today_day.get("steps") is not None else "👟 шаги не введены"
    wo_today = "💪 тренировка: " + ("да ✅" if today_day.get("workout") else "нет ❌") if "workout" in today_day else "💪 тренировка не отмечена"
    sl_today = u["sleep"].get(td,{})
    sl_txt   = ""
    if sl_today.get("saved"):
        hrs=sleep_hrs(sl_today["start"],sl_today["end"])
        sc,lbl=sleep_score(hrs,sl_today.get("wakeups",0))
        sl_txt = f"\n🌙 Сон: {int(hrs)}ч {int((hrs%1)*60)}мин — {lbl}"

    # Forecast
    fc=""
    if len(ws)>=3:
        rate=(ws[-1][1]-ws[0][1])/max(len(ws)-1,1)
        if rate<0:
            dn=int(to_goal/abs(rate))
            fc=f"\n🔮 Цель через *{dn} дн.* {'✓' if dn<=dl else '— ускоряйся!'}"

    await update.message.reply_text(
        f"🏠 *{date.today().strftime('%d.%m.%Y')}* · до 27 мая: *{dl} дн.*\n"
        f"{pbar(elapsed,total,12)} {pct}%\n\n"
        f"⚖️ Вес: *{cur_w:.1f} кг* · −{lost:.1f} · до цели {to_goal:.1f} кг\n"
        f"🔥 Стрик: *{strk} дней*{fc}\n"
        f"{lvl} · {xp} XP\n\n"
        f"*Сегодня:*\n{w_today}\n{s_today}\n{wo_today}{sl_txt}",
        parse_mode="Markdown", reply_markup=main_kb())

# ── WEIGHT ────────────────────────────────────────────────────────
async def start_weight(update: Update, ctx):
    await update.message.reply_text("⚖️ *Вес*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("wd"))
    return W_DATE

async def weight_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("wd_",""); ctx.user_data["w_date"]=ds_val
    data=load(); u=get_user(data,update.effective_user.id)
    existing=u["days"].get(ds_val,{}).get("weight")
    sw=u["settings"].get("startWeight",80)
    base=existing if existing else sw
    weights=[round(base-0.5+i*0.1,1) for i in range(11)]
    rows=[]
    row=[]
    for w in weights:
        row.append(InlineKeyboardButton(f"{w}", callback_data=f"wv_{w}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести вручную", callback_data="wv_manual")])
    hint=f"\nТекущее: *{existing} кг*" if existing else ""
    await q.edit_message_text(f"⚖️ Вес за *{dlabel(ds_val)}*{hint}\n\nВыбери:", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return W_VAL

async def weight_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="wv_manual":
        await q.edit_message_text("⚖️ Введи вес (кг), например: `76.3`", parse_mode="Markdown")
        return W_VAL
    w=float(q.data.replace("wv_",""))
    return await _save_weight(q, ctx, w, is_query=True)

async def weight_val_text(update: Update, ctx):
    try:
        w=float(update.message.text.replace(",",".")); assert 30<w<300
        return await _save_weight(update, ctx, w, is_query=False)
    except: await update.message.reply_text("❌ Введи число, например: 76.3"); return W_VAL

async def _save_weight(obj, ctx, w, is_query):
    ds_val=ctx.user_data["w_date"]
    data=load(); u=get_user(data,update_effective_user_id(obj, is_query))
    day=get_day(u,ds_val)
    prev=day.get("weight")
    day["weight"]=w
    # XP
    u["xp"]=u.get("xp",0)+5
    check_achievements(u)
    save(data)
    diff_txt=""
    if prev: diff=(w-prev); diff_txt=f" ({diff:+.1f} кг {'↓' if diff<0 else '↑'})"
    text=f"✅ Вес *{w} кг* за *{dlabel(ds_val)}* сохранён!{diff_txt}\n+5 XP"
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown")
    else: await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
    return ConversationHandler.END

def update_effective_user_id(obj, is_query):
    if is_query: return obj.from_user.id
    return obj.effective_user.id

# ── STEPS ─────────────────────────────────────────────────────────
async def start_steps(update: Update, ctx):
    await update.message.reply_text("👟 *Шаги*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("sd"))
    return S_DATE

async def steps_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("sd_",""); ctx.user_data["s_date"]=ds_val
    presets=[0,2000,4000,5000,6000,7000,8000,9000,10000,12000,15000,20000]
    rows=[]; row=[]
    for s in presets:
        lbl=f"{s//1000}к" if s>=1000 else "0"
        row.append(InlineKeyboardButton(lbl, callback_data=f"sv_{s}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести точно", callback_data="sv_manual")])
    await q.edit_message_text(f"👟 Шаги за *{dlabel(ds_val)}*\n\nВыбери:", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return S_VAL

async def steps_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="sv_manual":
        await q.edit_message_text("👟 Введи количество шагов:", parse_mode="Markdown")
        return S_VAL
    s=int(q.data.replace("sv_",""))
    return await _save_steps(q, ctx, s, is_query=True)

async def steps_val_text(update: Update, ctx):
    try:
        s=int(update.message.text.replace(" ","").replace(",","")); assert 0<=s<=100000
        return await _save_steps(update, ctx, s, is_query=False)
    except: await update.message.reply_text("❌ Введи число шагов"); return S_VAL

async def _save_steps(obj, ctx, s, is_query):
    ds_val=ctx.user_data["s_date"]
    data=load(); u=get_user(data, update_effective_user_id(obj, is_query))
    day=get_day(u,ds_val)
    day["steps"]=s
    goal=u["settings"].get("stepsGoal",10000)
    xp_earn=10 if s>=goal else 3
    u["xp"]=u.get("xp",0)+xp_earn
    check_achievements(u); save(data)
    pb=pbar(s,goal,8)
    tag="✅ Норма!" if s>=goal else f"{int(s/goal*100)}%"
    text=f"✅ Шаги *{s:,}* за *{dlabel(ds_val)}*\n{pb} {tag}\n+{xp_earn} XP"
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown")
    else: await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
    return ConversationHandler.END

# ── WORKOUT ───────────────────────────────────────────────────────
async def start_workout(update: Update, ctx):
    await update.message.reply_text("💪 *Тренировка*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("wod"))
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
    txt="💪 Тренировка отмечена!" if wo else "😴 Без тренировки"
    xp_txt=f" +{xp_earn} XP 🔥" if xp_earn else ""
    await q.edit_message_text(f"✅ {txt} за *{dlabel(ds_val)}*{xp_txt}", parse_mode="Markdown")
    return ConversationHandler.END

# ── RATING ────────────────────────────────────────────────────────
async def start_rating(update: Update, ctx):
    await update.message.reply_text("⭐ *Оценка дня*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("rtd"))
    return RT_DATE

async def rating_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ds_val=q.data.replace("rtd_",""); ctx.user_data["rt_date"]=ds_val
    rows=[[InlineKeyboardButton(str(i), callback_data=f"rtv_{i}") for i in range(1,6)],
          [InlineKeyboardButton(str(i), callback_data=f"rtv_{i}") for i in range(6,11)]]
    await q.edit_message_text(f"⭐ Оценка за *{dlabel(ds_val)}* (1–10):", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
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
    xp_txt=f" +{xp_earn} XP" if xp_earn else ""
    await q.edit_message_text(f"✅ Оценка *{r}/10* за *{dlabel(ds_val)}*\n{stars}{xp_txt}", parse_mode="Markdown")
    return ConversationHandler.END

# ── SLEEP ─────────────────────────────────────────────────────────
async def start_sleep(update: Update, ctx):
    await update.message.reply_text("🌙 *Сон*\n\nЗа какой день?",
        parse_mode="Markdown", reply_markup=day_kb("sld"))
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
    await q.edit_message_text(f"🌙 Сон за *{dlabel(ds_val)}*\n\n😴 Во сколько лёг?", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows))
    return SL_START

async def sleep_start_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="slst_manual":
        await q.edit_message_text("😴 Введи время (ЧЧ:ММ), например: `23:45`", parse_mode="Markdown")
        return SL_START
    t=q.data.replace("slst_",""); ctx.user_data["sl_start"]=t
    await q.edit_message_text(f"😴 Лёг в *{t}*\n\n⏰ Во сколько проснулся?", parse_mode="Markdown",
        reply_markup=wake_kb())
    return SL_END

async def sleep_start_text(update: Update, ctx):
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        ctx.user_data["sl_start"]=t
        await update.message.reply_text(f"😴 Лёг в *{t}*\n\n⏰ Во сколько проснулся?",
            parse_mode="Markdown", reply_markup=wake_kb())
        return SL_END
    except: await update.message.reply_text("❌ Формат ЧЧ:ММ, например: 23:30"); return SL_START

def wake_kb():
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
        await q.edit_message_text("⏰ Введи время пробуждения (ЧЧ:ММ):", parse_mode="Markdown")
        return SL_END
    return await _proc_sleep_end(q, ctx, q.data.replace("slet_",""), True)

async def sleep_end_text(update: Update, ctx):
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        return await _proc_sleep_end(update, ctx, t, False)
    except: await update.message.reply_text("❌ Формат ЧЧ:ММ, например: 07:30"); return SL_END

async def _proc_sleep_end(obj, ctx, t, is_query):
    start=ctx.user_data["sl_start"]
    hrs=sleep_hrs(start,t)
    if hrs<1:
        msg="❌ Слишком мало. Проверь время."
        if is_query: await obj.edit_message_text(msg)
        else: await obj.message.reply_text(msg)
        return SL_END
    if hrs>18:
        msg="❌ Больше 18ч? Проверь время."
        if is_query: await obj.edit_message_text(msg)
        else: await obj.message.reply_text(msg)
        return SL_END
    ctx.user_data["sl_end"]=t
    hh=int(hrs); mm=int((hrs%1)*60)
    color="🟢" if 7<=hrs<=9 else "🟡" if hrs>=6 else "🔴"
    rows=[[InlineKeyboardButton("0 — без пробуждений", callback_data="slw_0")],
          [InlineKeyboardButton("1",callback_data="slw_1"),
           InlineKeyboardButton("2",callback_data="slw_2"),
           InlineKeyboardButton("3",callback_data="slw_3")],
          [InlineKeyboardButton("4",callback_data="slw_4"),
           InlineKeyboardButton("5+",callback_data="slw_5")]]
    text=f"⏰ Встал в *{t}* · *{hh}ч {mm}мин* {color}\n\n🌃 Сколько раз просыпался?"
    if is_query: await obj.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    else: await obj.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
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
    advice="💪 Идеально!" if sc>=85 else "👍 Хорошо" if sc>=70 else "⚠️ Недосып влияет на похудение!" if sc<50 else "👌 Неплохо"
    await q.edit_message_text(
        f"🌙 *Сон за {dlabel(ds_val)} сохранён!*\n\n"
        f"😴 {start} → ⏰ {end} · *{hh}ч {mm}мин*\n"
        f"🌃 {wt}\n📊 *{sc}/100 — {lbl}*\n{advice}"
        f"{f' +{xp_earn} XP' if xp_earn else ''}",
        parse_mode="Markdown")
    return ConversationHandler.END

# ── CALENDAR ──────────────────────────────────────────────────────
async def show_calendar(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days=u["days"]; sl=u["sleep"]
    today_d=date.today()

    lines=["📅 *Дневник — последние 14 дней*\n"]
    lines.append("─────────────────────")

    for i in range(13,-1,-1):
        d=today_d-timedelta(i)
        ds_val=ds(d)
        day=days.get(ds_val,{})
        sl_day=sl.get(ds_val,{})

        is_today=d==today_d
        is_future=d>today_d

        if is_future: continue

        # Date header
        weekdays=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
        dow=weekdays[d.weekday()]
        date_str=f"*{'📍 ' if is_today else ''}{dow} {d.strftime('%d.%m')}{'  ← сегодня' if is_today else ''}*"
        lines.append(date_str)

        # Weight
        if day.get("weight"):
            lines.append(f"  ⚖️ Вес: {day['weight']} кг")
        else:
            if d<=today_d: lines.append(f"  ⚖️ Вес: —")

        # Steps
        if day.get("steps") is not None:
            goal=u["settings"].get("stepsGoal",10000)
            ok="✅" if day["steps"]>=goal else "❌"
            lines.append(f"  👟 Шаги: {day['steps']:,} {ok}")
        else:
            if d<=today_d: lines.append(f"  👟 Шаги: —")

        # Workout
        if "workout" in day:
            lines.append(f"  💪 Тренировка: {'Да ✅' if day['workout'] else 'Нет'}")

        # Rating
        if day.get("rating"):
            stars="⭐"*day["rating"]
            lines.append(f"  {stars} ({day['rating']}/10)")

        # Sleep
        if sl_day.get("saved"):
            hrs=sleep_hrs(sl_day["start"],sl_day["end"])
            hh=int(hrs); mm=int((hrs%1)*60)
            _,lbl=sleep_score(hrs,sl_day.get("wakeups",0))
            lines.append(f"  🌙 Сон: {hh}ч {mm}мин — {lbl}")

        lines.append("─────────────────────")

    # Stats
    done=sum(1 for v in days.values() if v.get("weight"))
    strk=streak(days)
    lines.append(f"\n📊 Дней с весом: *{done}* · Стрик: *{strk}* 🔥 · До 27 мая: *{days_left()}*")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

# ── ANALYTICS ─────────────────────────────────────────────────────
async def show_analytics(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days=u["days"]; sl=u["sleep"]; s=u["settings"]
    today_d=date.today()

    ws=sorted([(k,v["weight"]) for k,v in days.items() if v.get("weight")])
    if ws:
        diff=ws[-1][1]-ws[0][1]
        w_txt=f"*{ws[-1][1]:.1f} кг* ({diff:+.1f} {'↓' if diff<0 else '↑'})"
    else: w_txt="нет данных"
    lost=max(0,s.get("startWeight",0)-(ws[-1][1] if ws else s.get("startWeight",0)))

    last14=[(today_d-timedelta(i)).isoformat() for i in range(13,-1,-1)]
    steps14=[days.get(d,{}).get("steps",0) for d in last14 if days.get(d,{}).get("steps") is not None]
    avg_s=int(sum(steps14)/len(steps14)) if steps14 else 0
    goal_s=s.get("stepsGoal",10000)
    goal_days=sum(1 for s2 in steps14 if s2>=goal_s)

    chart=""
    for d in last14[-7:]:
        s2=days.get(d,{}).get("steps",0) or 0
        p=min(1.0,s2/max(goal_s,1))
        chart+="█" if p>=1 else "▇" if p>=0.8 else "▅" if p>=0.5 else "▂" if p>0 else "░"

    elapsed=(today_d-START_DATE).days+1
    done=sum(1 for v in days.values() if v.get("weight"))
    disc=int(done/max(elapsed,1)*100)
    strk=streak(days)

    sl_entries=[v for v in sl.values() if v.get("saved")]
    if sl_entries:
        hrs_l=[sleep_hrs(e["start"],e["end"]) for e in sl_entries]
        avg_sl=sum(hrs_l)/len(hrs_l)
        avg_sc=int(sum(e.get("score",0) for e in sl_entries)/len(sl_entries))
        sl_txt=f"*{avg_sl:.1f}ч* · оценка *{avg_sc}/100*"
    else: sl_txt="нет данных"

    tw=[days.get((today_d-timedelta(i)).isoformat(),{}) for i in range(7)]
    lw=[days.get((today_d-timedelta(i+7)).isoformat(),{}) for i in range(7)]
    tw_s=[e.get("steps",0) for e in tw if e.get("steps")]
    lw_s=[e.get("steps",0) for e in lw if e.get("steps")]
    tw_a=int(sum(tw_s)/len(tw_s)) if tw_s else 0
    lw_a=int(sum(lw_s)/len(lw_s)) if lw_s else 0

    await update.message.reply_text(
        f"📈 *Аналитика*\n\n"
        f"⚖️ Вес: {w_txt}\n📉 Сброшено: *{lost:.1f} кг*\n\n"
        f"👟 Шаги (14 дн.) · среднее *{avg_s:,}* · норма *{goal_days}* дней\n"
        f"`{chart}` ← последние 7 дней\n\n"
        f"📊 Дисциплина *{disc}%* · Стрик *{strk}* 🔥\n"
        f"Дней с данными: *{done}* из {elapsed}\n\n"
        f"🌙 Сон: {sl_txt}\n\n"
        f"Эта неделя vs прошлая\n"
        f"Шаги: *{tw_a:,}* vs *{lw_a:,}* ({tw_a-lw_a:+,})\n"
        f"Чек-инов: *{sum(1 for e in tw if e.get('weight'))}* vs *{sum(1 for e in lw if e.get('weight'))}*",
        parse_mode="Markdown", reply_markup=main_kb())

# ── GAME ──────────────────────────────────────────────────────────
async def show_game(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    xp=u.get("xp",0); days=u["days"]; unlocked=u.get("achievements",[])
    lvls=[(0,"⚡ Новичок",100),(100,"🥊 Боец",250),(250,"🏃 Атлет",500),(500,"🏆 Чемпион",1000),(1000,"🤖 Машина",9999)]
    cur=lvls[0]; nxt=lvls[1]
    for i,(mn,nm,_) in enumerate(lvls):
        if xp>=mn: cur=lvls[i]; nxt=lvls[i+1] if i+1<len(lvls) else None
    pb=pbar(xp-cur[0],(nxt[0] if nxt else xp)-cur[0],10)
    nxt_txt=f"До {nxt[1]}: {nxt[0]-xp} XP" if nxt else "🏆 Максимальный уровень!"
    strk=streak(days)
    entries=list(days.values())
    wo_count=sum(1 for e in entries if e.get("workout"))
    step_strk=0
    for i in range(30):
        d2=ds(date.today()-timedelta(i))
        if days.get(d2,{}).get("steps",0)>=10000: step_strk+=1
        else: break
    ach_def=[("first_weight","⚖️","Первое взвешивание"),("streak7","🔥","7 дней подряд"),
             ("streak14","💪","14 дней"),("minus1","📉","-1 кг"),("minus3","🎉","-3 кг"),
             ("steps10k","👟","10к шагов"),("steps5days","🦵","5×10к"),
             ("workout7","🏋️","7 тренировок"),("good_sleep","🌙","Отличный сон")]
    ach_txt=""
    for aid,icon,nm in ach_def:
        ach_txt+=f"{'✅' if aid in unlocked else '🔒'} {icon} {nm}\n"
    await update.message.reply_text(
        f"🎮 *Игра*\n\n*{cur[1]}*\n{pb} *{xp} XP*\n{nxt_txt}\n\n"
        f"*Достижения:*\n{ach_txt}\n"
        f"*Челленджи:*\n"
        f"{'✅' if strk>=7 else f'{strk}/7'} 7 дней подряд\n"
        f"{'✅' if step_strk>=5 else f'{step_strk}/5'} 10к шагов × 5 дней\n"
        f"{'✅' if wo_count>=7 else f'{wo_count}/7'} 7 тренировок\n\n"
        f"*XP:* +5 вес · +10 шаги (норма) · +15 тренировка\n+5 оценка 8+ · +50 стрик 7 дней · +10 сон",
        parse_mode="Markdown", reply_markup=main_kb())

# ── SETTINGS ──────────────────────────────────────────────────────
async def show_settings(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); s=u["settings"]
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Изменить цели",callback_data="reset_setup")],
                              [InlineKeyboardButton("📤 Экспорт данных",callback_data="export_data")]])
    done=sum(1 for v in u["days"].values() if v.get("weight"))
    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n⚖️ Стартовый: *{s.get('startWeight','—')} кг*\n"
        f"🎯 Цель: *{s.get('targetWeight','—')} кг*\n👟 Шаги/день: *{s.get('stepsGoal',10000):,}*\n\n"
        f"Дней с данными: *{done}* · XP: *{u.get('xp',0)}*\n"
        f"💾 Данные хранятся на сервере",
        parse_mode="Markdown", reply_markup=kb)

async def settings_cb(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="export_data":
        data=load(); u=get_user(data,q.from_user.id)
        js=json.dumps(u,ensure_ascii=False,indent=2)
        await q.message.reply_document(document=js.encode("utf-8"),
            filename=f"fit27_{today_s()}.json", caption="📤 Твои данные")
    elif q.data=="reset_setup":
        await q.edit_message_text("Введи новый стартовый вес (кг):")
        return SETUP_SW

# ── Achievements ──────────────────────────────────────────────────
def check_achievements(u):
    ul=u.get("achievements",[]); days=u["days"]; s=u["settings"]
    def unlock(a):
        if a not in ul: ul.append(a)
    entries=list(days.values())
    if any(v.get("weight") for v in entries): unlock("first_weight")
    strk=streak(days)
    if strk>=7: unlock("streak7")
    if strk>=14: unlock("streak14")
    ws=sorted([(k,v["weight"]) for k,v in days.items() if v.get("weight")])
    if ws and s.get("startWeight"):
        lost=s["startWeight"]-ws[-1][1]
        if lost>=1: unlock("minus1")
        if lost>=3: unlock("minus3")
    if any(e.get("steps",0)>=10000 for e in entries): unlock("steps10k")
    cs=0
    for i in range(30):
        d2=ds(date.today()-timedelta(i))
        if days.get(d2,{}).get("steps",0)>=10000: cs+=1
        else: break
    if cs>=5: unlock("steps5days")
    if sum(1 for e in entries if e.get("workout"))>=7: unlock("workout7")
    if any(v.get("score",0)>=85 for v in u.get("sleep",{}).values() if v.get("saved")): unlock("good_sleep")
    u["achievements"]=ul

# ── Text router ───────────────────────────────────────────────────
async def handle_text(update: Update, ctx):
    t=update.message.text
    if any(x in t for x in ["Главная","🏠"]): await show_home(update,ctx)
    elif any(x in t for x in ["Календарь","📅"]): await show_calendar(update,ctx)
    elif any(x in t for x in ["Аналитика","📈"]): await show_analytics(update,ctx)
    elif any(x in t for x in ["Игра","🎮"]): await show_game(update,ctx)
    elif any(x in t for x in ["Настройки","⚙️"]): await show_settings(update,ctx)
    else: await update.message.reply_text("Выбери раздел 👇", reply_markup=main_kb())

# ── Main ──────────────────────────────────────────────────────────
def main():
    app=Application.builder().token(TOKEN).build()

    def make_conv(entry_points, states):
        return ConversationHandler(entry_points=entry_points, states=states,
            fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)])

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start",cmd_start)],
        states={SETUP_SW:[MessageHandler(filters.TEXT&~filters.COMMAND,setup_sw)],
                SETUP_TW:[MessageHandler(filters.TEXT&~filters.COMMAND,setup_tw)],
                SETUP_SG:[MessageHandler(filters.TEXT&~filters.COMMAND,setup_sg)]},
        fallbacks=[CommandHandler("start",cmd_start)]))

    app.add_handler(make_conv(
        [CommandHandler("weight",start_weight), MessageHandler(filters.Regex("Вес|⚖️"),start_weight)],
        {W_DATE:[CallbackQueryHandler(weight_date,pattern="^wd_")],
         W_VAL:[CallbackQueryHandler(weight_val_btn,pattern="^wv_"),
                MessageHandler(filters.TEXT&~filters.COMMAND,weight_val_text)]}))

    app.add_handler(make_conv(
        [CommandHandler("steps",start_steps), MessageHandler(filters.Regex("Шаги|👟"),start_steps)],
        {S_DATE:[CallbackQueryHandler(steps_date,pattern="^sd_")],
         S_VAL:[CallbackQueryHandler(steps_val_btn,pattern="^sv_"),
                MessageHandler(filters.TEXT&~filters.COMMAND,steps_val_text)]}))

    app.add_handler(make_conv(
        [CommandHandler("workout",start_workout), MessageHandler(filters.Regex("Тренировка|💪"),start_workout)],
        {WO_DATE:[CallbackQueryHandler(workout_date,pattern="^wod_")],
         WO_VAL:[CallbackQueryHandler(workout_val,pattern="^wov_")]}))

    app.add_handler(make_conv(
        [CommandHandler("rating",start_rating), MessageHandler(filters.Regex("Оценка|⭐"),start_rating)],
        {RT_DATE:[CallbackQueryHandler(rating_date,pattern="^rtd_")],
         RT_VAL:[CallbackQueryHandler(rating_val,pattern="^rtv_")]}))

    app.add_handler(make_conv(
        [CommandHandler("sleep",start_sleep), MessageHandler(filters.Regex("Сон|🌙"),start_sleep)],
        {SL_DATE:[CallbackQueryHandler(sleep_date,pattern="^sld_")],
         SL_START:[CallbackQueryHandler(sleep_start_btn,pattern="^slst_"),
                   MessageHandler(filters.TEXT&~filters.COMMAND,sleep_start_text)],
         SL_END:[CallbackQueryHandler(sleep_end_btn,pattern="^slet_"),
                 MessageHandler(filters.TEXT&~filters.COMMAND,sleep_end_text)],
         SL_WAKES:[CallbackQueryHandler(sleep_wakes,pattern="^slw_")]}))

    app.add_handler(CallbackQueryHandler(settings_cb,pattern="^(reset_setup|export_data)$"))
    app.add_handler(CommandHandler("home",show_home))
    app.add_handler(CommandHandler("calendar",show_calendar))
    app.add_handler(CommandHandler("analytics",show_analytics))
    app.add_handler(CommandHandler("game",show_game))
    app.add_handler(CommandHandler("settings",show_settings))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_text))

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
