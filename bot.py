import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command 
from deep_translator import GoogleTranslator 
from google import genai
from google.genai.errors import APIError
from google.genai.types import HarmCategory as HCategory, SafetySetting, HarmBlockThreshold 
from google.genai.errors import APIError

# ==================================
# 1. Инициализация и настройки 
# ==================================

# Чтение ключей из окружения (Render)
BOT_TOKEN = os.getenv("BOT_TOKEN") 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    print("❌ Ключи BOT_TOKEN или GEMINI_API_KEY не найдены в переменных окружения Render.")

# Инициализация ботов и клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализация deep-translator
translator = GoogleTranslator(source='auto', target='en')

# Настройки языков
languages = {
    "Русский 🇷🇺": "ru",
    "Английский 🇬🇧": "en",
    "Узбекский 🇺🇿": "uz"
}

user_lang = {}

# КОНФИГУРАЦИЯ БЕЗОПАСНОСТИ (для Gemini)
SAFETY_SETTINGS = [
    SafetySetting(
        category=category,
        threshold=HarmBlockThreshold.BLOCK_NONE,
    )
    for category in [
        HCategory.HARM_CATEGORY_HARASSMENT,
        HCategory.HARM_CATEGORY_HATE_SPEECH,
        HCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        HCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    ]
]

# Инициализация клиента Gemini
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Ошибка инициализации Gemini: {e}")


# ==================================
# 2. Общая функция для вызова Gemini
# ==================================

async def get_gemini_response(prompt: str):
    """Отправляет запрос в Gemini и возвращает текст или ошибку."""
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=400,
                safety_settings=SAFETY_SETTINGS
            )
        )
        if response and response.text:
            return response.text.strip()
        return "⚠️ Gemini не смог дать ответ."
    except APIError as e:
        return f"⚠️ Ошибка ИИ (API Gemini): {e}"
    except Exception as e:
        return f"⚠️ Неизвестная ошибка ИИ: {e}"


# ==================================
# 3. Хендлеры (Обработчики сообщений)
# ==================================

@dp.message(Command('start'))
async def start(message: types.Message):
    button_list = [types.KeyboardButton(text=lang_name) for lang_name in languages.keys()]
    
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[button_list], 
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer(
        "👋 Привет! Я умный переводчик и помощник.\n"
        "Выбери язык, на который я буду переводить:",
        reply_markup=keyboard
    )

@dp.message(F.text.in_(languages.keys()))
async def set_language(message: types.Message):
    user_lang[message.from_user.id] = languages[message.text]
    await message.answer(f"✅ Отлично! Перевожу на {message.text}.\nТеперь просто напиши фразу для перевода и объяснения.",
                         reply_markup=types.ReplyKeyboardRemove())

@dp.message(F.text)
async def handle_text(message: types.Message):
    target_lang_code = user_lang.get(message.from_user.id, "en")
    source_text = message.text

    # --- Шаг 1: Перевод с помощью deep-translator ---
    translation = ""
    try:
        translator.target = target_lang_code 
        translation = await asyncio.to_thread(translator.translate, source_text)
        if not translation:
             translation = source_text
    except Exception as e:
        await message.answer(f"⚠️ Ошибка перевода: {e}")
        return

    # --- Шаг 2: Объяснение с помощью Gemini (Обходной путь через Английский) ---
    gemini_output_lang = "en"
    ai_text = ""
    ai_text_en = ""
    
    # 1. Gemini всегда генерирует объяснение на английском
    prompt_en = (
        f"Объясни значение и дай краткий контекст для фразы: '{translation}'. "
        f"Ответ должен быть на АНГЛИЙСКОМ языке и быть максимально коротким и по существу."
    )
    ai_text_en = await get_gemini_response(prompt_en)
    
    # 2. Перевод ответа Gemini на целевой язык
    if ai_text_en and not ai_text_en.startswith("⚠️"):
        if target_lang_code != gemini_output_lang:
            try:
                translator.source = gemini_output_lang
                translator.target = target_lang_code
                ai_text = await asyncio.to_thread(translator.translate, ai_text_en)
            except Exception as e:
                ai_text = f"⚠️ Ошибка перевода объяснения: {e}"
        else:
            ai_text = ai_text_en
    else:
        ai_text = ai_text_en # Передаем ошибку, если она была

    # --- Шаг 3: Отправка результата с кнопкой "Синонимы" ---
    
    # Используем ТОЛЬКО ПЕРВОЕ СЛОВО перевода для callback_data (для стабильности)
    first_word = translation.split()[0][:20] 
    synonym_callback_data = f"SYNONYM_{target_lang_code}_{first_word}"
    
    inline_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="🔎 Синонимы", callback_data=synonym_callback_data)
            ]
        ]
    )

    await message.answer(
        f"🌍 <b>Перевод:</b>\n{translation}\n\n🤖 <b>Объяснение от Gemini:</b>\n{ai_text}",
        parse_mode="HTML",
        reply_markup=inline_keyboard
    )


