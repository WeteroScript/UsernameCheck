"""
Модуль для решения капчи (фото + кнопки 1-9) с поддержкой нескольких ботов
"""

import logging
import asyncio
import random
from typing import Optional, Dict, Any, Tuple
from telethon import TelegramClient
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)


class CaptchaSolver:
    def __init__(self):
        self.bot = None
        self.captcha_storage: Dict[int, Dict[str, Any]] = {}
        self.active_clients: Dict[str, TelegramClient] = {}
        self.continue_callback = None
        self.auto_click_timeout = 15
    
    def set_bot(self, bot_instance):
        self.bot = bot_instance
    
    def set_active_clients(self, clients: Dict[str, TelegramClient]):
        self.active_clients = clients
    
    def set_continue_callback(self, callback):
        self.continue_callback = callback
    
    def set_auto_click_timeout(self, seconds: int):
        self.auto_click_timeout = seconds
    
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
            "выберите правильный вариант",
            "выберите животное",
            "выберите предмет"
        ]
        
        for keyword in captcha_keywords:
            if keyword in text:
                logger.info(f"🔍 Найдена капча: '{keyword}'")
                return True
        
        if msg.buttons:
            numbers = []
            for row in msg.buttons:
                for btn in row:
                    btn_text = self._get_button_text(btn)
                    if btn_text.isdigit() and 1 <= int(btn_text) <= 9:
                        numbers.append(btn_text)
            
            if len(numbers) >= 3:
                logger.info(f"🔍 Найдена капча с кнопками: {numbers}")
                return True
        
        return False
    
    async def send_captcha_to_user(self, msg, chat_id: int, client: TelegramClient, bot_username: str = None) -> bool:
        """
        Отправка капчи пользователю
        """
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
                'chat_id': chat_id,
                'answered': False
            }
            
            # 1. Текст
            text = f"🚨 <b>Обнаружена капча!</b>\n\n"
            text += f"🤖 <b>Бот:</b> @{bot_username}\n\n"
            if msg.raw_text:
                text += f"📝 <b>Текст капчи:</b>\n{msg.raw_text}\n\n"
            text += f"⏳ У тебя есть {self.auto_click_timeout} секунд.\n"
            text += f"Нажми номер правильного ответа (1-9)\n"
            text += f"Если не выберешь - бот нажмет рандомную кнопку."
            
            await self.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            
            # 2. Фото
            if msg.photo:
                try:
                    file_data = await client.download_media(msg, file=bytes)
                    if file_data:
                        await self.bot.send_photo(
                            chat_id,
                            BufferedInputFile(file_data, filename="captcha.jpg"),
                            caption="🖼 Выбери правильный ответ (1-9)"
                        )
                        logger.info(f"✅ Фото отправлено пользователю {chat_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки фото: {e}")
            
            # 3. Кнопки 1-9
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
            
            asyncio.create_task(self._auto_click_timer(chat_id))
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки капчи: {e}")
            return False
    
    async def _auto_click_timer(self, chat_id: int):
        await asyncio.sleep(self.auto_click_timeout)
        
        if chat_id not in self.captcha_storage:
            return
        
        data = self.captcha_storage[chat_id]
        if data.get('answered', False):
            return
        
        random_number = str(random.randint(1, 9))
        logger.info(f"🤖 Авто-нажатие: пользователь {chat_id} не ответил, нажимаю {random_number}")
        
        if self.bot:
            try:
                await self.bot.send_message(
                    chat_id,
                    f"⏳ Время вышло!\n"
                    f"🤖 Автоматически нажимаю кнопку <b>{random_number}</b>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await self._handle_answer(chat_id, random_number, is_auto=True)
    
    async def _handle_answer(self, chat_id: int, number: str, is_auto: bool = False) -> Tuple[bool, str]:
        try:
            if chat_id not in self.captcha_storage:
                return False, "Капча не найдена"
            
            data = self.captcha_storage[chat_id]
            client = data['client']
            bot_username = data['bot_username']
            
            data['answered'] = True
            
            if not client:
                return False, "Клиент не найден"
            
            if not client.is_connected():
                await client.connect()
            
            await client.send_message(bot_username, number)
            logger.info(f"📤 {'Авто-ответ' if is_auto else 'Пользователь'} отправил {number} в {bot_username}")
            await asyncio.sleep(2)
            
            msgs = await client.get_messages(bot_username, limit=1)
            new_msg = msgs[0] if msgs else None
            
            if new_msg and not self.is_captcha_message(new_msg):
                logger.info(f"✅ Капча пройдена! (ответ: {number})")
                del self.captcha_storage[chat_id]
                
                if self.bot and not is_auto:
                    await self.bot.send_message(
                        chat_id,
                        f"✅ <b>Капча пройдена!</b> (выбрано: {number})",
                        parse_mode=ParseMode.HTML
                    )
                
                if self.continue_callback:
                    phone = None
                    for p, c in self.active_clients.items():
                        if c == client:
                            phone = p
                            break
                    if phone:
                        await self.continue_callback(phone)
                
                return True, "Капча пройдена!"
            
            if self.bot and not is_auto:
                await self.bot.send_message(
                    chat_id,
                    f"⏳ Капча еще активна (ответ: {number})\nПопробуй другой номер",
                    parse_mode=ParseMode.HTML
                )
            
            if is_auto:
                await asyncio.sleep(1)
                new_number = str(random.randint(1, 9))
                logger.info(f"🤖 Повторное авто-нажатие: {new_number}")
                return await self._handle_answer(chat_id, new_number, is_auto=True)
            
            return False, "Капча еще активна"
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки ответа: {e}")
            return False, str(e)
    
    async def handle_captcha_answer(self, chat_id: int, number: str) -> Tuple[bool, str]:
        return await self._handle_answer(chat_id, number, is_auto=False)
    
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
