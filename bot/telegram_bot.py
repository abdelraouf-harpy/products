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

# خطة الترخيص المعتمدة (مدى الحياة فقط - ترخيص دائم لجهاز واحد)
PLANS = {
    "lifetime": {
        "name": "ترخيص مدى الحياة",
        "days": None,  # دائم بدون تاريخ انتهاء
        "desc": "ترخيص دائم لجهاز واحد مدى الحياة بدون أي اشتراكات أو تجديد"
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
def save_license_to_firebase(key, plan_id="lifetime", phone="غير محدد", custom_days=None, notes=""):
    url = get_fb_url(f"licenses/{key}.json")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    
    plan_name = "ترخيص مدى الحياة"

    data = {
        "key": key,
        "phone": phone,
        "notes": notes,
        "status": "active",
        "planId": "lifetime",
        "planName": plan_name,
        "planPrice": None,
        "durationDays": None,
        "offlineMode": "permanent",
        "lockedDeviceFingerprint": None,
        "createdAt": now_str,
        "activatedAt": None,
        "expiresAt": None,
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

def renew_or_extend_license(key, plan_id="lifetime"):
    """
    تثبيت / ترقية الترخيص لمدى الحياة (جهاز واحد دائم بدون اشتراك)
    """
    url = get_fb_url(f"licenses/{key}.json")
    try:
        lic = get_license(key)
        if not lic:
            return False, "كود الترخيص غير موجود."

        update_data = {
            "status": "active",
            "planId": "lifetime",
            "planName": "ترخيص مدى الحياة",
            "planPrice": None,
            "expiresAt": None,
            "durationDays": None,
            "offlineMode": "permanent"
        }

        ru = requests.patch(url, json=update_data, timeout=10)
        if ru.status_code == 200:
            return True, "دائم مدى الحياة ∞"
        return False, "فشل في تحديث الترخيص."
    except Exception as e:
        return False, str(e)

# ============================================================
# لوحات المفاتيح والأزرار التفاعلية (Keyboards)
# ============================================================
def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_new   = types.KeyboardButton("➕ إصدار كود ترخيص (مدى الحياة)")
    btn_list  = types.KeyboardButton("📋 قائمة التراخيص والأجهزة")
    btn_stats = types.KeyboardButton("📊 إحصائيات النظام")
    btn_help  = types.KeyboardButton("ℹ️ دليل النظام والحماية")
    markup.add(btn_new, btn_list)
    markup.add(btn_stats, btn_help)
    return markup

def new_key_inline_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    b1 = types.InlineKeyboardButton("👑 إصدار كود ترخيص مدى الحياة (جهاز واحد)", callback_data="gen_plan_lifetime")
    b2 = types.InlineKeyboardButton("📱 إصدار برقم هاتف العميل", callback_data="gen_plan_custom")
    markup.add(b1, b2)
    return markup

def license_action_keyboard(key, status, has_pin=False, has_fingerprint=False, offline_mode="permanent"):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if status == "blocked":
        b_toggle = types.InlineKeyboardButton("▶️ فك التجميد وتفعيل", callback_data=f"act_unblock_{key}")
    else:
        b_toggle = types.InlineKeyboardButton("⏸️ إيقاف الترخيص مؤقتاً", callback_data=f"act_block_{key}")
    
    b_delete = types.InlineKeyboardButton("🗑️ حذف الترخيص نهائياً", callback_data=f"ask_del_{key}")
    
    if has_fingerprint or offline_mode == "permanent":
        b_unlock = types.InlineKeyboardButton("🔓 فك قفل الجهاز والـ PIN", callback_data=f"ask_unlock_dev_{key}")
        markup.add(b_toggle, b_unlock)
    elif has_pin:
        b_reset_pin = types.InlineKeyboardButton("🔑 تصفير رمز PIN", callback_data=f"ask_reset_pin_{key}")
        markup.add(b_toggle, b_reset_pin)
    else:
        markup.add(b_toggle)

    b_upgrade = types.InlineKeyboardButton("👑 تثبيت مدى الحياة", callback_data=f"do_renew_{key}_lifetime")
    markup.add(b_upgrade, b_delete)
    return markup

def confirm_unlock_device_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b_yes = types.InlineKeyboardButton("⚠️ نعم، فك القفل وامسح البصمة", callback_data=f"do_unlock_dev_{key}")
    b_no  = types.InlineKeyboardButton("❌ تراجع وإلغاء", callback_data=f"view_lic_{key}")
    markup.add(b_yes, b_no)
    return markup

def confirm_reset_pin_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b_yes = types.InlineKeyboardButton("⚠️ نعم، صفّر رمز PIN", callback_data=f"do_reset_pin_{key}")
    b_no  = types.InlineKeyboardButton("❌ تراجع وإلغاء", callback_data=f"view_lic_{key}")
    markup.add(b_yes, b_no)
    return markup

def renew_plans_keyboard(key):
    markup = types.InlineKeyboardMarkup(row_width=1)
    b1 = types.InlineKeyboardButton("📅 تمديد شهر (+30 يوم) — 550 ج.م", callback_data=f"do_renew_{key}_month")
    b2 = types.InlineKeyboardButton("🕒 تمديد 6 شهور (+180 يوم) — 2400 ج.م", callback_data=f"do_renew_{key}_6months")
    b3 = types.InlineKeyboardButton("⭐ تمديد سنة (+365 يوم) — 4000 ج.م", callback_data=f"do_renew_{key}_year")
    b4 = types.InlineKeyboardButton("👑 ترقية لمدى الحياة — 8000 ج.م", callback_data=f"do_renew_{key}_lifetime")
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
                            "🎉 *تم تفعيل ترخيص جديد وربط الجهاز بنجاح!*\n\n"
                            f"👤 *اسم المستخدم:* {name}\n"
                            f"📱 *رقم الموبايل:* `{phone}`\n"
                            f"🔑 *كود التفعيل:* `{key}`\n"
                            f"🛡️ *حماية الحساب:* {pin_info}\n"
                            f"👑 *نوع الترخيص:* ترخيص دائم مدى الحياة (جهاز واحد محمي)\n"
                            f"⚡ *نمط التشغيل:* أوفلاين 100% (يعمل بدون إنترنت بعد التفعيل الأول)\n"
                            f"⏰ *وقت التفعيل:* {datetime.datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n\n"
                            "✅ الحساب مقترن بجهاز العميل ومحمي من النقل بنجاح."
                        )
                        try:
                            bot.send_message(
                                ADMIN_CHAT_ID, 
                                text, 
                                parse_mode="Markdown",
                                reply_markup=license_action_keyboard(key, data.get("status", "active"), has_pin=has_pin, has_fingerprint=True, offline_mode="permanent")
                            )
                            now_act = datetime.datetime.now()
                            patch_payload = {
                                "pendingNotification": False,
                                "activatedAt": now_act.isoformat(),
                                "offlineMode": "permanent"
                            }
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
        "👑 **مرحباً بك في لوحة تحكم تراخيص الكاشير وإدارة المنتجات (harpy)**\n\n"
        "🛡️ **نظام التراخيص المعتمد: مدى الحياة (جهاز واحد فقط)**\n"
        "• 👑 **نوع الترخيص:** دائم مدى الحياة (بدون أي اشتراكات دورية نهائياً)\n"
        "• 🔒 **حماية العتاد:** يعمل على جهاز واحد فقط ومحمي من النقل أو التمرير\n"
        "• ⚡ **التشغيل أوفلاين:** تفعيل أولي بالإنترنت لتوثيق الجهاز، ثم تشغيل 100% بدون إنترنت للأبد\n\n"
        "اختر الإجراء المطلوب من الأزرار بالأسفل 👇"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

@bot.message_handler(func=lambda msg: msg.text in ["➕ إصدار كود ترخيص (مدى الحياة)", "➕ إنشاء كود تفعيل جديد"])
def handle_btn_new(message):
    if not is_admin(message): return
    bot.send_message(
        message.chat.id, 
        "🏷️ **إصدار ترخيص دائم مدى الحياة (جهاز واحد محمي):**", 
        reply_markup=new_key_inline_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: msg.text in ["📋 قائمة التراخيص والأجهزة", "📋 قائمة المشتركين"])
def handle_btn_list(message):
    if not is_admin(message): return
    send_subscribers_list(message.chat.id)

@bot.message_handler(func=lambda msg: msg.text == "📊 إحصائيات النظام")
def handle_btn_stats(message):
    if not is_admin(message): return
    send_system_stats(message.chat.id)

@bot.message_handler(func=lambda msg: msg.text in ["ℹ️ دليل النظام والحماية", "ℹ️ الأسعار والمساعدة"])
def handle_btn_help(message):
    if not is_admin(message): return
    help_text = (
        "📖 **دليل نظام التراخيص وحماية الأجهزة:**\n\n"
        "👑 **1. ترخيص مدى الحياة (Lifetime):**\n"
        "تم إلغاء الاشتراكات الدورية بالكامل. كافة التراخيص الصادرة تعمل مدى الحياة بدون أي رسوم تجديد.\n\n"
        "🔒 **2. حماية الجهاز الواحد (Hardware-Locked):**\n"
        "عند تفعيل العميل للكود أول مرة، يربط النظام الترخيص ببصمة عتاد الجهاز (الشاشة، المعالج، الرسوميات، المتصفح). لا يمكن تشغيل الترخيص على أي جهاز آخر، ولا يمكن لأحد تمريره لأجهزة إضافية.\n\n"
        "⚡ **3. العمل أوفلاين بنسبة 100%:**\n"
        "يحتاج العميل للإنترنت في الدقيقة الأولى فقط لربط الحساب بالعتاد وتوثيق البصمة، وبعدها يعمل البرنامج أوفلاين بالكامل 100% دون الحاجة لأي اتصال بالإنترنت نهائياً.\n\n"
        "🔓 **4. نقل الترخيص لجهاز بديل:**\n"
        "في حال غير العميل جهازه أو حدث عطل بالكمبيوتر، اضغط على زر '🔓 فك قفل الجهاز' لمسح البصمة السابقة، وسيتمكن العميل من الدخول وربط جهازه الجديد فوراً."
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

    # توليد أكواد التراخيص
    if data == "gen_plan_lifetime" or data.startswith("gen_plan_"):
        plan_id = data.replace("gen_plan_", "")
        if plan_id == "custom":
            user_states[call.message.chat.id] = "waiting_for_custom_key"
            bot.send_message(
                call.message.chat.id, 
                "✍️ **أدخل رقم هاتف العميل للكود الجديد:**\n\n"
                "مثال: `01012345678`",
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)
        else:
            create_and_send_key(call.message.chat.id, plan_id="lifetime")
            bot.answer_callback_query(call.id, "تم إصدار كود مدى الحياة بنجاح ✓")

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
                off_m = lic.get("offlineMode", "weekly-checkin") if lic else "weekly-checkin"
                locked_fp = bool(lic.get("lockedDeviceFingerprint")) if lic else False
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=license_action_keyboard(key, status, has_pin=False, has_fingerprint=locked_fp, offline_mode=off_m)
                )
            else:
                bot.answer_callback_query(call.id, "فشل في تصفير رمز PIN.", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"خطأ: {e}", show_alert=True)

    # طلب تأكيد فك قفل الجهاز والبصمة والـ PIN
    elif data.startswith("ask_unlock_dev_"):
        key = data.replace("ask_unlock_dev_", "")
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=confirm_unlock_device_keyboard(key)
        )
        bot.answer_callback_query(call.id, "تنبيه: سيتم فك قفل الجهاز ومسح البصمة والـ PIN ليتمكن التاجر من استعادة حسابه على جهاز جديد!", show_alert=True)

    # تنفيذ فك قفل الجهاز والبصمة والـ PIN
    elif data.startswith("do_unlock_dev_"):
        key = data.replace("do_unlock_dev_", "")
        url = get_fb_url(f"licenses/{key}.json")
        try:
            r = requests.patch(url, json={
                "lockedDeviceFingerprint": None,
                "hasPin": None,
                "pinHash": None,
                "pinSalt": None,
                "failedAttempts": 0,
                "lockedUntil": None
            }, timeout=10)
            if r.status_code == 200:
                bot.answer_callback_query(call.id, "تم فك قفل الجهاز والـ PIN بنجاح ✓")
                bot.send_message(
                    call.message.chat.id,
                    f"🔓 **تم فك قفل الجهاز للحساب `{key}` بنجاح!**\n\n"
                    f"• تم مسح بصمة الجهاز السابقة ورمز PIN الماستر.\n"
                    f"• يمكن للتاجر الآن تسجيل الدخول من جهازه الجديد أو متصفحه وإنشاء رمز PIN جديد، وسيتم ربط البصمة الجديدة تلقائياً.",
                    parse_mode="Markdown"
                )
                lic = get_license(key)
                status = lic.get("status", "active") if lic else "active"
                off_m = lic.get("offlineMode", "weekly-checkin") if lic else "weekly-checkin"
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=license_action_keyboard(key, status, has_pin=False, has_fingerprint=False, offline_mode=off_m)
                )
            else:
                bot.answer_callback_query(call.id, "فشل في فك قفل الجهاز عبر السيرفر.", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"خطأ: {e}", show_alert=True)

    # إعادة عرض أزرار الترخيص العادية
    elif data.startswith("view_lic_"):
        key = data.replace("view_lic_", "")
        lic = get_license(key)
        status = lic.get("status", "active") if lic else "active"
        has_pin = bool(lic.get("hasPin") or lic.get("pinHash")) if lic else False
        locked_fp = bool(lic.get("lockedDeviceFingerprint")) if lic else False
        off_m = lic.get("offlineMode", "weekly-checkin") if lic else "weekly-checkin"
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=license_action_keyboard(key, status, has_pin=has_pin, has_fingerprint=locked_fp, offline_mode=off_m)
        )
        bot.answer_callback_query(call.id)

