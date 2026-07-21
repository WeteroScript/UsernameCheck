"""
Модуль для решения капчи (фото + кнопки 1-9)
С авто-решением через @ChatGPT_Gemini_DeepSeek_Bot
"""

import logging
import asyncio
import io
import os
import re
from typing import Optional, Dict, Any, Tuple
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

try:
    from gram_bot import _throttle_click
except ImportError:
    async def _throttle_click(bot_username: str):
        pass

CAPTCHA_SOLVER_BOT = "@ChatGPT_Gemini_DeepSeek_Bot"


class CaptchaSolver:
    def __init__(self):
        self.bot = None
        self.captcha_storage: Dict[int, Dict[str, Any]] = {}
        self.active_clients: Dict[str, TelegramClient] = {}
        self.continue_callback = None
        self.auto_click_timeout = 30
        self.use_ai_solver = True
    
    def set_bot(self, bot_instance):
        self.bot = bot_instance
    
    def set_active_clients(self, clients: Dict[str, TelegramClient]):
        self.active_clients = clients
    
    def set_continue_callback(self, callback):
        self.continue_callback = callback
    
    def set_auto_click_timeout(self, seconds: int):
        self.auto_click_timeout = seconds
    
    def set_ai_solver(self, enabled: bool):
        self.use_ai_solver = enabled
        logger.info(f"🤖 AI-решатель установлен: {enabled}")
    
    def _get_button_text(self, btn) -> str:
        try:
            return btn.text if hasattr(btn, 'text') else str(btn)
        except:
            return ""
    
    def _get_button_url(self, btn) -> Optional[str]:
        try:
            if hasattr(btn, 'url') and btn.url:
                return btn.url
            inner = getattr(btn, 'button', None)
            if inner is not None and hasattr(inner, 'url') and inner.url:
                return inner.url
        except:
            pass
        return None
    
    def _has_media(self, msg) -> bool:
        if not msg:
            return False
        return msg.photo is not None or msg.document is not None or msg.media is not None
    
    async def _download_media(self, client: TelegramClient, msg) -> Optional[bytes]:
        try:
            return await client.download_media(msg, file=bytes)
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания медиа: {e}")
            return None
    
    def is_captcha_message(self, msg) -> bool:
        if not msg:
            return False
        
        text = (msg.raw_text or "").lower()
        
        captcha_keywords = [
            "на какой фотографии изображён",
            "на какой фотографии изображен",
            "выберите правильный ответ",
            "капча",
            "captcha",
            "подтвердите, что вы человек",
            "verify you are human",
            "выберите жирафа",
            "выберите волка",
            "выберите собаку",
            "выберите кота",
            "выберите птицу",
            "выберите правильный вариант"
        ]
        
        for keyword in captcha_keywords:
            if keyword in text:
                return True
        
        if msg.buttons:
            numbers = []
            for row in msg.buttons:
                for btn in row:
                    btn_text = self._get_button_text(btn)
                    if btn_text.isdigit() and 1 <= int(btn_text) <= 9:
                        numbers.append(btn_text)
            
            if len(numbers) >= 3:
                return True
        
        return False
    
    async def _click_captcha_button(self, client: TelegramClient, bot_username: str, msg_id: int, number: str) -> bool:
        try:
            msg = await client.get_messages(bot_username, ids=msg_id)
            if not msg or not msg.buttons:
                logger.warning(f"⚠️ Сообщение {msg_id} не найдено или нет кнопок")
                return False
            
            for row in msg.buttons:
                for btn in row:
                    if self._get_button_text(btn) == number:
                        await _throttle_click(bot_username)
                        await btn.click()
                        logger.info(f"🖱 Нажата кнопка {number} в @{bot_username}")
                        return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка нажатия кнопки: {e}")
            return False
    
    async def _solve_with_ai(self, client: TelegramClient, photo_bytes: bytes, question: str) -> Optional[str]:
        temp_path = "captcha_temp.jpg"
        
        try:
            logger.info("🤖 Отправляю капчу в @ChatGPT_Gemini_DeepSeek_Bot...")
            
            with open(temp_path, "wb") as f:
                f.write(photo_bytes)
            logger.info("✅ Фото сохранено")
            
            await client.send_file(CAPTCHA_SOLVER_BOT, temp_path)
            logger.info("📤 Фото отправлено в AI-бот")
            await asyncio.sleep(2)
            
            prompt = f"{question}\nНапиши только цифру. Ничего более не пиши."
            await client.send_message(CAPTCHA_SOLVER_BOT, prompt)
            logger.info(f"📤 Вопрос отправлен: {prompt}")
            await asyncio.sleep(3)
            
            msgs = await client.get_messages(CAPTCHA_SOLVER_BOT, limit=1)
            if not msgs:
                logger.warning("⚠️ Нет ответа от AI бота")
                return None
            
            answer = msgs[0].raw_text or ""
            logger.info(f"🤖 Ответ AI: {answer}")
            
            digits = re.findall(r'[1-9]', answer)
            if digits:
                return digits[0]
            return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка решения капчи через AI: {e}")
            return None
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logger.info("🗑 Временный файл удален")
                except:
                    pass
    
    async def _solve_without_photo(self, client: TelegramClient, question: str) -> Optional[str]:
        try:
            logger.info("🤖 Отправляю текстовый вопрос в AI бот (без фото)...")
            
            prompt = f"{question}\nНапиши только цифру. Ничего более не пиши."
            await client.send_message(CAPTCHA_SOLVER_BOT, prompt)
            await asyncio.sleep(3)
            
            msgs = await client.get_messages(CAPTCHA_SOLVER_BOT, limit=1)
            if not msgs:
                return None
            
            answer = msgs[0].raw_text or ""
            logger.info(f"🤖 Ответ AI: {answer}")
            
            digits = re.findall(r'[1-9]', answer)
            if digits:
                return digits[0]
            return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return None
    
    async def send_captcha_to_user(self, msg, chat_id: int, client: TelegramClient, bot_username: str = None) -> bool:
        if not chat_id or not self.bot:
            logger.error("❌ Bot или chat_id не установлены")
            return False
        
        try:
            if not bot_username:
                try:
                    bot_username = (msg.chat.username if msg.chat else None) or "gram_prbot"
                except:
                    bot_username = "gram_prbot"
            
            self.captcha_storage[chat_id] = {
                'client': client,
                'bot_username': bot_username,
                'msg_id': msg.id,
                'answered': False
            }
            
            if self.use_ai_solver and msg.raw_text:
                question = msg.raw_text.strip()
                question = re.sub(r'\d+', '', question)
                question = question.replace('✅', '').replace('❌', '').strip()
                question = question.replace('1', '').replace('2', '').replace('3', '')
                question = question.replace('4', '').replace('5', '').replace('6', '')
                question = question.replace('7', '').replace('8', '').replace('9', '')
                question = question.strip()
                
                if len(question) > 3:
                    photo_bytes = None
                    if self._has_media(msg):
                        photo_bytes = await self._download_media(client, msg)
                    
                    if photo_bytes:
                        answer = await self._solve_with_ai(client, photo_bytes, question)
                    else:
                        answer = await self._solve_without_photo(client, question)
                    
                    if answer:
                        await self._click_captcha_button(client, bot_username, msg.id, answer)
                        await asyncio.sleep(2)
                        new_msg = await client.get_messages(bot_username, limit=1)
                        if new_msg and not self.is_captcha_message(new_msg):
                            del self.captcha_storage[chat_id]
                            if self.continue_callback:
                                phone = next((p for p, c in self.active_clients.items() if c == client), None)
                                if phone:
                                    await self.continue_callback(phone)
                            return True
            
            # Отправка пользователю
            text = f"🧩 <b>Капча!</b>\n\n"
            text += f"🤖 Бот: @{bot_username}\n\n"
            if msg.raw_text:
                text += f"📝 {msg.raw_text[:300]}\n\n"
            text += f"👇 Нажми номер правильного ответа"
            
            await self.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            
            if self._has_media(msg):
                try:
                    file_data = await self._download_media(client, msg)
                    if file_data:
                        await self.bot.send_photo(
                            chat_id,
                            BufferedInputFile(file_data, filename="captcha.jpg"),
                            caption="🖼 Выбери правильный ответ"
                        )
                        logger.info(f"✅ Фото капчи отправлено пользователю")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки фото: {e}")
            
            buttons = []
            row = []
            for i in range(1, 10):
                row.append(InlineKeyboardButton(
                    text=str(i),
                    callback_data=f"captcha_answer_{chat_id}_{i}"
                ))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            
            buttons.append([
                InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}"),
                InlineKeyboardButton(text="⏹ Отмена", callback_data=f"captcha_stop_{chat_id}")
            ])
            
            await self.bot.send_message(
                chat_id,
                "🔢 Выбери номер:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки капчи: {e}")
            return False
    
    async def handle_captcha_answer(self, chat_id: int, number: str) -> Tuple[bool, str]:
        try:
            if chat_id not in self.captcha_storage:
                return False, "Капча не найдена"
            
            data = self.captcha_storage[chat_id]
            client = data['client']
            bot_username = data['bot_username']
            msg_id = data['msg_id']
            
            if data.get('answered', False):
                return False, "Капча уже обработана"
            
            if not client:
                return False, "Клиент не найден"
            
            if not client.is_connected():
                await client.connect()
            
            success = await self._click_captcha_button(client, bot_username, msg_id, number)
            if not success:
                return False, f"Кнопка {number} не найдена"
            
            data['answered'] = True
            await asyncio.sleep(2)
            
            new_msg = await client.get_messages(bot_username, limit=1)
            
            if new_msg and not self.is_captcha_message(new_msg):
                del self.captcha_storage[chat_id]
                if self.continue_callback:
                    phone = next((p for p, c in self.active_clients.items() if c == client), None)
                    if phone:
                        await self.continue_callback(phone)
                return True, "✅ Капча пройдена!"
            else:
                data['answered'] = False
                return False, f"⏳ Кнопка {number} нажата, капча еще активна. Попробуй другой номер."
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки ответа: {e}")
            return False, str(e)
    
    async def check_captcha_status(self, chat_id: int) -> Tuple[bool, str]:
        try:
            if chat_id not in self.captcha_storage:
                return True, "Капча пройдена"
            
            data = self.captcha_storage[chat_id]
            client = data['client']
            bot_username = data['bot_username']
            
            if not client:
                return False, "Клиент не найден"
            
            if not client.is_connected():
                await client.connect()
            
            msgs = await client.get_messages(bot_username, limit=1)
            new_msg = msgs[0] if msgs else None
            
            if new_msg and not self.is_captcha_message(new_msg):
                del self.captcha_storage[chat_id]
                return True, "Капча пройдена"
            
            return False, "Капча еще активна"
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки: {e}")
            return False, str(e)
    
    def stop_captcha(self, chat_id: int) -> bool:
        if chat_id in self.captcha_storage:
            del self.captcha_storage[chat_id]
            logger.info(f"⏹ Капча остановлена для {chat_id}")
            return True
        return False
    
    def get_captcha_data(self, chat_id: int) -> Optional[Dict]:
        return self.captcha_storage.get(chat_id)


