# -*- coding: utf-8 -*-
"""
بوت إدارة تراخيص واشتراكات نظام إدارة المنتجات والكاشير (harpy)
لوحة تحكم ذكية للأدمن مع 4 باقات اشتراك رسمية وإدارة الحظر والحذف والتجديد التلقائي
"""

import os
import sys
import time
import random
import string
import datetime
import threading
import requests
import telebot
from telebot import types

# ضبط إخراج الكونسول على ويندوز
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ============================================================
# الإعدادات الأساسية والأمان (تحميل متغيرات البيئة تلقائياً)
# ============================================================
def load_env_file():
    """تحميل المتغيرات من ملف .env المحلي إن وجد تلقائياً بدعم جميع الترميزات (UTF-8, UTF-8-BOM, UTF-16)"""
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_file):
        return
    
    encodings = ["utf-8-sig", "utf-8", "utf-16", "cp1256"]
    content = None
    for enc in encodings:
        try:
            with open(env_file, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, Exception):
            continue

    if not content:
        return

    for line in content.splitlines():
        line = line.strip().lstrip('\ufeff')
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            key = key.strip().lstrip('\ufeff')
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val

load_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "1604040086")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "https://product-manager-5731f-default-rtdb.firebaseio.com/")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET", "")  # سر قاعدة بيانات Firebase لتخطي قيود الأمان بصلاحية الأدمن

if not FIREBASE_DB_URL.endswith('/'):
    FIREBASE_DB_URL += '/'

def get_fb_url(path):
    """بناء رابط Firebase مع إضافة توكن سر قاعدة البيانات (Database Secret) تلقائياً لصلاحيات الأدمن"""
    clean_path = path.lstrip('/')
    url = f"{FIREBASE_DB_URL}{clean_path}"
    if FIREBASE_SECRET:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}auth={FIREBASE_SECRET}"
    return url

if not BOT_TOKEN:
    print("=" * 60)
    print("❌ خطأ تشغيل: لم يتم العثور على BOT_TOKEN!")
    print("💡 يرجى إنشاء ملف .env داخل مجلد bot وكتابة التوكن الجديد:")
    print("   BOT_TOKEN=توكن_تليجرام_الجديد")
    print("   FIREBASE_SECRET=سر_قاعدة_البيانات")
    print("=" * 60)
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# خطط وباقات الاشتراكات المعتمدة
PLANS = {
    "month": {
        "name": "الاشتراك الشهري",
        "days": 30,
        "price": "200 ج.م",
        "desc": "صلاحية 30 يوماً"
    },
    "6months": {
        "name": "اشتراك 6 شهور",
        "days": 180,
        "price": "900 ج.م",
        "desc": "صلاحية 180 يوماً"
    },
    "year": {
        "name": "الاشتراك السنوي",
        "days": 365,
        "price": "1800 ج.م",
        "desc": "صلاحية 365 يوماً"
    },
    "lifetime": {
        "name": "اشتراك مدى الحياة",
        "days": None,  # بدون تاريخ انتهاء
        "price": "4500 ج.م",
        "desc": "دائم لمدى الحياة بدون تجديد"
    },
    "trial": {
        "name": "فترة تجريبية مجانية",
        "days": 7,
        "price": "مجاناً",
        "desc": "صلاحية 7 أيام للتجربة"
    }
}

user_states = {}

def is_admin(message):
    return str(message.chat.id) == str(ADMIN_CHAT_ID)

def generate_key():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"HARPY-{part1}-{part2}"

