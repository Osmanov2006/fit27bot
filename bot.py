import os, json, logging, asyncio, re
from zoneinfo import ZoneInfo
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, JobQueue)

logging.basicConfig(level=logging.INFO)
TOKEN    = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "data.json"
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")  # Set this in Railway Variables
TZ = ZoneInfo("Europe/Istanbul")

# ── States ────────────────────────────────────────────────────────
(S_GOAL,S_NAME,S_AGE,S_GENDER,S_HEIGHT,S_CW,S_TW,S_DATE,S_ACT,S_STEPS) = range(10)
W_DATE,W_VAL           = 20,21
ST_DATE,ST_VAL         = 30,31
WO_DATE,WO_VAL         = 40,41
SL_DATE,SL_ST,SL_EN,SL_WK = 50,51,52,53
RT_DATE,RT_VAL         = 60,61

# ── Storage ───────────────────────────────────────────────────────
def load():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except: pass
    return {}

def save(data):
    with open(DATA_FILE,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

def get_user(data,uid):
    uid=str(uid)
    if uid not in data:
        data[uid]={"settings":{},"days":{},"sleep":{},"xp":0,"achievements":[]}
    return data[uid]

def get_day(u,d):
    if d not in u["days"]: u["days"][d]={}
    return u["days"][d]

def tds(): return datetime.now(TZ).date().isoformat()
def dstr(d): return d.isoformat() if isinstance(d,date) else d

# ── Helpers ───────────────────────────────────────────────────────
async def del_msg(bot,chat_id,msg_id):
    try: await bot.delete_message(chat_id,msg_id)
    except: pass

async def del_prev(ctx,chat_id):
    mid=ctx.user_data.get("lm")
    if mid: await del_msg(ctx.bot,chat_id,mid)

async def reply(update,ctx,text,kb=None,pm="Markdown"):
    await del_prev(ctx,update.effective_chat.id)
    kw={"text":text,"parse_mode":pm}
    if kb: kw["reply_markup"]=kb
    else: kw["reply_markup"]=main_kb()
    msg=await update.message.reply_text(**kw)
    ctx.user_data["lm"]=msg.message_id
    return msg

def pbar(v,mx,n=8):
    f=int((v/max(mx,1))*n); return "▓"*f+"░"*(n-f)

def streak(days):
    d=date.today()
    if not days.get(dstr(d),{}).get("weight"): d-=timedelta(1)
    n=0
    for _ in range(365):
        if days.get(dstr(d),{}).get("weight"): n+=1; d-=timedelta(1)
        else: break
    return n

def sleep_hrs(s,e):
    sh,sm=map(int,s.split(":")); eh,em=map(int,e.split(":"))
    a=sh*60+sm; b=eh*60+em
    if b<=a: b+=1440
    return (b-a)/60

def sleep_score(hrs,w):
    d=50 if 7<=hrs<=9 else 40 if hrs>=6.5 else 28 if hrs>=6 else 15 if hrs>=5 else 5
    ws=[50,38,26,16,10,5,0][min(w,6)]; t=d+ws
    if t>=85: return t,"🌟 Отличный"
    if t>=70: return t,"😴 Хороший"
    if t>=50: return t,"😐 Средний"
    return t,"😮 Плохой"

def calc_kcal(s):
    age=s.get("age",25); h=s.get("height",175)
    w=s.get("currentWeight",s.get("startWeight",75))
    gender=s.get("gender","male"); act=s.get("activity",1.55)
    goal=s.get("goal","lose"); sw=s.get("startWeight",75); tw=s.get("targetWeight",70)
    diff_kg=abs(sw-tw)
    try: dl=max(14,(datetime.strptime(s["goalDate"],"%Y-%m-%d").date()-date.today()).days)
    except: dl=60
    if gender=="male": bmr=10*w+6.25*h-5*age+5
    else: bmr=10*w+6.25*h-5*age-161
    tdee=bmr*act
    if goal=="lose":
        deficit=min(900,max(300,int(diff_kg*7700/dl)))
        kcal=max(1400 if gender=="male" else 1200,int(tdee-deficit))
    elif goal=="gain":
        surplus=min(500,max(150,int(diff_kg*7700/dl)))
        kcal=int(tdee+surplus)
    else: kcal=int(tdee)
    return int(bmr),int(tdee),kcal

def dlabel(d_str):
    try:
        d=datetime.strptime(d_str,"%Y-%m-%d").date()
        if d==date.today(): return "сегодня"
        if d==date.today()-timedelta(1): return "вчера"
        return d.strftime("%d.%m.%Y")
    except: return d_str

def days_left(s):
    try: return max(0,(datetime.strptime(s["goalDate"],"%Y-%m-%d").date()-date.today()).days)
    except: return 0

def goal_name(g): return {"lose":"📉 Похудение","gain":"📈 Набор массы","maintain":"⚖️ Поддержание"}.get(g,"—")
def act_name(a): return {1.2:"🛋 Минимум",1.375:"🚶 Лёгкая",1.55:"🏃 Средняя",1.725:"💪 Высокая",1.9:"🏋️ Очень высокая"}.get(a,"—")

def weight_comment(diff, goal):
    """Motivational comment on weight change"""
    if diff is None: return ""
    if goal == "lose":
        if diff < -0.5: return "\n🎉 Отличный результат! Так держать!"
        if diff < 0:    return "\n✅ Идёшь в минус — всё правильно!"
        if diff == 0:   return "\n⚖️ Вес не изменился — норма, продолжай"
        if diff <= 0.3: return "\n😐 Небольшой плюс — не паника, бывает"
        return "\n⚠️ Вес вырос — проверь питание и воду"
    elif goal == "gain":
        if diff > 0.3:  return "\n🎉 Набираешь — отлично!"
        if diff > 0:    return "\n✅ Небольшой прирост — правильный путь"
        if diff == 0:   return "\n⚖️ Без изменений — добавь калорий"
        return "\n⚠️ Вес упал — добавь больше еды"
    return ""

def forecast_weight(days_d, s):
    """Forecast based on last 7 days trend"""
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    if len(ws)<3: return None,None
    recent=ws[-min(7,len(ws)):]
    if len(recent)<2: return None,None
    rate=(recent[-1][1]-recent[0][1])/max(len(recent)-1,1)  # kg/day
    if abs(rate)<0.001: return None,None
    target=s.get("targetWeight",recent[-1][1])
    cur=recent[-1][1]
    to_go=cur-target if s.get("goal","lose")=="lose" else target-cur
    if to_go<=0: return 0,rate
    days_needed=int(abs(to_go/rate))
    return days_needed,rate

def main_kb():
    return ReplyKeyboardMarkup([
        ["🏠 Главная",    "📊 Статус"],
        ["⚖️ Вес",        "👟 Шаги"],
        ["💪 Тренировка", "🌙 Сон"],
        ["⭐ Оценка дня", "📅 Календарь"],
        ["📈 Аналитика",  "⚙️ Настройки"],
        ["📱 Мини-апп"],
    ],resize_keyboard=True)

def day_kb(prefix):
    today_d=date.today()
    labels=["Сегодня","Вчера","2 дня назад","3 дня назад","4 дня назад"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{labels[i]} ({(today_d-timedelta(i)).strftime('%d.%m')})",
         callback_data=f"{prefix}_{dstr(today_d-timedelta(i))}")]
        for i in range(5)
    ])

def parse_float(text):
    """Robust float parsing: handles 76.3, 76,3, 76"""
    clean=text.strip().replace(",",".").replace(" ","")
    return float(clean)

def parse_steps(text):
    """Parse steps: handles 8500, 8к, 8.5к, 10000"""
    clean=text.strip().lower().replace(" ","").replace(",",".")
    if clean.endswith("к") or clean.endswith("k"):
        return int(float(clean[:-1])*1000)
    return int(float(clean))

