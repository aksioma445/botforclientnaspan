import telebot
import json
import time
import threading
import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from datetime import datetime
from pytz import timezone

BOT_TOKEN = '7898364358:AAGaivTXDQeXoo4D-1PTok3T2s2ulkLwiwI'
bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = 'bot_data.json'
KYIV_TZ = timezone('Europe/Kyiv')  # Київський часовий пояс

# Зберігання стану
selected_account = {}
scheduled_pending = {}
delete_pending = {}
text_indices = {}

def init_json():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                data = {
                    'accounts': {},
                    'texts': {},
                    'groups': {},
                    'schedules': {},
                    'spam_times': {},
                    'spam_active': {},
                    'admins': [8037144017]  # Початковий адмін
                }
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {
            'accounts': {},
            'texts': {},
            'groups': {},
            'schedules': {},
            'spam_times': {},
            'spam_active': {},
            'admins': [8037144017]  # Початковий адмін
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return data

class AccountManager:
    def __init__(self):
        self.data = init_json()
        self.clients = {}
        self.auth_pending = {}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.run_loop, daemon=True).start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def save_data(self):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False)

    async def start_client(self, account_id, chat_id=None):
        if account_id not in self.data['accounts']:
            return False
        account = self.data['accounts'][account_id]
        client = TelegramClient(f'session_{account_id}', account['api_id'], account['api_hash'])

        await client.connect()
        if not await client.is_user_authorized():
            phone = account.get('phone')
            if phone and chat_id:
                try:
                    await client.send_code_request(phone)
                    self.auth_pending[account_id] = {'client': client, 'chat_id': chat_id, 'step': 'code'}
                    bot.send_message(chat_id, f"🔑 Вам надіслано код для авторизації {account_id}. Введіть його:")
                    return False
                except Exception as e:
                    bot.send_message(chat_id, f"⚠️ Помилка авторизації {account_id}: {str(e)}")
                    return False
            return False

        self.clients[account_id] = client
        return True

    async def send_message(self, account_id, text, group=None):
        if account_id not in self.clients or not self.clients[account_id].is_connected():
            await self.start_client(account_id)

        client = self.clients[account_id]
        async with client:
            if group:
                await client.send_message(group, text)
            else:
                dialogs = await client.get_dialogs()
                groups = [d for d in dialogs if d.is_group]
                for g in groups:
                    await client.send_message(g.entity, text)
                    await asyncio.sleep(2)  # Фіксований інтервал між групами

manager = AccountManager()

def is_admin(chat_id):
    return chat_id in manager.data['admins']

def handle_auth(message):
    account_id = next((acc for acc in manager.auth_pending if manager.auth_pending[acc]['chat_id'] == message.chat.id), None)
    if not account_id:
        return False

    auth_data = manager.auth_pending[account_id]
    client = auth_data['client']
    chat_id = auth_data['chat_id']

    if auth_data['step'] == 'code':
        try:
            asyncio.run_coroutine_threadsafe(client.sign_in(code=message.text), manager.loop).result()
            password = manager.data['accounts'][account_id].get('password')
            if password:
                try:
                    asyncio.run_coroutine_threadsafe(client.sign_in(password=password), manager.loop).result()
                except SessionPasswordNeededError:
                    auth_data['step'] = 'password'
                    bot.send_message(chat_id, f"🔒 Введіть пароль для {account_id} (2FA):")
                    return True
            auth_data['step'] = 'done'
            bot.send_message(chat_id, f"✅ Авторизація {account_id} успішна!")
            manager.clients[account_id] = client
            del manager.auth_pending[account_id]
            manager.save_data()
        except SessionPasswordNeededError:
            auth_data['step'] = 'password'
            bot.send_message(chat_id, f"🔒 Введіть пароль для {account_id} (2FA):")
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Помилка введення коду: {str(e)}")
        return True

    elif auth_data['step'] == 'password':
        try:
            asyncio.run_coroutine_threadsafe(client.sign_in(password=message.text), manager.loop).result()
            manager.data['accounts'][account_id]['password'] = message.text
            auth_data['step'] = 'done'
            bot.send_message(chat_id, f"✅ Авторизація {account_id} успішна!")
            manager.clients[account_id] = client
            del manager.auth_pending[account_id]
            manager.save_data()
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Помилка введення пароля: {str(e)}")
        return True

    return False