# ============================================================
# دوال الاتصال بقاعدة البيانات (Firebase REST API)
# ============================================================
def save_license_to_firebase(key, plan_id="month", phone="غير محدد", custom_days=None, notes=""):
    url = get_fb_url(f"licenses/{key}.json")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    
    plan = PLANS.get(plan_id, PLANS["month"])
    duration_days = custom_days if custom_days is not None else plan["days"]
    plan_name = plan["name"] if custom_days is None else f"مخصص ({custom_days} يوم)"
    plan_price = plan["price"] if custom_days is None else "مخصص"

    now_dt = datetime.datetime.now()
    expires_at_str = (now_dt + datetime.timedelta(days=int(duration_days))).isoformat() if duration_days else None

    data = {
        "key": key,
        "phone": phone,
        "notes": notes,
        "status": "active",
        "planId": plan_id,
        "planName": plan_name,
        "planPrice": plan_price,
        "durationDays": duration_days,
        "createdAt": now_str,
        "activatedAt": None,
        "expiresAt": expires_at_str,
        "userName": None,
        "userPhone": None,
        "pendingNotification": False
    }
    try:
        r = requests.put(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("[Firebase Error - Save]:", e)
        return False

def get_all_licenses():
    url = get_fb_url("licenses.json")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()
        return {}
    except Exception as e:
        print("[Firebase Error - GetAll]:", e)
        return {}

def get_license(key):
    url = get_fb_url(f"licenses/{key}.json")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def update_license_status(key, status):
    """
    تغيير حالة الحساب (active = نشط، blocked = موقوف مؤقتاً مع الحفاظ الكامل على البيانات)
    """
    url = get_fb_url(f"licenses/{key}/status.json")
    try:
        r = requests.put(url, json=status, timeout=10)
        return r.status_code == 200
    except:
        return False

def delete_account_completely(key):
    """
    حذف الحساب نهائياً مع مسح كافة المنتجات والفواتير والمبيعات السحابية
    """
    try:
        # 1. حذف كود الترخيص
        requests.delete(get_fb_url(f"licenses/{key}.json"), timeout=10)
        # 2. حذف المنتجات الخاصة بالمستخدم
        requests.delete(get_fb_url(f"users_data/{key}.json"), timeout=10)
        # 3. حذف الفواتير والمبيعات
        requests.delete(get_fb_url(f"sales/{key}.json"), timeout=10)
        return True
    except Exception as e:
        print("[Firebase Error - Delete Account]:", e)
        return False

def renew_or_extend_license(key, plan_id):
    """
    تجديد وتمديد اشتراك الحساب بالباقة المختارة
    """
    url = get_fb_url(f"licenses/{key}.json")
    try:
        lic = get_license(key)
        if not lic:
            return False, "كود الاشتراك غير موجود."

        plan = PLANS.get(plan_id)
        if not plan:
            return False, "خطة الاشتراك غير معروفة."

        days = plan["days"]
        now = datetime.datetime.now()

        if days is None:
            # مدى الحياة
            new_exp_str = None
            display_exp = "دائم مدى الحياة ∞"
        else:
            current_exp_str = lic.get("expiresAt")
            base_time = now
            if current_exp_str:
                try:
                    current_exp = datetime.datetime.fromisoformat(current_exp_str.replace("Z", ""))
                    base_time = max(now, current_exp)
                except:
                    base_time = now
            new_exp = base_time + datetime.timedelta(days=int(days))
            new_exp_str = new_exp.isoformat()
            display_exp = new_exp.strftime("%Y-%m-%d %I:%M %p")

        update_data = {
            "status": "active",
            "planId": plan_id,
            "planName": plan["name"],
            "planPrice": plan["price"],
            "expiresAt": new_exp_str,
            "durationDays": (lic.get("durationDays") or 0) + (days if days else 99999)
        }

        ru = requests.patch(url, json=update_data, timeout=10)
        if ru.status_code == 200:
            return True, display_exp
        return False, "فشل في تحديث الاشتراك."
    except Exception as e:
        return False, str(e)

# ============================================================
# لوحات المفاتيح والأزرار التفاعلية (Keyboards)
# ============================================================
def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_new   = types.KeyboardButton("➕ إنشاء كود تفعيل جديد")
    btn_list  = types.KeyboardButton("📋 قائمة المشتركين")
    btn_stats = types.KeyboardButton("📊 إحصائيات النظام")
    btn_help  = types.KeyboardButton("ℹ️ الأسعار والمساعدة")
    markup.add(btn_new, btn_list)
    markup.add(btn_stats, btn_help)
    return markup

def new_key_inline_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    b1 = types.InlineKeyboardButton("📅 باقة شهرية (30 يوم) — 200 ج.م", callback_data="gen_plan_month")
    b2 = types.InlineKeyboardButton("🕒 باقة 6 شهور (180 يوم) — 900 ج.م", callback_data="gen_plan_6months")
    b3 = types.InlineKeyboardButton("⭐ باقة سنوية (365 يوم) — 1800 ج.م", callback_data="gen_plan_year")
    b4 = types.InlineKeyboardButton("👑 باقة مدى الحياة (دائم) — 4500 ج.م", callback_data="gen_plan_lifetime")
    b5 = types.InlineKeyboardButton("🎁 تجربة مجانية (7 أيام)", callback_data="gen_plan_trial")
    b6 = types.InlineKeyboardButton("✍️ تخصيص برقم العميل ومدة مخصصة", callback_data="gen_plan_custom")
    markup.add(b1, b2, b3, b4, b5, b6)
    return markup

def license_action_keyboard(key, status, has_pin=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b_renew = types.InlineKeyboardButton("🔄 تمديد / تجديد الاشتراك", callback_data=f"opt_renew_{key}")
    
    if status == "blocked":
        b_toggle = types.InlineKeyboardButton("▶️ فك التجميد وتفعيل", callback_data=f"act_unblock_{key}")
    else:
        b_toggle = types.InlineKeyboardButton("⏸️ إيقاف الحساب مؤقتاً", callback_data=f"act_block_{key}")
    
    b_delete = types.InlineKeyboardButton("🗑️ حذف الحساب نهائياً", callback_data=f"ask_del_{key}")
    
    markup.add(b_renew)
    if has_pin:
        b_reset_pin = types.InlineKeyboardButton("🔑 تصفير رمز PIN", callback_data=f"ask_reset_pin_{key}")
        markup.add(b_toggle, b_reset_pin)
    else:
        markup.add(b_toggle)
    markup.add(b_delete)
    return markup

def confirm_reset_pin_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b_yes = types.InlineKeyboardButton("⚠️ نعم، صفّر رمز PIN", callback_data=f"do_reset_pin_{key}")
    b_no  = types.InlineKeyboardButton("❌ تراجع وإلغاء", callback_data=f"view_lic_{key}")
    markup.add(b_yes, b_no)
    return markup

def renew_plans_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=1)
    b1 = types.InlineKeyboardButton("📅 تمديد شهر (+30 يوم) — 200 ج.م", callback_data=f"do_renew_{key}_month")
    b2 = types.InlineKeyboardButton("🕒 تمديد 6 شهور (+180 يوم) — 900 ج.م", callback_data=f"do_renew_{key}_6months")
    b3 = types.InlineKeyboardButton("⭐ تمديد سنة (+365 يوم) — 1800 ج.م", callback_data=f"do_renew_{key}_year")
    b4 = types.InlineKeyboardButton("👑 ترقية لمدى الحياة — 4500 ج.م", callback_data=f"do_renew_{key}_lifetime")
    b_back = types.InlineKeyboardButton("🔙 رجوع", callback_data=f"view_lic_{key}")
    markup.add(b1, b2, b3, b4, b_back)
    return markup

def confirm_delete_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b_yes = types.InlineKeyboardButton("⚠️ نعم، احذف الحساب نهائياً", callback_data=f"do_del_{key}")
    b_no  = types.InlineKeyboardButton("❌ تراجع وإلغاء", callback_data=f"view_lic_{key}")
    markup.add(b_yes, b_no)
    return markup

# ============================================================
# نظام مراقبة التفعيل وانتهاء الصلاحيات اللحظي
# ============================================================
def activation_notifications_worker():
    print("📡 بدء خادم مراقبة التفعيل وانتهاء الاشتراكات...")
    while True:
        try:
            time.sleep(4)
            if not bot:
                continue
            
            url = get_fb_url("licenses.json")
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and r.json():
                licenses = r.json()
                for key, data in licenses.items():
                    if isinstance(data, dict) and data.get("pendingNotification") is True:
                        name = data.get("userName") or "غير محدد"
                        phone = data.get("userPhone") or data.get("phone") or "غير محدد"
                        exp_str = data.get("expiresAt")
                        plan_name = data.get("planName") or "غير محدد"
                        
                        if exp_str:
                            try:
                                exp_formatted = datetime.datetime.fromisoformat(exp_str.replace("Z", "")).strftime("%Y-%m-%d %I:%M %p")
                            except:
                                exp_formatted = exp_str
                        else:
                            exp_formatted = "مدى الحياة (دائم) ∞"

                        has_pin = bool(data.get("hasPin") or data.get("pinHash"))
                        pin_info = "🔒 تم تأمين الحساب بـ PIN مكوّن من 6 أرقام." if has_pin else "⚪ لم يُنشأ رمز PIN بعد."

                        text = (
                            "🎉 *تم تفعيل حساب جديد للتو على الموقع!*\n\n"
                            f"👤 *اسم المستخدم:* {name}\n"
                            f"📱 *رقم الموبايل:* `{phone}`\n"
                            f"🔑 *كود التفعيل:* `{key}`\n"
                            f"🛡️ *حماية الحساب:* {pin_info}\n"
                            f"📦 *الباقة:* {plan_name}\n"
                            f"⏳ *تاريخ انتهاء الاشتراك:* `{exp_formatted}`\n"
                            f"⏰ *وقت التفعيل:* {datetime.datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n\n"
                            "✅ الحساب متصل بالمزامنة السحابية وقاعدة البيانات بنجاح."
                        )
                        try:
                            bot.send_message(
                                ADMIN_CHAT_ID, 
                                text, 
                                parse_mode="Markdown",
                                reply_markup=license_action_keyboard(key, data.get("status", "active"), has_pin=has_pin)
                            )
                            now_act = datetime.datetime.now()
                            patch_payload = {
                                "pendingNotification": False,
                                "activatedAt": now_act.isoformat()
                            }
                            if not data.get("expiresAt") and data.get("durationDays"):
                                patch_payload["expiresAt"] = (now_act + datetime.timedelta(days=int(data["durationDays"]))).isoformat()

                            requests.patch(get_fb_url(f"licenses/{key}.json"), json=patch_payload, timeout=5)
                            print(f"[إشعار]: تم إرسال تنبيه تفعيل الكود {key} للمدير بنجاح وتعيين تاريخ التفعيل.")
                        except Exception as ex:
                            print("[Notification Send Error]:", ex)
        except Exception:
            pass

# تشغيل الثريد في الخلفية
notification_thread = threading.Thread(target=activation_notifications_worker, daemon=True)
notification_thread.start()

# ============================================================
# معالجة الرسائل والأوامر
# ============================================================
@bot.message_handler(commands=['start'])
def handle_start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ عذراً، هذا البوت مخصص فقط لمدير نظام إدارة المنتجات والكاشير.")
        return

    user_states[message.chat.id] = None
    welcome_text = (
        "👑 **مرحباً بك في لوحة تحكم إدارة الاشتراكات والكاشير (harpy)**\n\n"
        "📊 **أسعار الباقات الرسمية المعتمدة:**\n"
        "• 📅 **شهرية (30 يوم):** `200 ج.م`\n"
        "• 🕒 **6 شهور (180 يوم):** `900 ج.م`\n"
        "• ⭐ **سنوية (365 يوم):** `1800 ج.م`\n"
        "• 👑 **مدى الحياة:** `4500 ج.م`\n\n"
        "اختر الإجراء المطلوب من الأزرار بالأسفل 👇"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

@bot.message_handler(func=lambda msg: msg.text == "➕ إنشاء كود تفعيل جديد")
def handle_btn_new(message):
    if not is_admin(message): return
    bot.send_message(
        message.chat.id, 
        "🏷️ **اختر باقة ومدة الاشتراك للكود الجديد:**", 
        reply_markup=new_key_inline_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: msg.text == "📋 قائمة المشتركين")
def handle_btn_list(message):
    if not is_admin(message): return
    send_subscribers_list(message.chat.id)

@bot.message_handler(func=lambda msg: msg.text == "📊 إحصائيات النظام")
def handle_btn_stats(message):
    if not is_admin(message): return
    send_system_stats(message.chat.id)

@bot.message_handler(func=lambda msg: msg.text == "ℹ️ الأسعار والمساعدة")
def handle_btn_help(message):
    if not is_admin(message): return
    help_text = (
        "📖 **دليل إدارة الاشتراكات والأسعار:**\n\n"
        "💰 **قائمة أسعار الباقات:**\n"
        "1️⃣ **الاشتراك الشهري:** `200 ج.م` (30 يوم)\n"
        "2️⃣ **اشتراك 6 شهور:** `900 ج.م` (180 يوم)\n"
        "3️⃣ **الاشتراك السنوي:** `1800 ج.م` (365 يوم)\n"
        "4️⃣ **اشتراك مدى الحياة:** `4500 ج.م` (دائم)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ **التحكم في الحسابات:**\n"
        "• ⏸️ **إيقاف مؤقت (تجميد):** يمنع العميل من الدخول مع الحفاظ الكامل على منتجاته وفواتيره ليعود إليها عند فك الحظر.\n"
        "• 🗑️ **حذف نهائي:** يمسح كود الحساب وكل منتجاته وفواتيره بالكامل من السيرفر.\n"
        "• 🔄 **تمديد / تجديد:** إضافة مدة جديدة لأي حساب منتهي أو نشط بسهولة."
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

# ============================================================
# معالجة أزرار الـ Callbacks (Inline Buttons)
# ============================================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    if not str(call.message.chat.id) == str(ADMIN_CHAT_ID):
        bot.answer_callback_query(call.id, "غير مصرح لك.")
        return

    data = call.data

    # توليد أكواد الباقات
    if data.startswith("gen_plan_"):
        plan_id = data.replace("gen_plan_", "")
        if plan_id == "custom":
            user_states[call.message.chat.id] = "waiting_for_custom_key"
            bot.send_message(
                call.message.chat.id, 
                "✍️ **أدخل رقم هاتف العميل ومدة الاشتراك بالأيام:**\n\n"
                "مثال: `01012345678 45`",
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)
        elif plan_id in PLANS:
            create_and_send_key(call.message.chat.id, plan_id=plan_id)
            bot.answer_callback_query(call.id, f"تم إنشاء كود {PLANS[plan_id]['name']} بنجاح ✓")

    # خيارات التجديد
    elif data.startswith("opt_renew_"):
        key = data.replace("opt_renew_", "")
        bot.edit_message_reply_markup(
            call.message.chat.id, 
            call.message.message_id, 
            reply_markup=renew_plans_keyboard(key)
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("do_renew_"):
        parts = data.split("_")
        key = parts[2]
        plan_id = parts[3]
        ok, res = renew_or_extend_license(key, plan_id)
        if ok:
            plan_name = PLANS.get(plan_id, {}).get("name", "الباقة")
            bot.answer_callback_query(call.id, f"تم تجديد الاشتراك بنجاح ({plan_name}) ✓")
            bot.send_message(
                call.message.chat.id, 
                f"🎉 **تم تجديد وتفعيل الكود `{key}` بنجاح!**\n📦 **الباقة:** {plan_name}\n📅 **تاريخ الانتهاء الجديد:** `{res}`", 
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, f"خطأ: {res}", show_alert=True)

    # إيقاف الحساب مؤقتاً (تجميد)
    elif data.startswith("act_block_"):
        key = data.replace("act_block_", "")
        if update_license_status(key, "blocked"):
            bot.answer_callback_query(call.id, "تم تجميد الحساب وإيقافه مؤقتاً ✓")
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=license_action_keyboard(key, "blocked")
            )
            bot.send_message(
                call.message.chat.id, 
                f"⏸️ **تم تجميد الحساب `{key}` مؤقتاً.**\n\n(تم حظر دخول المستخدم مع الحفاظ على كافة منتجاته وفواتيره).", 
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "فشل الإيقاف، تأكد من الاتصال.", show_alert=True)

    # فك تجميد الحساب
    elif data.startswith("act_unblock_"):
        key = data.replace("act_unblock_", "")
        if update_license_status(key, "active"):
            bot.answer_callback_query(call.id, "تم فك التجميد وتفعيل الحساب ✓")
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=license_action_keyboard(key, "active")
            )
            bot.send_message(
                call.message.chat.id, 
                f"▶️ **تم إعادة تفعيل الحساب `{key}` بنجاح!**\nيمكن للمستخدم الدخول فوراً.", 
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "فشل التفعيل.", show_alert=True)

    # طلب تأكيد حذف الحساب نهائياً
    elif data.startswith("ask_del_"):
        key = data.replace("ask_del_", "")
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=confirm_delete_keyboard(key)
        )
        bot.answer_callback_query(call.id, "تنبيه: سيتم مسح الحساب وبياناته نهائياً!", show_alert=True)

    # تنفيذ الحذف النهائي
    elif data.startswith("do_del_"):
        key = data.replace("do_del_", "")
        if delete_account_completely(key):
            bot.answer_callback_query(call.id, "تم حذف الحساب وبياناته نهائياً ✓")
            bot.edit_message_text(
                f"🗑️ **تم حذف الحساب `{key}` وكافة منتجاته وفواتيره نهائياً من السيرفر.**",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "فشل في حذف الحساب.", show_alert=True)

    # طلب تأكيد تصفير رمز PIN
    elif data.startswith("ask_reset_pin_"):
        key = data.replace("ask_reset_pin_", "")
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=confirm_reset_pin_keyboard(key)
        )
        bot.answer_callback_query(call.id, "تنبيه: سيتم مسح رمز PIN ليتمكن التاجر من إنشاء رمز جديد!", show_alert=True)

    # تنفيذ تصفير رمز PIN
    elif data.startswith("do_reset_pin_"):
        key = data.replace("do_reset_pin_", "")
        url = get_fb_url(f"licenses/{key}.json")
        try:
            r = requests.patch(url, json={"hasPin": None, "pinHash": None, "pinSalt": None, "failedAttempts": 0, "lockedUntil": None}, timeout=10)
            if r.status_code == 200:
                bot.answer_callback_query(call.id, "تم تصفير رمز PIN بنجاح ✓")
                bot.send_message(
                    call.message.chat.id,
                    f"🔑 **تم تصفير رمز PIN للحساب `{key}` بنجاح!**\nسيُطلب من التاجر إنشاء رمز PIN جديد مكون من 6 أرقام عند أول دخول.",
                    parse_mode="Markdown"
                )
                lic = get_license(key)
                status = lic.get("status", "active") if lic else "active"
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=license_action_keyboard(key, status, has_pin=False)
                )
            else:
                bot.answer_callback_query(call.id, "فشل في تصفير رمز PIN.", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"خطأ: {e}", show_alert=True)

    # إعادة عرض أزرار الترخيص العادية
    elif data.startswith("view_lic_"):
        key = data.replace("view_lic_", "")
        lic = get_license(key)
        status = lic.get("status", "active") if lic else "active"
        has_pin = bool(lic.get("hasPin") or lic.get("pinHash")) if lic else False
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=license_action_keyboard(key, status, has_pin=has_pin)
        )
        bot.answer_callback_query(call.id)