# ── QUICK INPUT — detect number in free text ──────────────────────
async def try_quick_input(update: Update, ctx):
    """
    Smart quick input: just type a number → bot figures out what it is.
    76.5 → weight, 8500 / 8к → steps
    """
    t=update.message.text.strip()
    data=load(); u=get_user(data,update.effective_user.id)
    if not u["settings"].get("startWeight"): return False

    # Try weight (30–250, float, likely has decimal or is in weight range)
    try:
        val=parse_float(t)
        if 30<=val<=250 and ("." in t or "," in t or val==int(val)):
            # Looks like weight
            td=tds(); prev=u["days"].get(td,{}).get("weight")
            get_day(u,td)["weight"]=val
            u["settings"]["currentWeight"]=val
            u["xp"]=u.get("xp",0)+5
            _check_ach(u); save(data)
            s=u["settings"]
            to_go=abs(val-s.get("targetWeight",val))
            diff=round(val-prev,1) if prev else None
            comment=weight_comment(diff,s.get("goal","lose"))
            diff_txt=f" ({diff:+.1f} кг)" if diff is not None else ""
            await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
            msg=await update.message.reply_text(
                f"⚖️ *{val} кг* записан{diff_txt}{comment}\nДо цели: *{to_go:.1f} кг*",
                parse_mode="Markdown",reply_markup=main_kb())
            ctx.user_data["lm"]=msg.message_id
            return True
    except: pass

    # Try steps (large integer or с "к")
    try:
        lower=t.lower()
        if re.match(r'^\d+[\.,]?\d*[кk]?$',lower):
            val=parse_steps(t)
            if 0<=val<=60000:
                td=tds()
                get_day(u,td)["steps"]=val
                goal=u["settings"].get("stepsGoal",10000)
                xp=10 if val>=goal else 3
                u["xp"]=u.get("xp",0)+xp; _check_ach(u); save(data)
                pb=pbar(val,goal,8)
                tag="✅ Норма!" if val>=goal else f"{int(val/goal*100)}%"
                burned=int(val*0.04)
                await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
                msg=await update.message.reply_text(
                    f"👟 *{val:,} шагов* записано\n{pb} {tag} · ~{burned} ккал сожжено · +{xp} XP",
                    parse_mode="Markdown",reply_markup=main_kb())
                ctx.user_data["lm"]=msg.message_id
                return True
    except: pass

    return False

# ═══════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); save(data)
    if u["settings"].get("startWeight"):
        msg=await update.message.reply_text("👋 С возвращением!",reply_markup=main_kb())
        ctx.user_data["lm"]=msg.message_id; return ConversationHandler.END
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Похудеть",callback_data="g_lose")],
        [InlineKeyboardButton("📈 Набрать массу",callback_data="g_gain")],
        [InlineKeyboardButton("⚖️ Поддерживать форму",callback_data="g_maintain")],
    ])
    msg=await update.message.reply_text("🔥 *FIT TRACKER*\n\nВыбери цель:",parse_mode="Markdown",reply_markup=kb)
    ctx.user_data["lm"]=msg.message_id; return S_GOAL

async def s_goal(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ctx.user_data["goal"]=q.data.replace("g_","")
    await q.edit_message_text(f"*{goal_name(ctx.user_data['goal'])}* ✓\n\nКак тебя зовут?",parse_mode="Markdown")
    return S_NAME

async def s_name(update: Update, ctx):
    ctx.user_data["name"]=update.message.text.strip()[:20]
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    await del_prev(ctx,update.effective_chat.id)
    msg=await update.message.reply_text(f"Привет, *{ctx.user_data['name']}*! 👋\n\nСколько лет?",parse_mode="Markdown")
    ctx.user_data["lm"]=msg.message_id; return S_AGE

async def s_age(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        age=int(update.message.text.strip()); assert 10<=age<=100
        ctx.user_data["age"]=age
        await del_prev(ctx,update.effective_chat.id)
        kb=InlineKeyboardMarkup([[
            InlineKeyboardButton("👨 Мужской",callback_data="gen_male"),
            InlineKeyboardButton("👩 Женский",callback_data="gen_female")
        ]])
        msg=await update.message.reply_text(f"*{age} лет* ✓\n\nПол:",parse_mode="Markdown",reply_markup=kb)
        ctx.user_data["lm"]=msg.message_id; return S_GENDER
    except:
        msg=await update.message.reply_text("❌ Введи число, например: 25")
        ctx.user_data["lm"]=msg.message_id; return S_AGE

async def s_gender(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ctx.user_data["gender"]=q.data.replace("gen_","")
    g="👨 Мужской" if ctx.user_data["gender"]=="male" else "👩 Женский"
    await q.edit_message_text(f"*{g}* ✓\n\nРост (см), например: `178`",parse_mode="Markdown")
    return S_HEIGHT

async def s_height(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        h=int(update.message.text.strip()); assert 100<=h<=250
        ctx.user_data["height"]=h
        await del_prev(ctx,update.effective_chat.id)
        msg=await update.message.reply_text(f"📏 *{h} см* ✓\n\nТекущий вес (кг), например: `82.5`",parse_mode="Markdown")
        ctx.user_data["lm"]=msg.message_id; return S_CW
    except:
        msg=await update.message.reply_text("❌ Рост от 100 до 250, например: 178")
        ctx.user_data["lm"]=msg.message_id; return S_HEIGHT

async def s_cw(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        w=parse_float(update.message.text); assert 30<w<300
        ctx.user_data["cw"]=w
        await del_prev(ctx,update.effective_chat.id)
        goal=ctx.user_data.get("goal","lose")
        if goal=="maintain":
            ctx.user_data["tw"]=w; return await _ask_date(update,ctx)
        hint="меньше текущего" if goal=="lose" else "больше текущего"
        msg=await update.message.reply_text(f"⚖️ *{w} кг* ✓\n\nЦелевой вес ({hint}):",parse_mode="Markdown")
        ctx.user_data["lm"]=msg.message_id; return S_TW
    except:
        msg=await update.message.reply_text("❌ Введи число, например: 82.5")
        ctx.user_data["lm"]=msg.message_id; return S_CW

async def s_tw(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        w=parse_float(update.message.text); assert 30<w<300
        goal=ctx.user_data.get("goal","lose")
        if goal=="lose" and w>=ctx.user_data["cw"]: raise ValueError
        if goal=="gain" and w<=ctx.user_data["cw"]: raise ValueError
        ctx.user_data["tw"]=w
        await del_prev(ctx,update.effective_chat.id)
        return await _ask_date(update,ctx)
    except:
        hint="меньше текущего" if ctx.user_data.get("goal")=="lose" else "больше текущего"
        msg=await update.message.reply_text(f"❌ Цель должна быть {hint}. Попробуй:")
        ctx.user_data["lm"]=msg.message_id; return S_TW

async def _ask_date(update,ctx):
    today_d=date.today()
    presets=[
        (today_d+timedelta(weeks=4),"1 месяц"),
        (today_d+timedelta(weeks=8),"2 месяца"),
        (today_d+timedelta(weeks=12),"3 месяца"),
        (today_d+timedelta(weeks=24),"6 месяцев"),
        (date(2026,5,27),"27 мая 2026"),
        (date(2026,12,31),"Конец 2026"),
    ]
    rows=[[InlineKeyboardButton(f"{lbl} ({d2.strftime('%d.%m.%Y')})",callback_data=f"gd_{dstr(d2)}")] for d2,lbl in presets]
    rows.append([InlineKeyboardButton("✏️ Своя дата",callback_data="gd_manual")])
    msg=await update.message.reply_text("📅 *До какой даты цель?*",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    ctx.user_data["lm"]=msg.message_id; return S_DATE

async def s_date_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="gd_manual":
        await q.edit_message_text("📅 Введи дату: *ДД.ММ.ГГГГ*, например: `31.12.2026`",parse_mode="Markdown")
        return S_DATE
    ctx.user_data["goalDate"]=q.data.replace("gd_","")
    await q.edit_message_text("📅 Дата сохранена ✓\n\nУровень активности?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛋 Сижу дома",callback_data="act_1.2")],
            [InlineKeyboardButton("🚶 Лёгкая (1-2 раза/нед)",callback_data="act_1.375")],
            [InlineKeyboardButton("🏃 Средняя (3-5 раз/нед)",callback_data="act_1.55")],
            [InlineKeyboardButton("💪 Высокая (6-7 раз/нед)",callback_data="act_1.725")],
            [InlineKeyboardButton("🏋️ Очень высокая (2×день)",callback_data="act_1.9")],
        ]))
    return S_ACT

async def s_date_txt(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        d2=datetime.strptime(update.message.text.strip(),"%d.%m.%Y").date()
        assert d2>date.today()
        ctx.user_data["goalDate"]=dstr(d2)
        await del_prev(ctx,update.effective_chat.id)
        msg=await update.message.reply_text("📅 Дата сохранена ✓\n\nУровень активности?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛋 Сижу дома",callback_data="act_1.2")],
                [InlineKeyboardButton("🚶 Лёгкая (1-2/нед)",callback_data="act_1.375")],
                [InlineKeyboardButton("🏃 Средняя (3-5/нед)",callback_data="act_1.55")],
                [InlineKeyboardButton("💪 Высокая (6-7/нед)",callback_data="act_1.725")],
                [InlineKeyboardButton("🏋️ Очень высокая",callback_data="act_1.9")],
            ]))
        ctx.user_data["lm"]=msg.message_id; return S_ACT
    except:
        msg=await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ, например: 31.12.2026")
        ctx.user_data["lm"]=msg.message_id; return S_DATE