# معالجة المدخلات النصية المخصصة
@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "waiting_for_custom_key")
def handle_custom_key_input(message):
    user_states[message.chat.id] = None
    phone = message.text.strip().split()[0] if message.text.strip() else "غير محدد"
    create_and_send_key(message.chat.id, plan_id="lifetime", phone=phone)

def create_and_send_key(chat_id, plan_id="lifetime", phone="غير محدد", custom_days=None):
    key = generate_key()
    ok = save_license_to_firebase(key, plan_id="lifetime", phone=phone)
    if ok:
        client_message = (
            "✅ **تم إصدار كود ترخيص مدى الحياة بنجاح!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 **كود التفعيل:**\n`{key}`\n\n"
            f"👑 **نوع الترخيص:** دائم مدى الحياة (بدون أي اشتراكات دورية)\n"
            f"🔒 **حماية العتاد:** يعمل على جهاز واحد فقط ومحمي من النقل أو المشاركة\n"
            f"⚡ **نمط التشغيل:** تفعيل أول مرة بالإنترنت، ثم 100% أوفلاين للأبد\n"
            f"📱 **رقم العميل:** `{phone}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📌 **رسالة التفعيل الجاهزة للعميل (انسخها وأرسلها له):**\n"
            "──────────────\n"
            "مرحباً بك! كود ترخيص نظام الكاشير وإدارة المنتجات (harpy) الخاص بك هو:\n\n"
            f"🔑 `{key}`\n\n"
            "👑 **الترخيص:** دائم مدى الحياة (لجهاز واحد فقط)\n"
            "⚡ **طريقة التفعيل:**\n"
            "1️⃣ افتح البرنامج مع وجود اتصال بالإنترنت (للتفعيل الأول فقط لتوثيق الجهاز).\n"
            "2️⃣ أدخل كود التفعيل أعلاه وأنشئ رمز PIN الماستر.\n"
            "3️⃣ سيتم ربط الترخيص بجهازك تلقائياً وبشكل محمي دائم.\n"
            "4️⃣ يمكنك بعد ذلك فصل الإنترنت واستخدام البرنامج 100% أوفلاين للأبد.\n"
            "──────────────"
        )
        bot.send_message(chat_id, client_message, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "❌ حدث خطأ أثناء الاتصال بقاعدة البيانات، تأكد من إعدادات وصلاحيات Firebase.")