def authorize_all_accounts():
    for account_id in manager.data['accounts']:
        if account_id not in manager.clients:
            asyncio.run_coroutine_threadsafe(manager.start_client(account_id, manager.data['admins'][0]), manager.loop).result()

def main_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Статистика', '⚙️ Керування акаунтами')
    markup.add('📝 Тексти')
    return markup

def account_menu(account_id):
    spam_status = "✅ Вкл" if manager.data['spam_active'].get(account_id, False) else "❌ Викл"
    groups_mode = "🌐 Всі" if not manager.data['groups'].get(account_id) else "🎯 Окремі"
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(f'Авто-спам: {spam_status}')
    markup.add('Додати час для спаму', 'Видалити час для авто-спаму')
    markup.add(f'Групи: {groups_mode}', 'Перемкнути режим груп')
    markup.add('Додати групи', 'Видалити групи')
    markup.add('⏰ Запланувати відправку')
    markup.add('⬅️ Назад')
    return markup

def run_scheduled_message(account_id, text):
    if not manager.data['spam_active'].get(account_id, False):
        return
    groups = manager.data['groups'].get(account_id, [])
    if groups:
        for group_id in groups:
            asyncio.run_coroutine_threadsafe(manager.send_message(account_id, text, group_id), manager.loop).result()
    else:
        asyncio.run_coroutine_threadsafe(manager.send_message(account_id, text), manager.loop).result()

def spam_loop():
    global text_indices
    while True:
        now = datetime.now(KYIV_TZ)
        current_time_str = now.strftime('%H:%M')

        for account_id in manager.data['accounts']:
            if not manager.data['spam_active'].get(account_id, False):
                continue
            spam_times = manager.data['spam_times'].get(account_id, [])
            if current_time_str in spam_times:
                texts = manager.data['texts'].get(account_id, [])
                if texts:
                    text_index = text_indices.get(account_id, 0) % len(texts)
                    text = texts[text_index]
                    run_scheduled_message(account_id, text)
                    text_indices[account_id] = text_index + 1

            if account_id in manager.data['schedules'] and 'time' in manager.data['schedules'][account_id]:
                sched = manager.data['schedules'][account_id]
                sched_time = datetime.strptime(sched['time'], '%d.%m.%Y %H:%M').astimezone(KYIV_TZ)
                if now >= sched_time:
                    texts = manager.data['texts'].get(account_id, [])
                    if texts and sched['text'] in texts:
                        run_scheduled_message(account_id, sched['text'])
                    del manager.data['schedules'][account_id]['time']
                    manager.save_data()

        time.sleep(60)