async def s_act(update: Update, ctx):
    q=update.callback_query; await q.answer()
    ctx.user_data["activity"]=float(q.data.replace("act_",""))
    await q.edit_message_text(f"*{act_name(ctx.user_data['activity'])}* ✓\n\nНорма шагов в день (например: `10000`):",parse_mode="Markdown")
    return S_STEPS

async def s_steps(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        steps=int(update.message.text.replace(" ","")); assert 500<=steps<=50000
        await del_prev(ctx,update.effective_chat.id)
        data=load(); u=get_user(data,update.effective_user.id)
        s={"name":ctx.user_data.get("name",""),"goal":ctx.user_data.get("goal","lose"),
           "age":ctx.user_data.get("age",25),"gender":ctx.user_data.get("gender","male"),
           "height":ctx.user_data.get("height",175),"startWeight":ctx.user_data.get("cw",80),
           "targetWeight":ctx.user_data.get("tw",70),"currentWeight":ctx.user_data.get("cw",80),
           "goalDate":ctx.user_data.get("goalDate",dstr(date.today()+timedelta(weeks=12))),
           "activity":ctx.user_data.get("activity",1.55),"stepsGoal":steps,"setupDate":tds()}
        u["settings"]=s; save(data)
        bmr,tdee,kcal=calc_kcal(s)
        goal_d=datetime.strptime(s["goalDate"],"%Y-%m-%d").date()
        dl=(goal_d-date.today()).days
        diff=abs(s["startWeight"]-s["targetWeight"])
        msg=await update.message.reply_text(
            f"🎯 *Готово, {s['name']}!*\n\n{goal_name(s['goal'])}\n"
            f"⚖️ {s['startWeight']} → {s['targetWeight']} кг ({diff:.1f} кг)\n"
            f"📅 До {goal_d.strftime('%d.%m.%Y')}: *{dl} дней*\n"
            f"🔥 Норма: *{kcal} ккал/день*\n"
            f"👟 Шаги: *{steps:,}/день*\n\n"
            f"💡 *Совет:* просто напиши вес (76.5) или шаги (8500) — бот сам поймёт!",
            parse_mode="Markdown",reply_markup=main_kb())
        ctx.user_data["lm"]=msg.message_id; return ConversationHandler.END
    except:
        msg=await update.message.reply_text("❌ Введи число шагов, например: 10000")
        ctx.user_data["lm"]=msg.message_id; return S_STEPS

# ═══════════════════════════════════════════════════════════════════
# HOME
# ═══════════════════════════════════════════════════════════════════
async def show_home(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); s=u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала /start"); return
    await del_prev(ctx,update.effective_chat.id)

    days_d=u["days"]; td=tds(); td_day=days_d.get(td,{})
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")],reverse=True)
    cur_w=ws[0][1] if ws else s["startWeight"]
    s["currentWeight"]=cur_w
    bmr,tdee,kcal=calc_kcal(s)
    goal=s.get("goal","lose")

    if goal=="gain":
        prog=f"📈 Набрано: +{max(0,cur_w-s['startWeight']):.1f} кг · осталось: {abs(cur_w-s['targetWeight']):.1f} кг"
    else:
        prog=f"📉 Сброшено: {max(0,s['startWeight']-cur_w):.1f} кг · осталось: {abs(cur_w-s['targetWeight']):.1f} кг"

    # Progress % toward goal
    total_diff=abs(s["startWeight"]-s["targetWeight"])
    done_diff=abs(s["startWeight"]-cur_w) if goal!="gain" else abs(cur_w-s["startWeight"])
    goal_pct=min(100,int(done_diff/max(total_diff,0.1)*100))

    try:
        sd=datetime.strptime(s.get("setupDate",tds()),"%Y-%m-%d").date()
        gd=datetime.strptime(s["goalDate"],"%Y-%m-%d").date()
        total_days=(gd-sd).days; elapsed=(date.today()-sd).days
        time_pct=min(100,int(elapsed/max(total_days,1)*100))
        pb_time=pbar(elapsed,total_days,10)
    except: time_pct=0; pb_time="░"*10

    strk=streak(days_d); xp=u.get("xp",0)

    # Forecast
    dn,rate=forecast_weight(days_d,s)
    fc_txt=""
    if dn is not None:
        dl=days_left(s)
        if dn==0: fc_txt="\n🎯 Цель достигнута!"
        elif dn<=dl: fc_txt=f"\n🔮 Прогноз: цель через *{dn} дн.* ✓ (темп {rate*7:.2f} кг/нед)"
        else: fc_txt=f"\n🔮 Прогноз: {dn} дн. → нужно ускориться на {rate*7:.2f} кг/нед"

    # Today quick status
    w_ok="✅" if td_day.get("weight") else "⬜"
    s_ok="✅" if td_day.get("steps") is not None else "⬜"
    wo_ok="✅" if td_day.get("workout") else "⬜"
    sl_ok="✅" if u["sleep"].get(td,{}).get("saved") else "⬜"

    msg=await update.message.reply_text(
        f"🏠 *{s.get('name','')} · {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}*\n\n"
        f"{goal_name(goal)} · осталось *{days_left(s)} дн.*\n"
        f"{pb_time} {time_pct}% времени\n"
        f"Цель: {pbar(goal_pct,100,10)} {goal_pct}%\n\n"
        f"⚖️ Вес: *{cur_w:.1f} кг*\n{prog}{fc_txt}\n\n"
        f"🔥 Стрик: *{strk} дн.* · {xp} XP · 🔥{kcal} ккал\n\n"
        f"*Сегодня:* {w_ok}вес  {s_ok}шаги  {wo_ok}трен  {sl_ok}сон\n\n"
        f"💡 Просто напиши *76.5* или *8500* — запишу автоматически",
        parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# STATUS — quick day summary
# ═══════════════════════════════════════════════════════════════════
async def show_status(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); s=u["settings"]
    if not s.get("startWeight"):
        await update.message.reply_text("Сначала /start"); return
    await del_prev(ctx,update.effective_chat.id)

    td=tds(); day=u["days"].get(td,{}); sl=u["sleep"].get(td,{})
    score=0; lines=[]

    # Weight
    if day.get("weight"):
        score+=1
        ws=sorted([(k,v["weight"]) for k,v in u["days"].items() if v.get("weight")])
        prev=None
        if len(ws)>=2: prev=ws[-2][1]
        diff=round(day["weight"]-prev,1) if prev else None
        comment=weight_comment(diff,s.get("goal","lose"))
        diff_txt=f" ({diff:+.1f} кг)" if diff else ""
        lines.append(f"✅ Вес: *{day['weight']} кг*{diff_txt}{comment}")
    else:
        lines.append("⬜ Вес: *не введён* → напиши число, например `76.5`")

    # Steps
    if day.get("steps") is not None:
        score+=1
        goal_s=s.get("stepsGoal",10000)
        pb=pbar(day["steps"],goal_s,8)
        tag="✅ Норма!" if day["steps"]>=goal_s else f"{int(day['steps']/goal_s*100)}%"
        lines.append(f"✅ Шаги: *{day['steps']:,}* {pb} {tag}")
    else:
        lines.append("⬜ Шаги: *не введены* → напиши число, например `8500`")

    # Workout
    if "workout" in day:
        score+=1
        lines.append(f"✅ Тренировка: *{'Да 💪' if day['workout'] else 'Нет'}*")
    else:
        lines.append("⬜ Тренировка: *не отмечена*")

    # Sleep
    if sl.get("saved"):
        score+=1
        hrs=sleep_hrs(sl["start"],sl["end"])
        sc,lbl=sleep_score(hrs,sl.get("wakeups",0))
        lines.append(f"✅ Сон: *{int(hrs)}ч {int((hrs%1)*60)}м* — {lbl} ({sc}/100)")
    else:
        lines.append("⬜ Сон: *не записан*")

    score_bar=["😴 Ничего","😐 Начало","👍 Хорошо","💪 Отлично","🔥 Идеальный день!"][score]

    msg=await update.message.reply_text(
        f"📊 *Статус — {date.today().strftime('%d.%m')}*\n"
        f"{'⭐'*score}{'☆'*(4-score)} {score}/4 — {score_bar}\n\n"
        +"\n".join(lines),
        parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# WEIGHT
# ═══════════════════════════════════════════════════════════════════
async def start_weight(update: Update, ctx):
    await del_prev(ctx,update.effective_chat.id)
    data=load(); u=get_user(data,update.effective_user.id)
    td=tds()

    # "Как вчера" button
    yesterday=dstr(date.today()-timedelta(1))
    prev_w=u["days"].get(yesterday,{}).get("weight")
    existing=u["days"].get(td,{}).get("weight")

    rows=[]
    if prev_w:
        rows.append([InlineKeyboardButton(f"📋 Как вчера ({prev_w} кг)",callback_data=f"wv_{prev_w}_today")])

    # Quick buttons around current/last weight
    base=existing if existing else (prev_w if prev_w else u["settings"].get("startWeight",80))
    weights=[round(base-0.4+i*0.1,1) for i in range(9)]
    row=[]; 
    for w in weights:
        row.append(InlineKeyboardButton(str(w),callback_data=f"wv_{w}_today"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("📅 За другой день",callback_data="wd_other")])
    rows.append([InlineKeyboardButton("✏️ Ввести другое число",callback_data="wv_m")])

    hint=f"\nСейчас: *{existing} кг*" if existing else ""
    msg=await update.message.reply_text(
        f"⚖️ *Вес{hint}*\n\nВыбери или нажми ✏️:",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    ctx.user_data["lm"]=msg.message_id; ctx.user_data["wd"]=td
    return W_VAL

async def w_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    d=q.data.replace("wd_",""); ctx.user_data["wd"]=d
    data=load(); u=get_user(data,q.from_user.id)
    existing=u["days"].get(d,{}).get("weight")
    base=existing if existing else u["settings"].get("startWeight",80)
    weights=[round(base-0.5+i*0.1,1) for i in range(11)]
    rows=[]; row=[]
    for w in weights:
        row.append(InlineKeyboardButton(str(w),callback_data=f"wv_{w}_{d}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести вручную",callback_data="wv_m")])
    hint=f"\nСейчас: *{existing} кг*" if existing else ""
    await q.edit_message_text(f"⚖️ Вес за *{dlabel(d)}*{hint}\n\nВыбери:",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    return W_VAL

async def w_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="wv_m":
        await q.edit_message_text("⚖️ Введи вес (кг):\n\nМожно писать: `76.5` или `76,5`",parse_mode="Markdown")
        return W_VAL
    if q.data=="wd_other":
        await q.edit_message_text("📅 За какой день?",reply_markup=day_kb("wd"))
        return W_DATE
    # Format: wv_{weight}_{date_or_today}
    parts=q.data.split("_",2)  # wv, weight, date
    w=float(parts[1])
    d=parts[2] if parts[2]!="today" else tds()
    ctx.user_data["wd"]=d
    return await _save_w(q,ctx,w,True)

async def w_val_txt(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        w=parse_float(update.message.text); assert 30<w<300
        return await _save_w(update,ctx,w,False)
    except:
        raw=update.message.text.strip()
        msg=await update.message.reply_text(
            f"❌ Не могу прочитать `{raw}`\n\nНапиши число: `76.5` или `76,5`",parse_mode="Markdown")
        ctx.user_data["lm"]=msg.message_id; return W_VAL

async def _save_w(obj,ctx,w,is_q):
    d=ctx.user_data.get("wd",tds()); uid=obj.from_user.id
    data=load(); u=get_user(data,uid)
    prev=u["days"].get(d,{}).get("weight")
    get_day(u,d)["weight"]=w
    u["settings"]["currentWeight"]=w
    u["xp"]=u.get("xp",0)+5
    _check_ach(u); save(data)
    s=u["settings"]
    to_go=abs(w-s.get("targetWeight",w))
    diff=round(w-prev,1) if prev else None
    comment=weight_comment(diff,s.get("goal","lose"))
    diff_txt=f" ({diff:+.1f} кг)" if diff else ""
    text=f"✅ *{w} кг* за *{dlabel(d)}* записан{diff_txt}{comment}\nДо цели: *{to_go:.1f} кг* · +5 XP"
    if is_q:
        await obj.edit_message_text(text,parse_mode="Markdown")
        msg=await obj.message.reply_text("Выбери:",reply_markup=main_kb())
    else:
        msg=await obj.message.reply_text(text,parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# STEPS
# ═══════════════════════════════════════════════════════════════════
async def start_steps(update: Update, ctx):
    await del_prev(ctx,update.effective_chat.id)
    data=load(); u=get_user(data,update.effective_user.id)
    td=tds(); existing=u["days"].get(td,{}).get("steps")
    yesterday=dstr(date.today()-timedelta(1))
    prev_s=u["days"].get(yesterday,{}).get("steps")

    presets=[0,3000,5000,6000,7000,8000,9000,10000,12000,15000,20000]
    rows=[]; row=[]
    if prev_s is not None:
        rows.append([InlineKeyboardButton(f"📋 Как вчера ({prev_s:,})",callback_data=f"sv_{prev_s}_today")])
    for s2 in presets:
        lbl=f"{s2//1000}к" if s2>=1000 else "0"
        row.append(InlineKeyboardButton(lbl,callback_data=f"sv_{s2}_today"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("📅 За другой день",callback_data="sd_other")])
    rows.append([InlineKeyboardButton("✏️ Ввести точно",callback_data="sv_m")])

    hint=f"\nСейчас: *{existing:,}*" if existing is not None else ""
    ctx.user_data["sd"]=td
    msg=await update.message.reply_text(
        f"👟 *Шаги{hint}*\n\nВыбери или ✏️:",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    ctx.user_data["lm"]=msg.message_id; return ST_VAL

async def st_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    d=q.data.replace("sd_",""); ctx.user_data["sd"]=d
    presets=[0,2000,4000,5000,6000,7000,8000,9000,10000,12000,15000,20000]
    rows=[]; row=[]
    for s2 in presets:
        row.append(InlineKeyboardButton(f"{s2//1000}к" if s2>=1000 else "0",callback_data=f"sv_{s2}_{d}"))
        if len(row)==4: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Ввести точно",callback_data="sv_m")])
    await q.edit_message_text(f"👟 Шаги за *{dlabel(d)}*\n\nВыбери:",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    return ST_VAL

async def st_val_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="sv_m":
        await q.edit_message_text("👟 Введи шаги (можно: `8500` или `8.5к` или `8к`):",parse_mode="Markdown")
        return ST_VAL
    if q.data=="sd_other":
        await q.edit_message_text("📅 За какой день?",reply_markup=day_kb("sd"))
        return ST_DATE
    parts=q.data.split("_",2)
    s2=int(parts[1]); d=parts[2] if parts[2]!="today" else tds()
    ctx.user_data["sd"]=d
    return await _save_st(q,ctx,s2,True)

async def st_val_txt(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        s2=parse_steps(update.message.text); assert 0<=s2<=100000
        return await _save_st(update,ctx,s2,False)
    except:
        msg=await update.message.reply_text("❌ Введи число шагов: `8500` или `8к`",parse_mode="Markdown")
        ctx.user_data["lm"]=msg.message_id; return ST_VAL

async def _save_st(obj,ctx,s2,is_q):
    d=ctx.user_data.get("sd",tds()); uid=obj.from_user.id
    data=load(); u=get_user(data,uid)
    get_day(u,d)["steps"]=s2
    goal=u["settings"].get("stepsGoal",10000)
    xp=10 if s2>=goal else 3
    u["xp"]=u.get("xp",0)+xp; _check_ach(u); save(data)
    pb=pbar(s2,goal,8); tag="✅ Норма!" if s2>=goal else f"{int(s2/goal*100)}%"
    burned=int(s2*0.04)
    text=f"✅ *{s2:,} шагов* за *{dlabel(d)}*\n{pb} {tag} · ~{burned} ккал · +{xp} XP"
    if is_q:
        await obj.edit_message_text(text,parse_mode="Markdown")
        msg=await obj.message.reply_text("Выбери:",reply_markup=main_kb())
    else:
        msg=await obj.message.reply_text(text,parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# WORKOUT
# ═══════════════════════════════════════════════════════════════════
async def start_workout(update: Update, ctx):
    await del_prev(ctx,update.effective_chat.id)
    msg=await update.message.reply_text("💪 *Тренировка*\n\nЗа какой день?",parse_mode="Markdown",reply_markup=day_kb("wod"))
    ctx.user_data["lm"]=msg.message_id; return WO_DATE

async def wo_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    d=q.data.replace("wod_",""); ctx.user_data["wod"]=d
    await q.edit_message_text(f"💪 Тренировка за *{dlabel(d)}*\n\nБыла?",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💪 Да",callback_data="wov_y"),
            InlineKeyboardButton("😴 Нет",callback_data="wov_n")]]))
    return WO_VAL

async def wo_val(update: Update, ctx):
    q=update.callback_query; await q.answer()
    wo=q.data=="wov_y"; d=ctx.user_data["wod"]
    data=load(); u=get_user(data,q.from_user.id)
    get_day(u,d)["workout"]=wo
    if wo: u["xp"]=u.get("xp",0)+15
    _check_ach(u); save(data)
    txt="💪 Тренировка!" if wo else "😴 Без тренировки"
    xp_txt=" +15 XP" if wo else ""
    await q.edit_message_text(f"✅ *{txt}* за *{dlabel(d)}*{xp_txt}",parse_mode="Markdown")
    msg=await q.message.reply_text("Выбери:",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# SLEEP
# ═══════════════════════════════════════════════════════════════════
async def start_sleep(update: Update, ctx):
    await del_prev(ctx,update.effective_chat.id)
    msg=await update.message.reply_text("🌙 *Сон*\n\nЗа какой день?",parse_mode="Markdown",reply_markup=day_kb("sld"))
    ctx.user_data["lm"]=msg.message_id; return SL_DATE

async def sl_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    d=q.data.replace("sld_",""); ctx.user_data["sld"]=d
    presets=["21:00","22:00","22:30","23:00","23:30","00:00","00:30","01:00","02:00"]
    rows=[]; row=[]
    for t in presets:
        row.append(InlineKeyboardButton(t,callback_data=f"slst_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое",callback_data="slst_m")])
    await q.edit_message_text(f"🌙 Сон за *{dlabel(d)}*\n\n😴 Лёг в:",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    return SL_ST

async def sl_st_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="slst_m":
        await q.edit_message_text("😴 Введи время (ЧЧ:ММ):",parse_mode="Markdown"); return SL_ST
    t=q.data.replace("slst_",""); ctx.user_data["slst"]=t
    await q.edit_message_text(f"😴 Лёг в *{t}*\n\n⏰ Проснулся в:",parse_mode="Markdown",reply_markup=_wake_kb())
    return SL_EN

async def sl_st_txt(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        ctx.user_data["slst"]=t
        await del_prev(ctx,update.effective_chat.id)
        msg=await update.message.reply_text(f"😴 Лёг в *{t}*\n\n⏰ Проснулся в:",parse_mode="Markdown",reply_markup=_wake_kb())
        ctx.user_data["lm"]=msg.message_id; return SL_EN
    except:
        msg=await update.message.reply_text("❌ Формат ЧЧ:ММ, например: 23:30")
        ctx.user_data["lm"]=msg.message_id; return SL_ST

def _wake_kb():
    presets=["05:00","05:30","06:00","06:30","07:00","07:30","08:00","08:30","09:00","10:00"]
    rows=[]; row=[]
    for t in presets:
        row.append(InlineKeyboardButton(t,callback_data=f"slen_{t}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другое",callback_data="slen_m")])
    return InlineKeyboardMarkup(rows)

async def sl_en_btn(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="slen_m":
        await q.edit_message_text("⏰ Введи время пробуждения (ЧЧ:ММ):",parse_mode="Markdown"); return SL_EN
    return await _proc_sleep_end(q,ctx,q.data.replace("slen_",""),True)

async def sl_en_txt(update: Update, ctx):
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)
    try:
        t=update.message.text.strip(); datetime.strptime(t,"%H:%M")
        return await _proc_sleep_end(update,ctx,t,False)
    except:
        msg=await update.message.reply_text("❌ Формат ЧЧ:ММ, например: 07:30")
        ctx.user_data["lm"]=msg.message_id; return SL_EN

async def _proc_sleep_end(obj,ctx,t,is_q):
    start=ctx.user_data["slst"]; hrs=sleep_hrs(start,t)
    if hrs<1 or hrs>18:
        err="❌ Слишком мало." if hrs<1 else "❌ Больше 18ч? Проверь."
        if is_q: await obj.edit_message_text(err)
        else:
            msg=await obj.message.reply_text(err); ctx.user_data["lm"]=msg.message_id
        return SL_EN
    ctx.user_data["slen"]=t
    hh=int(hrs); mm=int((hrs%1)*60)
    color="🟢" if 7<=hrs<=9 else "🟡" if hrs>=6 else "🔴"
    rows=[[InlineKeyboardButton("0 — не просыпался 🌟",callback_data="slw_0")],
          [InlineKeyboardButton("1",callback_data="slw_1"),InlineKeyboardButton("2",callback_data="slw_2"),InlineKeyboardButton("3",callback_data="slw_3")],
          [InlineKeyboardButton("4",callback_data="slw_4"),InlineKeyboardButton("5+",callback_data="slw_5")]]
    text=f"⏰ Встал в *{t}* · *{hh}ч {mm}мин* {color}\n\n🌃 Сколько раз просыпался?"
    if is_q: await obj.edit_message_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    else:
        msg=await obj.message.reply_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
        ctx.user_data["lm"]=msg.message_id
    return SL_WK

async def sl_wk(update: Update, ctx):
    q=update.callback_query; await q.answer()
    w=int(q.data.replace("slw_","")); d=ctx.user_data["sld"]
    start=ctx.user_data["slst"]; end=ctx.user_data["slen"]
    hrs=sleep_hrs(start,end); sc,lbl=sleep_score(hrs,w)
    hh=int(hrs); mm=int((hrs%1)*60)
    data=load(); u=get_user(data,q.from_user.id)
    u["sleep"][d]={"start":start,"end":end,"wakeups":w,"score":sc,"saved":True,"ts":datetime.now().isoformat()}
    xp=0
    if 7<=hrs<=9: xp+=10
    if w==0: xp+=5
    if xp: u["xp"]=u.get("xp",0)+xp
    _check_ach(u); save(data)
    wt=["Без пробуждений 🌟","1 раз","2 раза","3 раза","4 раза","5+ раз"][min(w,5)]
    advice="💪 Идеальный сон!" if sc>=85 else "👍 Хорошо" if sc>=70 else "⚠️ Недосып влияет на прогресс" if sc<50 else "👌 Нормально"
    await q.edit_message_text(
        f"🌙 *Сон за {dlabel(d)}*\n\n😴 {start} → ⏰ {end} · *{hh}ч {mm}мин*\n"
        f"{wt} · *{sc}/100 {lbl}*\n{advice}"+(f"\n+{xp} XP" if xp else ""),
        parse_mode="Markdown")
    msg=await q.message.reply_text("Выбери:",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# RATING
# ═══════════════════════════════════════════════════════════════════
async def start_rating(update: Update, ctx):
    await del_prev(ctx,update.effective_chat.id)
    msg=await update.message.reply_text("⭐ *Оценка дня*\n\nЗа какой день?",parse_mode="Markdown",reply_markup=day_kb("rtd"))
    ctx.user_data["lm"]=msg.message_id; return RT_DATE

async def rt_date(update: Update, ctx):
    q=update.callback_query; await q.answer()
    d=q.data.replace("rtd_",""); ctx.user_data["rtd"]=d
    rows=[[InlineKeyboardButton(str(i),callback_data=f"rtv_{i}_{d}") for i in range(1,6)],
          [InlineKeyboardButton(str(i),callback_data=f"rtv_{i}_{d}") for i in range(6,11)]]
    await q.edit_message_text(f"⭐ Оценка за *{dlabel(d)}* (1–10):",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(rows))
    return RT_VAL

async def rt_val(update: Update, ctx):
    q=update.callback_query; await q.answer()
    parts=q.data.split("_",2); r=int(parts[1]); d=parts[2]
    data=load(); u=get_user(data,q.from_user.id)
    get_day(u,d)["rating"]=r
    xp=5 if r>=8 else 0
    if xp: u["xp"]=u.get("xp",0)+xp
    _check_ach(u); save(data)
    stars="⭐"*r
    await q.edit_message_text(f"✅ *{stars}* ({r}/10) за *{dlabel(d)}*"+(f" +{xp} XP" if xp else ""),parse_mode="Markdown")
    msg=await q.message.reply_text("Выбери:",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# CALENDAR
# ═══════════════════════════════════════════════════════════════════
async def show_calendar(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days_d=u["days"]; sl=u["sleep"]; s=u["settings"]
    today_d=date.today()
    await del_prev(ctx,update.effective_chat.id)

    wdays=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    try:
        sd=datetime.strptime(s.get("setupDate",tds()),"%Y-%m-%d").date()
        gd=datetime.strptime(s["goalDate"],"%Y-%m-%d").date()
        total_d=(gd-sd).days; sw=s.get("startWeight",0); tw=s.get("targetWeight",0)
        total_diff=tw-sw
    except: sd=today_d; total_d=60; total_diff=0

    lines=["📅 *Последние 7 дней*\n━━━━━━━━━━━━━━━"]

    for i in range(6,-1,-1):
        d=today_d-timedelta(i); ds_val=dstr(d)
        day=days_d.get(ds_val,{}); sl_d=sl.get(ds_val,{})
        dow=wdays[d.weekday()]; is_today=d==today_d
        mark="📍" if is_today else "  "
        el=max((d-sd).days,0)
        exp_w=round(sw+total_diff*el/max(total_d,1),1) if total_d>0 else None

        lines.append(f"\n{mark}*{dow} {d.strftime('%d.%m')}*{'  ← сегодня' if is_today else ''}")

        aw=day.get("weight")
        if aw and exp_w:
            dif=round(aw-exp_w,1); gm=s.get("goal","lose")
            if gm=="lose": st="✅" if dif<-0.2 else ("⚠️" if dif>0.3 else "👍")
            else: st="✅" if dif>0.2 else ("⚠️" if dif<-0.3 else "👍")
            lines.append(f"  ⚖️ {aw} кг (план {exp_w}) {st}")
        elif aw: lines.append(f"  ⚖️ {aw} кг")
        elif d<=today_d and exp_w: lines.append(f"  ⚖️ — (план {exp_w} кг)")

        if day.get("steps") is not None:
            gs=s.get("stepsGoal",10000)
            lines.append(f"  👟 {day['steps']:,} {'✅' if day['steps']>=gs else '❌'}")
        if "workout" in day:
            lines.append(f"  💪 {'✅' if day['workout'] else 'нет'}")
        if day.get("rating"): lines.append(f"  ⭐ {day['rating']}/10")
        if sl_d.get("saved"):
            hrs=sleep_hrs(sl_d["start"],sl_d["end"])
            _,lbl=sleep_score(hrs,sl_d.get("wakeups",0))
            lines.append(f"  🌙 {int(hrs)}ч {int((hrs%1)*60)}м {lbl}")
        if not any([aw,day.get("steps") is not None,"workout" in day,sl_d.get("saved")]):
            lines.append("  · нет данных")
        lines.append("  ──────────────")

    # Mini calendar to deadline
    try:
        dl=(gd-today_d).days
        lines.append(f"\n📆 *До цели: {dl} дн.* (до {gd.strftime('%d.%m.%Y')})\n`Пн Вт Ср Чт Пт Сб Вс`")
        cur=today_d-timedelta(today_d.weekday())
        end=min(gd,today_d+timedelta(weeks=8))
        row_c=[]
        while cur<=end:
            ds_c=dstr(cur)
            if cur<today_d:
                if cur<sd: cell="  "
                elif days_d.get(ds_c,{}).get("weight"): cell="✅"
                else: cell="❌"
            elif cur==today_d: cell="📍"
            elif cur==gd: cell="🎯"
            else: cell="⬜"
            row_c.append(cell)
            if len(row_c)==7: lines.append("`"+" ".join(row_c)+"`"); row_c=[]
            cur+=timedelta(1)
        if row_c:
            while len(row_c)<7: row_c.append("  ")
            lines.append("`"+" ".join(row_c)+"`")
        lines.append("\n✅ вес есть · ❌ нет данных · 📍 сегодня · 🎯 цель")
    except: pass

    strk=streak(days_d)
    lines.append(f"\n🔥 Стрик: *{strk}* дней")

    text="\n".join(lines)
    if len(text)>4000:
        mid=len(lines)//2
        await update.message.reply_text("\n".join(lines[:mid]),parse_mode="Markdown")
        msg=await update.message.reply_text("\n".join(lines[mid:]),parse_mode="Markdown",reply_markup=main_kb())
    else:
        msg=await update.message.reply_text(text,parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════
async def show_analytics(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    days_d=u["days"]; sl=u["sleep"]; s=u["settings"]
    today_d=date.today()
    await del_prev(ctx,update.effective_chat.id)

    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    if ws:
        dif=ws[-1][1]-ws[0][1]
        w_txt=f"*{ws[-1][1]:.1f} кг* ({dif:+.1f} {'📉' if dif<0 else '📈'})"
    else: w_txt="нет данных"

    gm=s.get("goal","lose")
    sw=s.get("startWeight",0); cur=ws[-1][1] if ws else sw
    if gm=="gain": prog=f"📈 Набрано: +{max(0,cur-sw):.1f} кг"
    else: prog=f"📉 Сброшено: {max(0,sw-cur):.1f} кг"

    # Best day of week
    dow_steps={}
    for k,v in days_d.items():
        if v.get("steps"):
            try:
                dow=datetime.strptime(k,"%Y-%m-%d").weekday()
                dow_steps.setdefault(dow,[]).append(v["steps"])
            except: pass
    best_dow=""
    if dow_steps:
        best=max(dow_steps,key=lambda d:sum(dow_steps[d])/len(dow_steps[d]))
        dnames=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
        best_dow=f"\n🏆 Лучший день: *{dnames[best]}* (ср. {int(sum(dow_steps[best])/len(dow_steps[best])):,} шагов)"

    # Steps 14 days
    last14=[(today_d-timedelta(i)).isoformat() for i in range(13,-1,-1)]
    steps14=[days_d.get(d,{}).get("steps",0) for d in last14 if days_d.get(d,{}).get("steps") is not None]
    avg_s=int(sum(steps14)/len(steps14)) if steps14 else 0
    goal_s=s.get("stepsGoal",10000); goal_days=sum(1 for x in steps14 if x>=goal_s)
    chart="".join(["█" if (days_d.get(d,{}).get("steps") or 0)>=goal_s
        else "▇" if (days_d.get(d,{}).get("steps") or 0)>=goal_s*0.8
        else "▅" if (days_d.get(d,{}).get("steps") or 0)>=goal_s*0.5
        else "▂" if (days_d.get(d,{}).get("steps") or 0)>0 else "░" for d in last14[-7:]])

    try: sd=datetime.strptime(s.get("setupDate",tds()),"%Y-%m-%d").date()
    except: sd=today_d
    elapsed=(today_d-sd).days+1
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

    # Forecast
    dn,rate=forecast_weight(days_d,s)
    fc_txt=""
    if dn is not None:
        dl=days_left(s)
        if dn==0: fc_txt="\n🎯 Цель уже достигнута!"
        elif dn<=dl: fc_txt=f"\n🔮 Прогноз (по 7 дням): цель через *{dn} дн.* ✓"
        else: fc_txt=f"\n🔮 Прогноз: {dn} дн. — нужно ускориться!"

    tw=[days_d.get((today_d-timedelta(i)).isoformat(),{}) for i in range(7)]
    lw=[days_d.get((today_d-timedelta(i+7)).isoformat(),{}) for i in range(7)]
    tw_s=[e.get("steps",0) for e in tw if e.get("steps")]; lw_s=[e.get("steps",0) for e in lw if e.get("steps")]
    tw_a=int(sum(tw_s)/len(tw_s)) if tw_s else 0; lw_a=int(sum(lw_s)/len(lw_s)) if lw_s else 0

    bmr,tdee,kcal=calc_kcal(s)

    msg=await update.message.reply_text(
        f"📈 *Аналитика · {goal_name(gm)}*\n\n"
        f"⚖️ Вес: {w_txt}\n{prog}{fc_txt}\n\n"
        f"🔥 Норма: *{kcal} ккал/день*\n\n"
        f"👟 Шаги (14 дн.) · среднее *{avg_s:,}* · норма *{goal_days}* дней\n"
        f"`{chart}` ← 7 дней{best_dow}\n\n"
        f"📊 Дисциплина *{disc}%* · Стрик *{strk}* 🔥\n\n"
        f"🌙 Сон: {sl_txt}\n\n"
        f"Эта неделя vs прошлая\n"
        f"Шаги: *{tw_a:,}* vs *{lw_a:,}* ({tw_a-lw_a:+,})\n"
        f"Дней с данными: *{sum(1 for e in tw if e.get('weight'))}* vs *{sum(1 for e in lw if e.get('weight'))}*",
        parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════════════
ACHIEVEMENTS=[
    ("first_weight","⚖️","Первое взвешивание","Записал вес впервые"),
    ("streak7","🔥","7 дней подряд","7 дней с весом"),
    ("streak14","💪","14 дней подряд","14 дней без пропуска"),
    ("streak30","🏆","30 дней подряд","Месяц дисциплины!"),
    ("1kg","📉","−1 кг","Первый килограмм сброшен"),
    ("3kg","🎉","−3 кг","Серьёзный прогресс"),
    ("5kg","🥇","−5 кг","Огонь! 5 кг долой"),
    ("steps10k","👟","10 000 шагов","Норма за день"),
    ("steps7d","🦵","7 дней нормы шагов","Неделя активности"),
    ("workout7","🏋️","7 тренировок","Неделя тренировок"),
    ("perfect_day","⭐","Идеальный день","Вес+шаги+трен+сон за 1 день"),
    ("good_sleep","🌙","Отличный сон","Оценка сна 85+"),
]

def _check_ach(u):
    ul=u.get("achievements",[]); days_d=u["days"]; s=u["settings"]
    def unlock(a):
        if a not in ul: ul.append(a)
    entries=list(days_d.values())
    if any(v.get("weight") for v in entries): unlock("first_weight")
    strk=streak(days_d)
    if strk>=7: unlock("streak7")
    if strk>=14: unlock("streak14")
    if strk>=30: unlock("streak30")
    ws=sorted([(k,v["weight"]) for k,v in days_d.items() if v.get("weight")])
    if ws and s.get("startWeight"):
        diff=abs(s["startWeight"]-ws[-1][1])
        if diff>=1: unlock("1kg")
        if diff>=3: unlock("3kg")
        if diff>=5: unlock("5kg")
    if any(e.get("steps",0)>=10000 for e in entries): unlock("steps10k")
    # 7 days steps goal
    goal_s=s.get("stepsGoal",10000)
    days_with_goal=sum(1 for e in entries if e.get("steps",0)>=goal_s)
    if days_with_goal>=7: unlock("steps7d")
    if sum(1 for e in entries if e.get("workout"))>=7: unlock("workout7")
    # Perfect day
    for ds_val,day in days_d.items():
        sl_d=u.get("sleep",{}).get(ds_val,{})
        if day.get("weight") and day.get("steps",0)>=goal_s and day.get("workout") and sl_d.get("saved"):
            unlock("perfect_day"); break
    if any(v.get("score",0)>=85 for v in u.get("sleep",{}).values() if v.get("saved")): unlock("good_sleep")
    u["achievements"]=ul

async def show_achievements(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id)
    ul=u.get("achievements",[]); xp=u.get("xp",0)
    await del_prev(ctx,update.effective_chat.id)
    lines=[f"🏅 *Достижения* · {xp} XP\n"]
    unlocked_count=0
    for aid,icon,name,desc in ACHIEVEMENTS:
        if aid in ul:
            lines.append(f"✅ {icon} *{name}* — {desc}")
            unlocked_count+=1
        else:
            lines.append(f"🔒 {icon} {name} — {desc}")
    lines.append(f"\n*{unlocked_count}/{len(ACHIEVEMENTS)}* разблокировано")
    msg=await update.message.reply_text("\n".join(lines),parse_mode="Markdown",reply_markup=main_kb())
    ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════
async def show_settings(update: Update, ctx):
    data=load(); u=get_user(data,update.effective_user.id); s=u["settings"]
    await del_prev(ctx,update.effective_chat.id)
    bmr,tdee,kcal=calc_kcal(s)
    done=sum(1 for v in u["days"].values() if v.get("weight"))
    try: gd=datetime.strptime(s["goalDate"],"%Y-%m-%d").strftime("%d.%m.%Y")
    except: gd="—"
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Изменить цели",callback_data="cfg_reset")],
        [InlineKeyboardButton("📤 Экспорт JSON",callback_data="cfg_export")],
        [InlineKeyboardButton("📊 Экспорт CSV",callback_data="cfg_csv")],
    ])
    msg=await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"👤 {s.get('name','—')} · {'👨' if s.get('gender')=='male' else '👩'} · {s.get('age','—')} лет\n"
        f"📏 {s.get('height','—')} см · ⚖️ {s.get('startWeight','—')} → {s.get('targetWeight','—')} кг\n"
        f"🎯 {goal_name(s.get('goal','lose'))} до *{gd}* · {days_left(s)} дн.\n"
        f"🏃 {act_name(s.get('activity',1.55))}\n"
        f"👟 Шаги: *{s.get('stepsGoal',10000):,}/день*\n\n"
        f"🔥 Норма: *{kcal} ккал* (BMR {bmr} · TDEE {tdee})\n\n"
        f"Дней с данными: *{done}* · XP: *{u.get('xp',0)}*",
        parse_mode="Markdown",reply_markup=kb)
    ctx.user_data["lm"]=msg.message_id

async def settings_cb(update: Update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cfg_export":
        data=load(); u=get_user(data,q.from_user.id)
        js=json.dumps(u,ensure_ascii=False,indent=2)
        await q.message.reply_document(document=js.encode("utf-8"),
            filename=f"fittracker_{tds()}.json",caption="📤 Данные в JSON")
    elif q.data=="cfg_csv":
        data=load(); u=get_user(data,q.from_user.id)
        lines=["date,weight,steps,workout,rating,sleep_hrs,sleep_score"]
        for d in sorted(u["days"]):
            day=u["days"][d]; sl=u["sleep"].get(d,{})
            w=day.get("weight",""); st=day.get("steps","")
            wo="1" if day.get("workout") else ("0" if "workout" in day else "")
            rt=day.get("rating","")
            if sl.get("saved"):
                hrs=round(sleep_hrs(sl["start"],sl["end"]),1); sc=sl.get("score","")
            else: hrs=""; sc=""
            lines.append(f"{d},{w},{st},{wo},{rt},{hrs},{sc}")
        csv="\n".join(lines)
        await q.message.reply_document(document=csv.encode("utf-8"),
            filename=f"fittracker_{tds()}.csv",caption="📊 Данные в CSV (Excel)")
    elif q.data=="cfg_reset":
        data=load(); u=get_user(data,q.from_user.id)
        u["settings"]={}; save(data)
        await q.edit_message_text("Настройки сброшены. Напиши /start")

# ═══════════════════════════════════════════════════════════════════
# REMINDERS (daily jobs)
# ═══════════════════════════════════════════════════════════════════
async def morning_reminder(ctx):
    """8:00 — remind to log weight"""
    data=load()
    for uid,u in data.items():
        if not u.get("settings",{}).get("startWeight"): continue
        td=tds()
        if not u["days"].get(td,{}).get("weight"):
            try:
                await ctx.bot.send_message(
                    chat_id=int(uid),
                    text="☀️ *Доброе утро!*\n\nНе забудь взвеситься — напиши вес прямо сюда, например `76.5`",
                    parse_mode="Markdown",reply_markup=main_kb())
            except: pass

async def evening_reminder(ctx):
    """21:00 — remind to log steps"""
    data=load()
    for uid,u in data.items():
        if not u.get("settings",{}).get("startWeight"): continue
        td=tds(); day=u["days"].get(td,{})
        missing=[]
        if day.get("steps") is None: missing.append("👟 шаги")
        if "workout" not in day: missing.append("💪 тренировка")
        if not u["sleep"].get(td,{}).get("saved"): missing.append("🌙 сон")
        if missing:
            try:
                await ctx.bot.send_message(
                    chat_id=int(uid),
                    text=f"🌙 *Вечерняя проверка*\n\nЕщё не заполнено: {', '.join(missing)}\n\nШаги можно написать прямо сюда — например `8500`",
                    parse_mode="Markdown",reply_markup=main_kb())
            except: pass

# ═══════════════════════════════════════════════════════════════════
# TEXT ROUTER
# ═══════════════════════════════════════════════════════════════════
async def handle_text(update: Update, ctx):
    t=update.message.text.strip()
    await del_msg(ctx.bot,update.effective_chat.id,update.message.message_id)

    # Try quick input first (number = weight/steps)
    if await try_quick_input(update,ctx): return

    if any(x in t for x in ["🏠","Главная"]):        await show_home(update,ctx)
    elif any(x in t for x in ["📊","Статус"]):        await show_status(update,ctx)
    elif any(x in t for x in ["📅","Календарь"]):     await show_calendar(update,ctx)
    elif any(x in t for x in ["📈","Аналитика"]):     await show_analytics(update,ctx)
    elif any(x in t for x in ["⚙️","Настройки"]):    await show_settings(update,ctx)
    else:
        msg=await update.message.reply_text(
            "Выбери раздел 👇\n\n💡 Или просто напиши число:\n`76.5` = вес · `8500` = шаги",
            parse_mode="Markdown",reply_markup=main_kb())
        ctx.user_data["lm"]=msg.message_id

# ═══════════════════════════════════════════════════════════════════
# WEBAPP
# ═══════════════════════════════════════════════════════════════════
async def open_webapp(update: Update, ctx):
    if not WEBAPP_URL:
        await update.message.reply_text(
            "⚙️ Мини-апп не настроен. Добавь WEBAPP_URL в Railway Variables.",
            reply_markup=main_kb())
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Открыть FIT TRACKER", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await update.message.reply_text(
        "📱 *FIT TRACKER — Мини-апп*\n\nОткрой удобный интерфейс с ползунками:",
        parse_mode="Markdown", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    app=Application.builder().token(TOKEN).build()

    def conv(entries,states):
        return ConversationHandler(
            entry_points=entries, states=states,
            fallbacks=[CommandHandler("cancel",lambda u,c:ConversationHandler.END)],
            allow_reentry=True, per_message=False)

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start",cmd_start)],
        states={
            S_GOAL:  [CallbackQueryHandler(s_goal,pattern="^g_")],
            S_NAME:  [MessageHandler(filters.TEXT&~filters.COMMAND,s_name)],
            S_AGE:   [MessageHandler(filters.TEXT&~filters.COMMAND,s_age)],
            S_GENDER:[CallbackQueryHandler(s_gender,pattern="^gen_")],
            S_HEIGHT:[MessageHandler(filters.TEXT&~filters.COMMAND,s_height)],
            S_CW:    [MessageHandler(filters.TEXT&~filters.COMMAND,s_cw)],
            S_TW:    [MessageHandler(filters.TEXT&~filters.COMMAND,s_tw)],
            S_DATE:  [CallbackQueryHandler(s_date_btn,pattern="^gd_"),
                      MessageHandler(filters.TEXT&~filters.COMMAND,s_date_txt)],
            S_ACT:   [CallbackQueryHandler(s_act,pattern="^act_")],
            S_STEPS: [MessageHandler(filters.TEXT&~filters.COMMAND,s_steps)],
        },
        fallbacks=[CommandHandler("start",cmd_start)],
        allow_reentry=True, per_message=False))

    app.add_handler(conv(
        [CommandHandler("weight",start_weight),
         MessageHandler(filters.Regex(r"^(⚖️ Вес|⚖️|Вес)$"),start_weight)],
        {W_DATE:[CallbackQueryHandler(w_date,pattern="^wd_")],
         W_VAL: [CallbackQueryHandler(w_val_btn,pattern="^wv_"),
                 CallbackQueryHandler(w_val_btn,pattern="^wd_other"),
                 MessageHandler(filters.TEXT&~filters.COMMAND,w_val_txt)]}))

    app.add_handler(conv(
        [CommandHandler("steps",start_steps),
         MessageHandler(filters.Regex(r"^(👟 Шаги|👟|Шаги)$"),start_steps)],
        {ST_DATE:[CallbackQueryHandler(st_date,pattern="^sd_")],
         ST_VAL: [CallbackQueryHandler(st_val_btn,pattern="^sv_"),
                  CallbackQueryHandler(st_val_btn,pattern="^sd_other"),
                  MessageHandler(filters.TEXT&~filters.COMMAND,st_val_txt)]}))

    app.add_handler(conv(
        [CommandHandler("workout",start_workout),
         MessageHandler(filters.Regex(r"^(💪 Тренировка|💪|Тренировка)$"),start_workout)],
        {WO_DATE:[CallbackQueryHandler(wo_date,pattern="^wod_")],
         WO_VAL: [CallbackQueryHandler(wo_val,pattern="^wov_")]}))

    app.add_handler(conv(
        [CommandHandler("sleep",start_sleep),
         MessageHandler(filters.Regex(r"^(🌙 Сон|🌙|Сон)$"),start_sleep)],
        {SL_DATE:[CallbackQueryHandler(sl_date,pattern="^sld_")],
         SL_ST:  [CallbackQueryHandler(sl_st_btn,pattern="^slst_"),
                  MessageHandler(filters.TEXT&~filters.COMMAND,sl_st_txt)],
         SL_EN:  [CallbackQueryHandler(sl_en_btn,pattern="^slen_"),
                  MessageHandler(filters.TEXT&~filters.COMMAND,sl_en_txt)],
         SL_WK:  [CallbackQueryHandler(sl_wk,pattern="^slw_")]}))

    app.add_handler(conv(
        [CommandHandler("rating",start_rating),
         MessageHandler(filters.Regex(r"^(⭐ Оценка дня|⭐|Оценка)$"),start_rating)],
        {RT_DATE:[CallbackQueryHandler(rt_date,pattern="^rtd_")],
         RT_VAL: [CallbackQueryHandler(rt_val,pattern="^rtv_")]}))

    app.add_handler(CallbackQueryHandler(settings_cb,pattern="^cfg_"))
    app.add_handler(CommandHandler("home",show_home))
    app.add_handler(CommandHandler("app",open_webapp))
    app.add_handler(CommandHandler("status",show_status))
    app.add_handler(CommandHandler("achievements",show_achievements))
    app.add_handler(CommandHandler("calendar",show_calendar))
    app.add_handler(CommandHandler("analytics",show_analytics))
    app.add_handler(CommandHandler("settings",show_settings))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_text))

    # Daily reminders
    jq=app.job_queue
    if jq:
        from datetime import time as dtime
        jq.run_daily(morning_reminder, time=dtime(8,0,tzinfo=TZ))
        jq.run_daily(evening_reminder, time=dtime(21,0,tzinfo=TZ))

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
