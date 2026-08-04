import asyncio
import re
import os
from aiohttp import web
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart, Command

# ================= НАЛАШТУВАННЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [6132348011, 965741347, 484191739]  

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
pool = None  # Глобальний пул з'єднань з БД

# ================= СТАНИ =================
class ContactAdmin(StatesGroup):
    waiting_for_message = State()

class AddFAQ(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()
    
class EditFAQ(StatesGroup):
    waiting_for_new_question = State()
    waiting_for_new_answer = State()
    
class ReplyFromPanel(StatesGroup):
    waiting_for_reply = State()

# ================= БАЗА ДАНИХ =================
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

# ================= СТАТИЧНІ КЛАВІАТУРИ =================
def get_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Часті питання (FAQ)", callback_data="faq_menu")],
        [InlineKeyboardButton(text="✉️ Написати адміністратору", callback_data="contact_admin")]
    ])

def get_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад до питань", callback_data="faq_menu")]
    ])
    
def get_persistent_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Головне меню")]],
        resize_keyboard=True
    )

# ================= БАЗОВІ КОМАНДИ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привіт! 👋 Я бот-помічник.", reply_markup=get_persistent_kb())
    await message.answer("Обери потрібний розділ нижче:", reply_markup=get_main_kb())

@router.message(F.text == "🏠 Головне меню")
async def show_main_menu_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Головне меню. Обери потрібний розділ:", reply_markup=get_main_kb())

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Головне меню:", reply_markup=get_main_kb())

# ================= ЛОГІКА FAQ =================
@router.callback_query(F.data == "faq_menu")
async def open_faq(callback: CallbackQuery):
    async with pool.acquire() as conn:
        faqs = await conn.fetch("SELECT id, question FROM faq ORDER BY id ASC")
        
    keyboard = []
    for f in faqs:
        keyboard.append([InlineKeyboardButton(text=f['question'], callback_data=f"faq_{f['id']}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
    
    await callback.message.edit_text("Ось популярні питання:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.callback_query(F.data.startswith("faq_") & (F.data != "faq_menu"))
async def show_faq_answer(callback: CallbackQuery):
    faq_id = int(callback.data.replace("faq_", ""))
    async with pool.acquire() as conn:
        record = await conn.fetchrow("SELECT answer FROM faq WHERE id = $1", faq_id)
        
    if record:
        await callback.message.edit_text(f"ℹ️ {record['answer']}", reply_markup=get_back_kb())

# ================= ПАНЕЛЬ АДМІНІСТРАТОРА =================
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_panel(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати нове питання", callback_data="admin_add")],
        [InlineKeyboardButton(text="⚙️ Редагувати / Видалити", callback_data="admin_manage")],
        [InlineKeyboardButton(text="📥 Нерозв'язані питання", callback_data="admin_pending_tickets")]
    ])
    await message.answer("🛠 <b>Панель адміністратора</b>\nОбери дію:", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_back_to_panel", F.from_user.id.in_(ADMIN_IDS))
async def admin_back_to_panel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати нове питання", callback_data="admin_add")],
        [InlineKeyboardButton(text="⚙️ Редагувати / Видалити", callback_data="admin_manage")],
        [InlineKeyboardButton(text="📥 Нерозв'язані питання", callback_data="admin_pending_tickets")]
    ])
    await callback.message.edit_text("🛠 <b>Панель адміністратора</b>\nОбери дію:", reply_markup=kb, parse_mode="HTML")

# ================= ДОДАВАННЯ FAQ =================
@router.callback_query(F.data == "admin_add", F.from_user.id.in_(ADMIN_IDS))
async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введи текст НОВОГО ПИТАННЯ (те, що буде на кнопці):")
    await state.set_state(AddFAQ.waiting_for_question)

@router.message(AddFAQ.waiting_for_question, F.text)
async def process_faq_question(message: Message, state: FSMContext):
    await state.update_data(question=message.text)
    await message.answer("Тепер введи ВІДПОВІДЬ на це питання:")
    await state.set_state(AddFAQ.waiting_for_answer)

@router.message(AddFAQ.waiting_for_answer, F.text)
async def process_faq_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO faq (question, answer) VALUES ($1, $2)", data['question'], message.text)
        
    await message.answer("✅ Питання успішно додано!")
    await state.clear()

# ================= КЕРУВАННЯ FAQ =================
@router.callback_query(F.data == "admin_manage", F.from_user.id.in_(ADMIN_IDS))
async def admin_manage_list(callback: CallbackQuery):
    async with pool.acquire() as conn:
        faqs = await conn.fetch("SELECT id, question FROM faq ORDER BY id ASC")
        
    if not faqs:
        return await callback.message.edit_text(
            "Список питань порожній.", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_to_panel")]])
        )
    
    kb = []
    for f in faqs:
        kb.append([InlineKeyboardButton(text=f"📌 {f['question']}", callback_data=f"admin_select_{f['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_to_panel")])
    
    await callback.message.edit_text("Обери питання, яке хочеш змінити чи видалити:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("admin_select_"), F.from_user.id.in_(ADMIN_IDS))
async def admin_select_faq(callback: CallbackQuery):
    faq_id = int(callback.data.replace("admin_select_", ""))
    async with pool.acquire() as conn:
        record = await conn.fetchrow("SELECT question, answer FROM faq WHERE id = $1", faq_id)
        
    if not record:
        return await callback.answer("Помилка! Питання не знайдено.", show_alert=True)
        
    text = f"<b>Поточне питання:</b>\n{record['question']}\n\n<b>Поточна відповідь:</b>\n{record['answer']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"admin_edit_{faq_id}"),
         InlineKeyboardButton(text="🗑 Видалити", callback_data=f"admin_del_{faq_id}")],
        [InlineKeyboardButton(text="⬅️ Назад до списку", callback_data="admin_manage")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_del_"), F.from_user.id.in_(ADMIN_IDS))
async def admin_delete_faq(callback: CallbackQuery):
    faq_id = int(callback.data.replace("admin_del_", ""))
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM faq WHERE id = $1", faq_id)
        
    await callback.answer("✅ Питання видалено!", show_alert=True)
    await admin_manage_list(callback)

@router.callback_query(F.data.startswith("admin_edit_"), F.from_user.id.in_(ADMIN_IDS))
async def admin_edit_start(callback: CallbackQuery, state: FSMContext):
    faq_id = int(callback.data.replace("admin_edit_", ""))
    await state.update_data(editing_faq_id=faq_id)
    
    await callback.message.edit_text(
        "Введи НОВИЙ текст для цього питання:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_manage")]])
    )
    await state.set_state(EditFAQ.waiting_for_new_question)