# ==================================
# 4. Хендлер для инлайн-кнопки "Синонимы"
# ==================================

@dp.callback_query(F.data.startswith("SYNONYM_"))
async def handle_synonym_request(callback_query: types.CallbackQuery):
    # Разбираем данные: SYNONYM_[lang_code]_[word]
    _, lang_code, word = callback_query.data.split('_')
    
    # Убираем кнопку
    await bot.edit_message_reply_markup(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        reply_markup=None
    )

    await callback_query.answer("Ищем синонимы...")

    synonyms_text = ""
    
    # --- Динамический выбор метода запроса ---

    if lang_code == "uz":
        # Узбекский (uz): Двойной перевод (Англ -> Узбек) для надежности
        prompt_synonym_en = (
            f"Provide 5-7 synonyms for the word '{word}' and list them. The response must be a simple, clean, unnumbered list in ENGLISH."
        )
        synonyms_en = await get_gemini_response(prompt_synonym_en)

        if synonyms_en.startswith("⚠️"):
            synonyms_text = synonyms_en
        elif not synonyms_en or len(synonyms_en) < 5:
            synonyms_text = f"⚠️ Gemini не смог найти синонимы или альтернативы для слова \"{word}\"."
        else:
            try:
                # Перевод с английского на узбекский
                translator.source = "en"
                translator.target = lang_code
                synonyms_text = await asyncio.to_thread(translator.translate, synonyms_en)
            except Exception:
                synonyms_text = f"⚠️ Ошибка при переводе списка синонимов на {lang_code}. Вот английский оригинал:\n{synonyms_en}"
            
    else:
        # Русский (ru) и Английский (en): Прямой запрос на целевом языке
        
        prompt_synonym_direct = (
            f"Provide 5-7 synonyms or alternative phrases for the word '{word}' in the language with code '{lang_code}' and list them in a simple, unnumbered format."
            f"Do not add extra explanations, only the list."
        )
        synonyms_text = await get_gemini_response(prompt_synonym_direct)
        
        if synonyms_text.startswith("⚠️"):
             pass # Ошибка уже в тексте
        elif not synonyms_text or len(synonyms_text) < 5:
             # Если ответ пуст
             synonyms_text = f"⚠️ Gemini не смог найти синонимы или альтернативы для слова \"{word}\"."


    # --- Отправка результата ---
    await bot.send_message(
        callback_query.message.chat.id,
        f"📚 <b>Синонимы/альтернативы для \"{word}\":</b>\n\n{synonyms_text}",
        parse_mode="HTML"
    )

    await callback_query.answer()


# ==================================
# 5. Асинхронный запуск бота (aiogram 3.x)
# ==================================
async def main():
    print("Бот запущен. Ожидание сообщений...")
    # Проверка на наличие ключа перед запуском polling
    if BOT_TOKEN and GEMINI_API_KEY:
        await dp.start_polling(bot, skip_updates=True)
    else:
        print("Ключи API не настроены. Бот не будет запущен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную")
    except Exception as e:
        print(f"Критическая ошибка при запуске: {e}")