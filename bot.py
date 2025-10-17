from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
import datetime
import json
from typing import Dict, List
import logging

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = "8316945407:AAEepiQe2QtOhHgCEfgGRJWL5ygghPiDiEg"

def get_cat_image(points):
    """Возвращает путь к локальной картинке котика"""
    cat_images = {
        0: "1.jpg",      
        250: "2.jpg",    
        500: "3.jpg",    
        750: "4.jpg",   
        1000: "5.jpg",   
        1500: "6.jpg",   
    }

    suitable_levels = [level for level in cat_images.keys() if points >= level]
    if suitable_levels:
        level = max(suitable_levels)
        return cat_images[level]
    else:
        return cat_images[0]

class DeadlineManager:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_points INTEGER DEFAULT 0,
                completed_tasks INTEGER DEFAULT 0,
                created_at TEXT
            )
        ''')
        
        # Таблица задач
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task_name TEXT,
                deadline_date TEXT,
                deadline_time TEXT,
                is_completed BOOLEAN DEFAULT FALSE,
                points_awarded BOOLEAN DEFAULT FALSE,
                created_at TEXT,
                last_notification TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_or_create_user(self, user_id: int, username: str = ""):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        
        if user is None:
            cursor.execute(
                'INSERT INTO users (user_id, username, total_points, completed_tasks, created_at) VALUES (?, ?, ?, ?, ?)',
                (user_id, username, 0, 0, datetime.datetime.now().isoformat())
            )
            conn.commit()
        
        conn.close()
    
    def add_task(self, user_id: int, task_name: str, deadline_date: str, deadline_time: str = "23:59"):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO tasks (user_id, task_name, deadline_date, deadline_time, created_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, task_name, deadline_date, deadline_time, datetime.datetime.now().isoformat())
        )
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return task_id
    
    def complete_task(self, user_id: int, task_id: int):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        # Помечаем задачу как выполненную
        cursor.execute(
            'UPDATE tasks SET is_completed = TRUE WHERE task_id = ? AND user_id = ?',
            (task_id, user_id)
        )
        
        # Начисляем баллы если еще не начисляли
        cursor.execute(
            'SELECT points_awarded FROM tasks WHERE task_id = ?', (task_id,)
        )
        task = cursor.fetchone()
        
        if task and not task[0]:
            # Добавляем баллы
            cursor.execute(
                'UPDATE users SET total_points = total_points + 50, completed_tasks = completed_tasks + 1 WHERE user_id = ?',
                (user_id,)
            )
            # Помечаем что баллы начислены
            cursor.execute(
                'UPDATE tasks SET points_awarded = TRUE WHERE task_id = ?', (task_id,)
            )
        
        conn.commit()
        conn.close()
    
    def get_user_tasks(self, user_id: int, include_completed: bool = False):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        if include_completed:
            cursor.execute(
                'SELECT * FROM tasks WHERE user_id = ? ORDER BY deadline_date, deadline_time',
                (user_id,)
            )
        else:
            cursor.execute(
                'SELECT * FROM tasks WHERE user_id = ? AND is_completed = FALSE ORDER BY deadline_date, deadline_time',
                (user_id,)
            )
        
        tasks = cursor.fetchall()
        conn.close()
        
        return tasks
    
    def get_user_stats(self, user_id: int):
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT total_points, completed_tasks FROM users WHERE user_id = ?', (user_id,))
        user_stats = cursor.fetchone()
        
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE user_id = ? AND is_completed = FALSE', (user_id,))
        active_tasks = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_points': user_stats[0] if user_stats else 0,
            'completed_tasks': user_stats[1] if user_stats else 0,
            'active_tasks': active_tasks
        }
    
    def get_overdue_tasks(self):
        """Получить просроченные задачи, которым нужно отправить уведомление"""
        conn = sqlite3.connect('/data/deadlines.db')
        cursor = conn.cursor()
        
        now = datetime.datetime.now()
        current_date = now.date().isoformat()
        current_time = now.time().strftime('%H:%M')
        
        # Получаем все просроченные задачи
        cursor.execute('''
            SELECT t.task_id, t.user_id, t.task_name, t.deadline_date, t.deadline_time, 
                   u.username, t.last_notification
            FROM tasks t 
            JOIN users u ON t.user_id = u.user_id 
            WHERE t.is_completed = FALSE 
            AND (
                t.deadline_date < ? 
                OR (t.deadline_date = ? AND t.deadline_time < ?)
            )
        ''', (current_date, current_date, current_time))
        
        tasks = cursor.fetchall()
        conn.close()
        
        # Фильтруем задачи: отправляем уведомление только если прошло больше 12 часов
        tasks_to_notify = []
        for task in tasks:
            task_id, user_id, task_name, deadline_date, deadline_time, username, last_notification = task
            
            if last_notification:
                # Проверяем, прошло ли 12 часов с последнего уведомления
                last_notification_time = datetime.datetime.fromisoformat(last_notification)
                time_since_last_notification = now - last_notification_time
                if time_since_last_notification.total_seconds() >= 43200:  # 12 часов в секундах
                    tasks_to_notify.append(task)
            else:
                # Если уведомление никогда не отправлялось
                tasks_to_notify.append(task)
        
        return tasks_to_notify

async def check_deadlines(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для проверки просроченных дедлайнов"""
    try:
        deadline_manager = context.bot_data['deadline_manager']
        overdue_tasks = deadline_manager.get_overdue_tasks()
        
        print(f"🔍 Проверка дедлайнов... Найдено {len(overdue_tasks)} просроченных задач")
        
        for task in overdue_tasks:
            task_id, user_id, task_name, deadline_date, deadline_time, username, last_notification = task
            
            # Отладочная информация
            now = datetime.datetime.now()
            current_date = now.date().isoformat()
            current_time = now.time().strftime('%H:%M')
            
            print(f"📋 Задача {task_id}: {task_name}")
            print(f"   Дедлайн: {deadline_date} {deadline_time}")
            print(f"   Сейчас: {current_date} {current_time}")
            print(f"   Пользователь: {user_id}")
            
            # Форматируем дату для сообщения
            year, month, day = deadline_date.split('-')
            formatted_date = f"{day}.{month}.{year}"
            
            message = (
                f"🚨 *ДЕДЛАЙН!*\n\n"
                f"*Задача:* {task_name}\n"
                f"*Было до:* {formatted_date} ⏰ {deadline_time}\n"
                f"*ID:* {task_id}\n\n"
                f"⚠️ *Завершите задачу!*"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown'
                )
                
                # ОБНОВЛЯЕМ ВРЕМЯ ПОСЛЕДНЕГО УВЕДОМЛЕНИЯ
                conn = sqlite3.connect('/data/deadlines.db')
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE tasks SET last_notification = ? WHERE task_id = ?',
                    (datetime.datetime.now().isoformat(), task_id)
                )
                conn.commit()
                conn.close()
                
                print(f"✅ Отправлено уведомление о просрочке пользователю {user_id}")
                
            except Exception as e:
                print(f"❌ Не удалось отправить уведомление пользователю {user_id}: {e}")
                
    except Exception as e:
        print(f"❌ Ошибка в check_deadlines: {e}")

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("Добавить дедлайн")],
        [KeyboardButton("Завершить дедлайн")],
        [KeyboardButton("Посмотреть все задачи")],
        [KeyboardButton("Посмотреть мой статус")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

# Состояния для диалога добавления задачи
ADDING_TASK, ADDING_DATE, ADDING_TIME = range(3)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start для любых чатов"""
    if update.message.chat.type == 'private':
        # Личный чат - показываем полное меню
        user = update.message.from_user
        deadline_manager = context.bot_data['deadline_manager']
        
        deadline_manager.get_or_create_user(user.id, user.username or user.first_name)
        
        welcome_text = f"""*Привет, {user.first_name}!*

📅 *Система управления дедлайнами*

За каждую выполненную задачу ты получаешь *50 баллов*!

Выбери действие:"""
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    else:
        # Групповой чат - упрощенное сообщение
        await update.message.reply_text(
            "🤖 Бот для управления дедлайнами активирован!\n"
            "Используйте /help для списка команд"
        )

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик личных сообщений"""
    text = update.message.text
    user_id = update.message.from_user.id
    deadline_manager = context.bot_data['deadline_manager']
    
    # Создаем/получаем пользователя
    deadline_manager.get_or_create_user(user_id, update.message.from_user.username or update.message.from_user.first_name)
    
    if text == "Добавить дедлайн":
        await update.message.reply_text(
            "📝 *Введите название задачи:*\n\n"
            "Пример: 'Сдать проект по Python'",
            parse_mode='Markdown'
        )
        context.user_data['state'] = ADDING_TASK
    
    elif text == "Завершить дедлайн":
        tasks = deadline_manager.get_user_tasks(user_id, include_completed=False)
        
        if not tasks:
            await update.message.reply_text(
                "✅ У вас нет активных задач для завершения!",
                reply_markup=get_main_keyboard()
            )
            return
        
        tasks_text = "📋 *Ваши активные задачи:*\n\n"
        for i, task in enumerate(tasks, 1):
            task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
            tasks_text += f"{i}. *{task_name}*\n"
            tasks_text += f"   📅 {deadline_date} ⏰ {deadline_time}\n"
            tasks_text += f"   🆔 *ID*: {task_id}\n\n"
        
        tasks_text += "*Введите ID задачи для завершения:*"
        
        await update.message.reply_text(
            tasks_text,
            parse_mode='Markdown'
        )
        context.user_data['state'] = 'completing_task'
    
    elif text == "Посмотреть все задачи":
        tasks = deadline_manager.get_user_tasks(user_id, include_completed=True)
        
        if not tasks:
            await update.message.reply_text(
                "У вас пока нет задач!",
                reply_markup=get_main_keyboard()
            )
            return
        
        active_tasks = []
        completed_tasks = []
        
        for task in tasks:
            task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
            if is_completed:
                completed_tasks.append(task)
            else:
                active_tasks.append(task)
        
        response_text = "*Все ваши задачи:*\n\n"
        
        if active_tasks:
            response_text += "*АКТИВНЫЕ ЗАДАЧИ:*\n"
            for task in active_tasks:
                task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
                response_text += f"• {task_name}\n"
                response_text += f"  📅 {deadline_date} ⏰ {deadline_time}\n"
                response_text += f"  🆔 *ID*: {task_id}\n\n"
        
        if completed_tasks:
            response_text += "*ВЫПОЛНЕННЫЕ ЗАДАЧИ:*\n"
            for task in completed_tasks:
                task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
                status = "+50 баллов" if points_awarded else "Выполнено"
                response_text += f"• {task_name} - {status}\n"
        
        await update.message.reply_text(
            response_text,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
    elif text == "Посмотреть мой статус":
        stats = deadline_manager.get_user_stats(user_id)
        points = stats['total_points']
        
        # Получаем картинку котика
        cat_image_path = get_cat_image(points)
        
        # Определяем уровень и сообщение
        if points >= 1500:
            level = "🐱 Котик-Легенда 🏆"
            message = "Вы достигли вершины! Ваши достижения вдохновляют! ✨"
            next_goal = "Вы достигли максимального уровня!"
        elif points >= 1000:
            level = "🐱 Супер-Котик ⭐"
            message = "Невероятно! Вы просто супер! 🌟"
            next_goal = "1500 баллов - стань Легендой!"
        elif points >= 750:
            level = "🐱 Продвинутый Котик 💪"
            message = "Отличные результаты! Так держать! 💪"
            next_goal = "1000 баллов - стань Супер-Котиком!"
        elif points >= 500:
            level = "🐱 Уверенный Котик 😊"
            message = "Вы на правильном пути! Продолжайте! 🚀"
            next_goal = "750 баллов - стань Продвинутым!"
        elif points >= 250:
            level = "🐱 Начинающий Котик 🐾"
            message = "Хорошее начало! Вы уже многого достигли! 🌈"
            next_goal = "500 баллов - стань Уверенным!"
        else:
            level = "🐱 Малыш-Котик 🌱"
            message = "Каждое большое достижение начинается с маленького шага! 🌟"
            next_goal = "250 баллов - стань Начинающим!"
        
        status_text = f"""🏆 *Ваш статус*

{level}

{message}

🎯 *Выполнено задач:* {stats['completed_tasks']}
📊 *Активных задач:* {stats['active_tasks']}
💰 *Всего баллов:* {stats['total_points']}

💡 *Следующая цель:*
{next_goal}

🐾 *Продолжайте в том же духе! Каждая задача приближает вас к новому уровню!* 🎁"""
        
        try:
            # Отправляем картинку с подписью
            with open(cat_image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=status_text,
                    reply_markup=get_main_keyboard(),
                    parse_mode='Markdown'
                )
        except Exception as e:
            # Если не удалось отправить картинку, отправляем только текст
            print(f"Ошибка при отправке картинки: {e}")
            await update.message.reply_text(
                status_text,
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
    
    else:
        # Обработка состояний диалога
        state = context.user_data.get('state')
        
        if state == ADDING_TASK:
            context.user_data['task_name'] = text
            await update.message.reply_text(
                "📅 *Введите дату дедлайна (ДД.ММ.ГГГГ):*\n\n"
                "Пример: 25.12.2024",
                parse_mode='Markdown'
            )
            context.user_data['state'] = ADDING_DATE
        
        elif state == ADDING_DATE:
            try:
                # Проверяем корректность даты
                parts = text.split('.')
                if len(parts) != 3:
                    raise ValueError("Неверный формат даты")
        
                day, month, year = map(int, parts)
        
                # Исправляем год если он в коротком формате
                if year < 100:
                    if year < 50:  # 00-49 -> 2000-2049
                        year += 2000
                    else:  # 50-99 -> 1950-1999
                        year += 1900
        
                # Проверяем корректность даты
                deadline_date_obj = datetime.date(year, month, day)
                deadline_date = deadline_date_obj.isoformat()
        
                context.user_data['deadline_date'] = deadline_date
                
                await update.message.reply_text(
                    "⏰ *Введите время дедлайна (ЧЧ:ММ):*\n\n"
                    "Пример: 18:30\n"
                    "Или отправьте 'нет' для времени по умолчанию (23:59)",
                    parse_mode='Markdown'
                )
                context.user_data['state'] = ADDING_TIME
            except:
                await update.message.reply_text(
                    "❌ *Неверный формат даты!*\n"
                    "Пожалуйста, введите дату в формате ДД.ММ.ГГГГ\n"
                    "Пример: 25.12.2024",
                    parse_mode='Markdown'
                )
        
        elif state == ADDING_TIME:
            deadline_time = "23:59"
            if text.lower() != 'нет' and ':' in text:
                try:
                    hours, minutes = map(int, text.split(':'))
                    if 0 <= hours <= 23 and 0 <= minutes <= 59:
                        deadline_time = f"{hours:02d}:{minutes:02d}"
                except:
                    pass
            
            # Сохраняем задачу
            task_name = context.user_data['task_name']
            deadline_date = context.user_data['deadline_date']
            
            task_id = deadline_manager.add_task(
                user_id=user_id,
                task_name=task_name,
                deadline_date=deadline_date,
                deadline_time=deadline_time
            )
            
            # Форматируем дату для вывода
            year, month, day = deadline_date.split('-')
            formatted_date = f"{day}.{month}.{year}"
            
            await update.message.reply_text(
                f"*Задача добавлена!*\n\n"
                f"*Название:* {task_name}\n"
                f"📅 *Дата:* {formatted_date}\n"
                f"⏰ *Время:* {deadline_time}\n"
                f"🆔 *ID задачи:* {task_id}\n\n"
                f"*Не забудьте завершить задачу вовремя для получения 50 баллов!*",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
            context.user_data.clear()
        
        elif state == 'completing_task':
            try:
                task_id = int(text)
                tasks = deadline_manager.get_user_tasks(user_id, include_completed=False)
                task_ids = [task[0] for task in tasks]
                
                if task_id in task_ids:
                    deadline_manager.complete_task(user_id, task_id)
                    await update.message.reply_text(
                        f"🎉 *Задача завершена!*\n\n"
                        f"✅ +50 баллов начислено на ваш счет!\n"
                        f"🆔 ID задачи: {task_id}",
                        reply_markup=get_main_keyboard(),
                        parse_mode='Markdown'
                    )
                    context.user_data.clear()
                else:
                    await update.message.reply_text(
                        "❌ *Задача не найдена!*\n"
                        "Пожалуйста, введите корректный ID задачи из списка:",
                        parse_mode='Markdown'
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ *Неверный формат!*\n"
                    "Пожалуйста, введите числовой ID задачи:",
                    parse_mode='Markdown'
                )
        
        else:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки меню для навигации 👆",
                reply_markup=get_main_keyboard()
            )

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений в групповых чатах"""
    text = update.message.text
    user_id = update.message.from_user.id
    bot_username = context.bot.username
    
    # Инициализируем менеджер дедлайнов
    deadline_manager = context.bot_data['deadline_manager']
    
    # Создаем/получаем пользователя
    deadline_manager.get_or_create_user(user_id, update.message.from_user.username or update.message.from_user.first_name)
    
    # Бот реагирует на команды или упоминания
    if text.startswith('/') or (bot_username and f"@{bot_username}" in text):
        
        if '/start' in text or 'начать' in text.lower():
            welcome_text = f"""🤖 *Привет, {update.message.from_user.first_name}!*

Я бот для управления дедлайнами! 🎯

*Команды для группы:*
/add_deadline - добавить дедлайн
/my_tasks - мои задачи  
/my_stats - моя статистика
/help - помощь

*Или напишите мне в личные сообщения для полного функционала!* 💬"""
            
            await update.message.reply_text(welcome_text, parse_mode='Markdown')
        
        elif '/add_deadline' in text or 'добавить дедлайн' in text.lower():
            await update.message.reply_text(
                f"Чтобы добавить дедлайн, напишите мне в личные сообщения: @{bot_username}\n"
                "Там вы получите удобное меню с кнопками! 🎯"
            )
        
        elif '/my_tasks' in text or 'мои задачи' in text.lower():
            tasks = deadline_manager.get_user_tasks(user_id, include_completed=False)
            
            if not tasks:
                await update.message.reply_text(
                    f"📭 {update.message.from_user.first_name}, у вас пока нет активных задач!",
                    parse_mode='Markdown'
                )
                return
            
            tasks_text = f"📋 *Задачи {update.message.from_user.first_name}:*\n\n"
            for i, task in enumerate(tasks, 1):
                task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
                tasks_text += f"{i}. *{task_name}*\n"
                tasks_text += f"   📅 {deadline_date} ⏰ {deadline_time}\n"
                tasks_text += f"   🆔 ID: {task_id}\n\n"
            
            await update.message.reply_text(tasks_text, parse_mode='Markdown')
        
        elif '/my_stats' in text or 'статистика' in text.lower():
            stats = deadline_manager.get_user_stats(user_id)
            
            status_text = f"""*Статистика {update.message.from_user.first_name}:*

🎯 *Выполнено задач:* {stats['completed_tasks']}
📊 *Активных задач:* {stats['active_tasks']}
💰 *Всего баллов:* {stats['total_points']}

🐱 *Продолжайте в том же духе!* 🎁"""
            
            await update.message.reply_text(status_text, parse_mode='Markdown')
        
        elif '/help' in text or 'помощь' in text.lower():
            help_text = """🤖 *Помощь по командам:*

*В группе:*
/add_deadline - добавить дедлайн
/my_tasks - мои задачи
/my_stats - моя статистика
/help - помощь

*Для полного функционала* напишите боту в личные сообщения! 💬
Там вас ждет удобное меню с кнопками! 🎯"""
            
            await update.message.reply_text(help_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """🤖 *Помощь по командам:*

*В группе:*
/add_deadline - добавить дедлайн
/my_tasks - мои задачи
/my_stats - моя статистика
/help - помощь

*Для полного функционала* напишите боту в личные сообщения! 💬
Там вас ждет удобное меню с кнопками! 🎯"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def my_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /my_tasks"""
    user_id = update.message.from_user.id
    deadline_manager = context.bot_data['deadline_manager']
    
    # Создаем/получаем пользователя
    deadline_manager.get_or_create_user(user_id, update.message.from_user.username or update.message.from_user.first_name)
    
    tasks = deadline_manager.get_user_tasks(user_id, include_completed=False)
    
    if not tasks:
        await update.message.reply_text(
            f"📭 {update.message.from_user.first_name}, у вас пока нет активных задач!",
            parse_mode='Markdown'
        )
        return
    
    tasks_text = f"📋 *Задачи {update.message.from_user.first_name}:*\n\n"
    for i, task in enumerate(tasks, 1):
        task_id, _, task_name, deadline_date, deadline_time, is_completed, points_awarded, created_at, last_notification = task
        tasks_text += f"{i}. *{task_name}*\n"
        tasks_text += f"   📅 {deadline_date} ⏰ {deadline_time}\n"
        tasks_text += f"   🆔 ID: {task_id}\n\n"
    
    await update.message.reply_text(tasks_text, parse_mode='Markdown')

async def my_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /my_stats"""
    user_id = update.message.from_user.id
    deadline_manager = context.bot_data['deadline_manager']
    
    # Создаем/получаем пользователя
    deadline_manager.get_or_create_user(user_id, update.message.from_user.username or update.message.from_user.first_name)
    
    stats = deadline_manager.get_user_stats(user_id)
    
    status_text = f"""*Статистика {update.message.from_user.first_name}:*

🎯 *Выполнено задач:* {stats['completed_tasks']}
📊 *Активных задач:* {stats['active_tasks']}
💰 *Всего баллов:* {stats['total_points']}

🐱 *Продолжайте в том же духе!* 🎁"""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def add_deadline_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add_deadline"""
    bot_username = context.bot.username
    await update.message.reply_text(
        f"Чтобы добавить дедлайн, напишите мне в личные сообщения: @{bot_username}\n"
        "Там вы получите удобное меню с кнопками! 🎯"
    )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.bot_data['deadline_manager'] = DeadlineManager()
    
    # Обработчики команд для всех чатов
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_tasks", my_tasks_command))
    application.add_handler(CommandHandler("my_stats", my_stats_command))
    application.add_handler(CommandHandler("add_deadline", add_deadline_command))
    
    # Обработчики для личных сообщений (кнопки)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, 
        handle_private_message
    ))
    
    # Обработчики для групповых чатов (текстовые сообщения)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), 
        handle_group_message
    ))
    
    # Фоновая задача для проверки дедлайнов
    job_queue = application.job_queue
    job_queue.run_repeating(
        callback=check_deadlines,
        interval=60,
        first=10
    )
    
    print("Бот запущен и готов к работе в личных и групповых чатах!")
    application.run_polling()

if __name__ == "__main__":
    main()

# # Токен вашего бота
# BOT_TOKEN = "8316945407:AAEepiQe2QtOhHgCEfgGRJWL5ygghPiDiEg"