@router.message(EditFAQ.waiting_for_new_question, F.text)
async def admin_edit_q(message: Message, state: FSMContext):
    await state.update_data(new_question=message.text)
    await message.answer("Тепер введи НОВУ відповідь:")
    await state.set_state(EditFAQ.waiting_for_new_answer)

@router.message(EditFAQ.waiting_for_new_answer, F.text)
async def admin_edit_a(message: Message, state: FSMContext):
    data = await state.get_data()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE faq SET question = $1, answer = $2 WHERE id = $3", 
            data['new_question'], message.text, data['editing_faq_id']
        )
    await message.answer("✅ Питання успішно оновлено!")
    await state.clear()

# ================= ТИКЕТИ ТА ЗВ'ЯЗОК З АДМІНАМИ =================
@router.callback_query(F.data == "contact_admin")
async def ask_for_message(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "✍️ Напиши своє питання одним повідомленням.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="back_to_main")]
        ])
    )
    await state.set_state(ContactAdmin.waiting_for_message)

@router.message(ContactAdmin.waiting_for_message, F.text)
async def forward_to_admins(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "Без юзернейму"
    
    async with pool.acquire() as conn:
        ticket_id = await conn.fetchval(
            "INSERT INTO tickets (user_id, username, text) VALUES ($1, $2, $3) RETURNING id",
            user_id, username, message.text
        )
    
    admin_text = (
        f"📩 <b>Нове питання! [Тикет #{ticket_id}]</b>\n"
        f"Від: {message.from_user.full_name} ({username})\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"<b>Текст:</b>\n{message.text}"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception:
            pass
            
    await message.answer("✅ Твоє повідомлення надіслано адміністраторам!")
    await state.clear()

@router.callback_query(F.data == "admin_pending_tickets", F.from_user.id.in_(ADMIN_IDS))
async def view_pending_tickets(callback: CallbackQuery):
    async with pool.acquire() as conn:
        tickets = await conn.fetch("SELECT id, username, text FROM tickets ORDER BY created_at ASC")
        
    if not tickets:
        return await callback.message.edit_text(
            "✅ Супер! Усі питання закриті. Немає нових повідомлень.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_to_panel")]])
        )

    kb = []
    for t in tickets:
        short_text = (t['text'][:20] + '...') if len(t['text']) > 20 else t['text']
        kb.append([InlineKeyboardButton(text=f"❓ {t['username']} | {short_text}", callback_data=f"ticket_reply_{t['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_to_panel")])

    await callback.message.edit_text(
        f"📥 <b>Очікують відповіді ({len(tickets)}):</b>\nНатисни на питання, щоб відповісти прямо звідси.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("ticket_reply_"), F.from_user.id.in_(ADMIN_IDS))
async def select_ticket_to_reply(callback: CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.replace("ticket_reply_", ""))
    
    async with pool.acquire() as conn:
        record = await conn.fetchrow("SELECT user_id, username, text FROM tickets WHERE id = $1", ticket_id)

    if not record:
        return await callback.answer("Це питання вже було закрито іншим адміном!", show_alert=True)

    await state.update_data(reply_ticket_id=ticket_id, reply_user_id=record['user_id'])
    await callback.message.edit_text(
        f"👤 <b>Від:</b> {record['username']}\n"
        f"💬 <b>Питання:</b>\n{record['text']}\n\n"
        "✍️ <i>Напиши відповідь у чат, і я передам її користувачу:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_pending_tickets")]])
    )
    await state.set_state(ReplyFromPanel.waiting_for_reply)