# معالجة المدخلات النصية المخصصة
@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "waiting_for_custom_key")
def handle_custom_key_input(message):
    user_states[message.chat.id] = None
    parts = message.text.strip().split()
    phone = parts[0] if len(parts) > 0 else "غير محدد"
    duration = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    create_and_send_key(message.chat.id, plan_id="custom", phone=phone, custom_days=duration)

def create_and_send_key(chat_id, plan_id="month", phone="غير محدد", custom_days=None):
    key = generate_key()
    ok = save_license_to_firebase(key, plan_id=plan_id, phone=phone, custom_days=custom_days)
    if ok:
        plan = PLANS.get(plan_id, PLANS["month"])
        plan_name = plan["name"] if custom_days is None else f"مخصص ({custom_days} يوم)"
        plan_price = plan["price"] if custom_days is None else "مخصص"
        duration_label = "دائم مدى الحياة ∞" if plan["days"] is None and custom_days is None else f"{custom_days or plan['days']} يوماً (تبدأ من أول تسجيل دخول)"

        client_message = (
            "✅ **تم إنشاء كود تفعيل واشتراك جديد بنجاح!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 **كود التفعيل / كلمة السر:**\n`{key}`\n\n"
            f"📦 **الباقة:** {plan_name} ({plan_price})\n"
            f"⏳ **مدة الصلاحية:** `{duration_label}`\n"
            f"📱 **رقم العميل:** `{phone}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📌 **الرسالة الجاهزة للإرسال للعميل (انسخها وأرسلها له):**\n"
            "──────────────\n"
            f"مرحباً بك! كود تفعيل نظام إدارة المنتجات والكاشير (harpy) الخاص بك هو:\n"
            f"🔑 `{key}`\n"
            f"📦 الباقة: {plan_name}\n"
            f"⏳ الصلاحية: {duration_label}\n"
            "──────────────"
        )
        bot.send_message(chat_id, client_message, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "❌ حدث خطأ أثناء الاتصال بقاعدة البيانات، تأكد من إعدادات وصلاحيات Firebase.")