def send_subscribers_list(chat_id):
    licenses = get_all_licenses()
    if not licenses:
        bot.send_message(chat_id, "📭 لا توجد أي تراخيص مسجلة حالياً.")
        return

    bot.send_message(chat_id, f"📋 **قائمة تراخيص الأجهزة المسجلة (إجمالي: {len(licenses)}):**", parse_mode="Markdown")

    for key, data in licenses.items():
        if not isinstance(data, dict):
            continue

        status = data.get("status", "active")
        exp_display = "دائم مدى الحياة ∞"

        if status == "blocked":
            status_text = "⏸️ موقوف ومجمد مؤقتاً"
        elif data.get("lockedDeviceFingerprint") or data.get("activatedAt"):
            status_text = "🟢 نشط ومقترن بجهاز"
        else:
            status_text = "🟡 جديد (في انتظار أول تفعيل)"

        user_name = data.get("userName") or "غير مسجل بعد"
        user_phone = data.get("userPhone") or data.get("phone") or "غير محدد"

        has_pin = bool(data.get("hasPin") or data.get("pinHash"))
        pin_badge = "🟢 تم إنشاء PIN" if has_pin else "⚪ في انتظار إنشاء PIN"

        locked_fp = bool(data.get("lockedDeviceFingerprint"))
        fp_badge = "🔒 مقترن بجهاز محمي" if locked_fp else "⏳ بانتظار الاقتران بالجهاز"

        card_text = (
            f"👤 **المستخدم:** {user_name}\n"
            f"📱 **الموبايل:** `{user_phone}`\n"
            f"🔑 **الكود:** `{key}`\n"
            f"🛡️ **حماية العتاد:** {fp_badge}\n"
            f"🔒 **رمز PIN:** {pin_badge}\n"
            f"👑 **الترخيص:** دائم مدى الحياة (100% أوفلاين)\n"
            f"📊 **الحالة:** {status_text}"
        )
        
        bot.send_message(
            chat_id, 
            card_text, 
            parse_mode="Markdown",
            reply_markup=license_action_keyboard(key, status, has_pin=has_pin, has_fingerprint=locked_fp, offline_mode="permanent")
        )