# ============================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ============================================================

captcha_solver = CaptchaSolver()


# ============================================================
# ФУНКЦИИ ДЛЯ ВЫЗОВА ИЗ ВНЕ
# ============================================================

def set_captcha_bot(bot_instance):
    captcha_solver.set_bot(bot_instance)


def set_captcha_clients(clients: Dict[str, TelegramClient]):
    captcha_solver.set_active_clients(clients)


def set_captcha_continue_callback(callback):
    captcha_solver.set_continue_callback(callback)


def set_auto_click_timeout(seconds: int):
    captcha_solver.set_auto_click_timeout(seconds)


def set_ai_solver(enabled: bool):
    captcha_solver.set_ai_solver(enabled)


def is_captcha_message(msg) -> bool:
    return captcha_solver.is_captcha_message(msg)


async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient, bot_username: str = None) -> bool:
    return await captcha_solver.send_captcha_to_user(msg, chat_id, client, bot_username)


async def handle_captcha_answer(chat_id: int, number: str) -> Tuple[bool, str]:
    return await captcha_solver.handle_captcha_answer(chat_id, number)


async def check_captcha_status(chat_id: int) -> Tuple[bool, str]:
    return await captcha_solver.check_captcha_status(chat_id)


def stop_captcha(chat_id: int) -> bool:
    return captcha_solver.stop_captcha(chat_id)


def get_captcha_data(chat_id: int) -> Optional[Dict]:
    return captcha_solver.get_captcha_data(chat_id)