def send_subscribers_list(chat_id):
    licenses = get_all_licenses()
    if not licenses:
        bot.send_message(chat_id, "📭 لا توجد أي اشتراكات مسجلة حالياً.")
        return

    bot.send_message(chat_id, f"📋 **قائمة المشتركين المسجلين (إجمالي: {len(licenses)}):**", parse_mode="Markdown")
    now = datetime.datetime.now()

    for key, data in licenses.items():
        if not isinstance(data, dict):
            continue

        status = data.get("status", "active")
        exp_str = data.get("expiresAt")
        plan_name = data.get("planName") or "الاشتراك الشهري"
        is_expired = False
        
        if data.get("durationDays") is None or data.get("planId") == "lifetime":
            exp_display = "مدى الحياة (دائم) ∞"
        elif exp_str:
            try:
                exp_dt = datetime.datetime.fromisoformat(exp_str.replace("Z", ""))
                exp_display = exp_dt.strftime("%Y-%m-%d %I:%M %p")
                if now > exp_dt:
                    is_expired = True
            except:
                exp_display = exp_str
        else:
            exp_display = "لم يبدأ بعد (في انتظار أول دخول)"

        if status == "blocked":
            status_text = "⏸️ موقوف ومجمد مؤقتاً"
        elif is_expired:
            status_text = "⏳ منتهي الصلاحية"
        elif data.get("activatedAt"):
            status_text = "🟢 نشط ومفعل"
        else:
            status_text = "🟡 جديد (في انتظار التفعيل)"

        user_name = data.get("userName") or "غير مسجل بعد"
        user_phone = data.get("userPhone") or data.get("phone") or "غير محدد"

        has_pin = bool(data.get("hasPin") or data.get("pinHash"))
        pin_badge = "🟢 مفعّل ومحمي بـ PIN" if has_pin else "⚪ لسه ما عملش PIN"

        card_text = (
            f"👤 **المستخدم:** {user_name}\n"
            f"📱 **الموبايل:** `{user_phone}`\n"
            f"🔑 **الكود:** `{key}`\n"
            f"🔒 **حماية الحساب:** {pin_badge}\n"
            f"📦 **الباقة:** {plan_name}\n"
            f"📊 **الحالة:** {status_text}\n"
            f"📅 **الانتهاء:** `{exp_display}`"
        )
        
        bot.send_message(
            chat_id, 
            card_text, 
            parse_mode="Markdown",
            reply_markup=license_action_keyboard(key, status, has_pin=has_pin)
        )