def send_system_stats(chat_id):
    licenses = get_all_licenses()
    total = len(licenses)
    active_cnt = 0
    blocked_cnt = 0
    pending_cnt = 0

    for k, v in licenses.items():
        if isinstance(v, dict):
            status = v.get("status")
            locked_fp = bool(v.get("lockedDeviceFingerprint"))
            
            if status == "blocked":
                blocked_cnt += 1
            elif locked_fp or v.get("activatedAt"):
                active_cnt += 1
            else:
                pending_cnt += 1

    stats_text = (
        "📊 **إحصائيات نظام تراخيص الكاشير والأجهزة (مدى الحياة):**\n\n"
        f"🏷️ إجمالي التراخيص: **{total}**\n"
        f"🔒 التراخيص المقترنة والمفعلة بالأجهزة: **{active_cnt}**\n"
        f"🟡 أكواد جديدة في انتظار أول تفعيل: **{pending_cnt}**\n"
        f"⏸️ التراخيص المجمدة مؤقتاً: **{blocked_cnt}**\n\n"
        "👑 **النظام:** ترخيص دائم مدى الحياة لجهاز واحد — أوفلاين 100%."
    )
    bot.send_message(chat_id, stats_text, parse_mode="Markdown")

if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("🚀 تم تشغيل بوت تليجرام لإدارة تراخيص مدى الحياة بنجاح!", flush=True)
    print(f"👑 حساب المدير المعتمد: {ADMIN_CHAT_ID}", flush=True)
    print("🛡️ نظام التراخيص: مدى الحياة (جهاز واحد محمي - أوفلاين 100%)", flush=True)
    print("=" * 60, flush=True)
    bot.infinity_polling()