@router.message(ReplyFromPanel.waiting_for_reply, F.text)
async def process_panel_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['reply_user_id'], f"🧑‍💻 <b>Відповідь від адміністратора:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Відповідь успішно надіслана!")

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tickets WHERE id = $1", data['reply_ticket_id'])
            
    except Exception as e:
        await message.answer(f"❌ Не вдалося надіслати. Помилка: {e}")

    await state.clear()

@router.message(F.reply_to_message & F.from_user.id.in_(ADMIN_IDS))
async def reply_from_admin(message: Message):
    original_text = message.reply_to_message.text
    if original_text and "ID:" in original_text:
        try:
            user_id = int(re.search(r"ID:\s*(\d+)", original_text).group(1))
            ticket_match = re.search(r"\[Тикет #(\d+)\]", original_text)
            
            if ticket_match:
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM tickets WHERE id = $1", int(ticket_match.group(1)))
            
            await bot.send_message(user_id, f"🧑‍💻 <b>Відповідь від адміністратора:</b>\n\n{message.text}", parse_mode="HTML")
            await message.reply("✅ Відповідь надіслана! Питання закрито.")
        except Exception as e:
            await message.reply(f"❌ Помилка: {e}")

import os
from aiohttp import web

# Функція-відповідь для пінгера (UptimeRobot)
async def health_check(request):
    return web.Response(text="Bot is awake and running!")

# ================= ЗАПУСК =================
async def main():
    # 1. Твоя ініціалізація БД та роутерів
    await init_db()
    dp.include_router(router)
    print("Бот запущено (PostgreSQL)...")
    
    # 2. --- БЛОК ДЛЯ RENDER (запуск вебсервера) ---
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Вебсервер для Render запущено на порту {port}")
    # ----------------------------------------------
    
    try:
        # 3. Запуск самого бота
        await dp.start_polling(bot)
    finally:
        # 4. Очищення підключень після зупинки бота
        if pool:
            await pool.close()
        await runner.cleanup() # Не забуваємо зупинити і вебсервер

if __name__ == "__main__":
    asyncio.run(main())