def send_system_stats(chat_id):
    licenses = get_all_licenses()
    total = len(licenses)
    now = datetime.datetime.now()
    active_cnt = 0
    expired_cnt = 0
    blocked_cnt = 0
    pending_cnt = 0
    lifetime_cnt = 0

    for k, v in licenses.items():
        if isinstance(v, dict):
            status = v.get("status")
            exp_str = v.get("expiresAt")
            is_lifetime = (v.get("planId") == "lifetime" or v.get("durationDays") is None)
            
            if is_lifetime:
                lifetime_cnt += 1
            
            is_expired = False
            if exp_str and not is_lifetime:
                try:
                    exp_dt = datetime.datetime.fromisoformat(exp_str.replace("Z", ""))
                    if now > exp_dt:
                        is_expired = True
                except:
                    pass
            
            if status == "blocked":
                blocked_cnt += 1
            elif is_expired:
                expired_cnt += 1
            elif v.get("activatedAt"):
                active_cnt += 1
            else:
                pending_cnt += 1

    stats_text = (
        "📊 **إحصائيات نظام إدارة المنتجات وتراخيص المشتركين:**\n\n"
        f"🏷️ إجمالي الأكواد: **{total}**\n"
        f"🟢 الاشتراكات النشطة: **{active_cnt}**\n"
        f"👑 باقات مدى الحياة: **{lifetime_cnt}**\n"
        f"🟡 أكواد جديدة بانتظار التفعيل: **{pending_cnt}**\n"
        f"⏳ الاشتراكات المنتهية: **{expired_cnt}**\n"
        f"⏸️ الحسابات المجمدة مؤقتاً: **{blocked_cnt}**\n"
    )
    bot.send_message(chat_id, stats_text, parse_mode="Markdown")

if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("🚀 تم تشغيل بوت تليجرام التفاعلي بنجاح!", flush=True)
    print(f"👑 حساب المدير المعتمد: {ADMIN_CHAT_ID}", flush=True)
    print("💰 تم ضبط باقات الأسعار المعتمدة (200 / 900 / 1800 / 4500 ج.م)", flush=True)
    print("=" * 60, flush=True)
    bot.infinity_polling()