@bot.message_handler(commands=['start'])
def start(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Ви не маєте доступу до цього бота!")
        return
    bot.send_message(message.chat.id, "👋 Вітаю в адмін-боті! Оберіть дію:", reply_markup=main_menu())

@bot.message_handler(commands=['add_admin'])
def add_admin(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Тільки адміни можуть додавати інших адмінів!")
        return
    try:
        new_admin_id = int(message.text.split()[1])
        if new_admin_id not in manager.data['admins']:
            manager.data['admins'].append(new_admin_id)
            manager.save_data()
            bot.send_message(message.chat.id, f"✅ Адмін {new_admin_id} додано!")
        else:
            bot.send_message(message.chat.id, f"⚠️ Користувач {new_admin_id} уже є адміном!")
    except (IndexError, ValueError):
        bot.send_message(message.chat.id, "⚠️ Вкажіть ID нового адміна після команди! Наприклад: /add_admin 123456789")

@bot.message_handler(commands=['remove_admin'])
def remove_admin(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Тільки адміни можуть видаляти інших адмінів!")
        return
    try:
        admin_id = int(message.text.split()[1])
        if admin_id in manager.data['admins']:
            if len(manager.data['admins']) <= 1:
                bot.send_message(message.chat.id, "⚠️ Не можна видалити останнього адміна!")
            elif admin_id == message.chat.id:
                bot.send_message(message.chat.id, "⚠️ Ви не можете видалити себе!")
            else:
                manager.data['admins'].remove(admin_id)
                manager.save_data()
                bot.send_message(message.chat.id, f"✅ Адмін {admin_id} видалено!")
        else:
            bot.send_message(message.chat.id, f"⚠️ Користувач {admin_id} не є адміном!")
    except (IndexError, ValueError):
        bot.send_message(message.chat.id, "⚠️ Вкажіть ID адміна для видалення! Наприклад: /remove_admin 123456789")

@bot.message_handler(commands=['list_admins'])
def list_admins(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Тільки адміни можуть переглядати список адмінів!")
        return
    admins = "\n".join(str(admin_id) for admin_id in manager.data['admins'])
    bot.send_message(message.chat.id, f"📋 Список адмінів:\n{admins}")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Ви не маєте доступу до цього бота!")
        return

    if handle_auth(message):
        return

    chat_id = message.chat.id
    text = message.text

    if chat_id in scheduled_pending:
        account_id = scheduled_pending[chat_id]['account_id']
        time_str = scheduled_pending[chat_id]['time_str']
        try:
            text_num = int(text) - 1
            texts = manager.data['texts'].get(account_id, [])
            if not (0 <= text_num < len(texts)):
                raise ValueError("Номер тексту поза межами!")
            manager.data['schedules'][account_id] = {'time': time_str, 'text': texts[text_num]}
            manager.save_data()
            bot.send_message(chat_id, f"✅ Відправка для {account_id} запланована на {time_str}!",
                             reply_markup=account_menu(account_id))
            del scheduled_pending[chat_id]
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Помилка: {str(e)}. Введіть правильний номер тексту!",
                             reply_markup=account_menu(account_id))
        return

    if chat_id in delete_pending:
        account_id = delete_pending[chat_id]['account_id']
        step = delete_pending[chat_id]['step']

        if step == 'select':
            try:
                time_num = int(text) - 1
                spam_times = manager.data['spam_times'].get(account_id, [])
                if not (0 <= time_num < len(spam_times)):
                    raise ValueError("Номер часу поза межами!")
                delete_pending[chat_id] = {'account_id': account_id, 'step': 'confirm',
                                           'time_to_delete': spam_times[time_num]}
                bot.send_message(chat_id,
                                 f"Ви впевнені, що хочете видалити час {spam_times[time_num]} для {account_id}? (Так/Ні)")
            except Exception as e:
                bot.send_message(chat_id, f"⚠️ Помилка: {str(e)}. Введіть правильний номер!",
                                 reply_markup=account_menu(account_id))
                del delete_pending[chat_id]
        elif step == 'confirm':
            if text.lower() == 'так':
                spam_times = manager.data['spam_times'][account_id]
                time_to_delete = delete_pending[chat_id]['time_to_delete']
                spam_times.remove(time_to_delete)
                if not spam_times:
                    del manager.data['spam_times'][account_id]
                manager.save_data()
                bot.send_message(chat_id, f"✅ Час {time_to_delete} видалено для {account_id}!",
                                 reply_markup=account_menu(account_id))
            else:
                bot.send_message(chat_id, f"❌ Видалення скасовано!", reply_markup=account_menu(account_id))
            del delete_pending[chat_id]
        return

    if text == '📊 Статистика':
        total = len(manager.data['accounts'])
        active_spam = sum(1 for acc in manager.data['spam_active'] if manager.data['spam_active'][acc])
        bot.send_message(chat_id, f"📊 Статистика:\nАкаунтів: {total}\nЗ авто-спамом: {active_spam}",
                         reply_markup=main_menu())

    elif text == '⚙️ Керування акаунтами':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        for i, acc in enumerate(manager.data['accounts'].keys(), 1):
            markup.add(f'👤 Акаунт {i} ({acc})')
        markup.add('⬅️ Назад')
        bot.send_message(chat_id, "Виберіть акаунт:", reply_markup=markup)

    elif text.startswith('👤 Акаунт'):
        try:
            acc_num = int(text.split()[2].split('(')[0]) - 1
            account_id = list(manager.data['accounts'].keys())[acc_num]
            if account_id in manager.clients:
                selected_account[chat_id] = account_id
                bot.send_message(chat_id, f"⚙️ Налаштування {account_id}", reply_markup=account_menu(account_id))
            else:
                bot.send_message(chat_id,
                                 f"⚠️ Акаунт {account_id} ще не авторизований. Зачекайте завершення авторизації.")
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Помилка вибору: {str(e)}", reply_markup=main_menu())

    elif text.startswith('Авто-спам:'):
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            manager.data['spam_active'][account_id] = not manager.data['spam_active'].get(account_id, False)
            status = "увімкнено" if manager.data['spam_active'][account_id] else "вимкнено"
            bot.send_message(chat_id, f"✅ Авто-спам для {account_id} {status}!", reply_markup=account_menu(account_id))
            manager.save_data()
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == 'Додати час для спаму':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            bot.send_message(chat_id, f"⏰ Введіть час для авто-спаму {account_id} у форматі ГГ:ХХ (наприклад, 06:30):")
            bot.register_next_step_handler(message, process_add_spam_time, account_id)
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == 'Видалити час для авто-спаму':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            spam_times = manager.data['spam_times'].get(account_id, [])
            if not spam_times:
                bot.send_message(chat_id, f"⚠️ Немає часу для авто-спаму для {account_id}!",
                                 reply_markup=account_menu(account_id))
            else:
                times_list = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(spam_times))
                bot.send_message(chat_id, f"⏰ Виберіть номер часу для видалення з {account_id}:\n{times_list}")
                delete_pending[chat_id] = {'account_id': account_id, 'step': 'select'}
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == 'Перемкнути режим груп':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            if manager.data['groups'].get(account_id):
                del manager.data['groups'][account_id]
                manager.save_data()
                bot.send_message(chat_id, f"✅ Переключено на 'Всі групи' для {account_id}",
                                 reply_markup=account_menu(account_id))
            else:
                bot.send_message(chat_id,
                                 f"📋 Введіть ID груп для {account_id} через кому (наприклад, -100123456789, -100987654321):")
                bot.register_next_step_handler(message, process_groups, account_id)
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == 'Додати групи':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            bot.send_message(chat_id,
                             f"📋 Введіть ID груп для додавання до {account_id} через кому (наприклад, -100123456789, -100987654321):")
            bot.register_next_step_handler(message, process_add_groups, account_id)
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == 'Видалити групи':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            if manager.data['groups'].get(account_id):
                bot.send_message(chat_id,
                                 f"📋 Введіть ID груп для видалення з {account_id} через кому (наприклад, -100123456789, -100987654321):")
                bot.register_next_step_handler(message, process_remove_groups, account_id)
            else:
                bot.send_message(chat_id, f"⚠️ Немає окремих груп для видалення!",
                                 reply_markup=account_menu(account_id))
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == '⏰ Запланувати відправку':
        account_id = selected_account.get(chat_id)
        if account_id and account_id in manager.clients:
            texts = manager.data['texts'].get(account_id, [])
            if not texts:
                bot.send_message(chat_id, f"⚠️ Додайте тексти для {account_id} спочатку!",
                                 reply_markup=account_menu(account_id))
            else:
                bot.send_message(chat_id, f"⏰ Введіть час для {account_id} у форматі ДД.ММ.РРРР ГГ:ХХ:")
                bot.send_message(chat_id, f"Тексти:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
                bot.register_next_step_handler(message, process_schedule_step1, account_id)
        else:
            bot.send_message(chat_id, "⚠️ Виберіть акаунт спочатку!", reply_markup=main_menu())

    elif text == '📝 Тексти':
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        for i, acc in enumerate(manager.data['accounts'].keys(), 1):
            markup.add(f'✍️ Тексти для {acc}')
        markup.add('⬅️ Назад')
        bot.send_message(chat_id, "Виберіть акаунт для налаштування текстів:", reply_markup=markup)

    elif text.startswith('✍️ Тексти для'):
        account_id = text.split('✍️ Тексти для ')[1]
        if account_id in manager.data['accounts']:
            bot.send_message(chat_id, f"📝 Введіть один текст для {account_id}:")
            bot.register_next_step_handler(message, process_add_text, account_id)
        else:
            bot.send_message(chat_id, "⚠️ Акаунт не знайдено!", reply_markup=main_menu())

    elif text == '⬅️ Назад':
        if chat_id in selected_account:
            del selected_account[chat_id]
        bot.send_message(chat_id, "Повернення до головного меню", reply_markup=main_menu())

def process_add_spam_time(message, account_id):
    try:
        time_str = message.text.strip()
        datetime.strptime(time_str, '%H:%M')
        spam_times = manager.data['spam_times'].get(account_id, [])
        if time_str not in spam_times:
            spam_times.append(time_str)
            manager.data['spam_times'][account_id] = sorted(spam_times)
            manager.save_data()
            bot.send_message(message.chat.id, f"✅ Час {time_str} додано для авто-спаму {account_id}!",
                             reply_markup=account_menu(account_id))
        else:
            bot.send_message(message.chat.id, f"⚠️ Час {time_str} уже є для {account_id}!",
                             reply_markup=account_menu(account_id))
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка формату: {str(e)}. Використовуйте ГГ:ХХ",
                         reply_markup=account_menu(account_id))

def process_groups(message, account_id):
    try:
        group_ids = [int(gid.strip()) for gid in message.text.split(',')]
        manager.data['groups'][account_id] = group_ids
        manager.save_data()
        bot.send_message(message.chat.id, f"✅ Групи для {account_id} налаштовано!",
                         reply_markup=account_menu(account_id))
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка: {str(e)}", reply_markup=account_menu(account_id))

def process_add_groups(message, account_id):
    try:
        group_ids = [int(gid.strip()) for gid in message.text.split(',')]
        current_groups = manager.data['groups'].get(account_id, [])
        manager.data['groups'][account_id] = list(set(current_groups + group_ids))
        manager.save_data()
        bot.send_message(message.chat.id, f"✅ Групи додано до {account_id}!", reply_markup=account_menu(account_id))
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка: {str(e)}", reply_markup=account_menu(account_id))

def process_remove_groups(message, account_id):
    try:
        group_ids = [int(gid.strip()) for gid in message.text.split(',')]
        current_groups = manager.data['groups'].get(account_id, [])
        manager.data['groups'][account_id] = [gid for gid in current_groups if gid not in group_ids]
        if not manager.data['groups'][account_id]:
            del manager.data['groups'][account_id]
        manager.save_data()
        bot.send_message(message.chat.id, f"✅ Групи видалено з {account_id}!", reply_markup=account_menu(account_id))
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка: {str(e)}", reply_markup=account_menu(account_id))

def process_schedule_step1(message, account_id):
    try:
        time_str = message.text.strip()
        datetime.strptime(time_str, '%d.%m.%Y %H:%M')
        scheduled_pending[message.chat.id] = {'account_id': account_id, 'time_str': time_str}
        bot.send_message(message.chat.id, "📝 Введіть номер тексту з бази:")
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка формату: {str(e)}. Використовуйте ДД.ММ.РРРР ГГ:ХХ",
                         reply_markup=account_menu(account_id))

def process_add_text(message, account_id):
    try:
        new_text = message.text.strip()
        if not new_text:
            raise ValueError("Текст не може бути порожнім!")
        texts = manager.data['texts'].get(account_id, [])
        texts.append(new_text)
        manager.data['texts'][account_id] = texts
        manager.save_data()
        text_number = len(texts)
        bot.send_message(message.chat.id,
                         f"✅ Ваш текст виглядає ось так: '{new_text}'. Додано з номером {text_number}!",
                         reply_markup=main_menu())
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Помилка: {str(e)}", reply_markup=main_menu())

if __name__ == '__main__':
    threading.Thread(target=authorize_all_accounts, daemon=True).start()
    threading.Thread(target=spam_loop, daemon=True).start()
    bot.polling(none_stop=True)
