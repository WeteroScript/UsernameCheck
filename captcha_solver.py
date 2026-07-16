"""
Модуль для решения Drag & Drop капчи через Playwright (серверная версия)
"""

import asyncio
import logging
import random
import os
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser, Playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CaptchaSolver:
    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        Args:
            headless: на сервере всегда True
            timeout: таймаут в мс
        """
        self.headless = True  # Принудительно headless на сервере
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright: Optional[Playwright] = None
        self._initialized = False
    
    async def start(self) -> bool:
        """Запуск браузера в headless режиме"""
        try:
            logger.info("🚀 Запускаю браузер в headless режиме...")
            
            self.playwright = await async_playwright().start()
            
            self.browser = await self.playwright.chromium.launch(
                headless=True,  # Всегда True на сервере
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-accelerated-2d-canvas',
                    '--disable-accelerated-video-decode',
                    '--disable-software-rasterizer'
                ]
            )
            
            self.page = await self.browser.new_page(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            self.page.set_default_timeout(self.timeout)
            self._initialized = True
            
            logger.info("✅ Браузер запущен (headless)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска браузера: {e}")
            logger.error("💡 Убедись, что запущено: playwright install chromium")
            return False
    
    async def solve_drag_drop(self, url: str) -> bool:
        """Решение капчи"""
        try:
            if not self._initialized:
                if not await self.start():
                    return False
            
            logger.info(f"🌐 Открываю WebApp: {url[:100]}...")
            
            try:
                await self.page.goto(url, wait_until='networkidle', timeout=15000)
            except:
                await self.page.goto(url, wait_until='domcontentloaded', timeout=15000)
            
            await asyncio.sleep(3)
            
            # Скриншот для отладки
            try:
                screenshot = await self.page.screenshot()
                logger.info(f"📸 Скриншот: {len(screenshot)} байт")
            except:
                pass
            
            # Пробуем решить
            result = await self._try_solve()
            
            if result:
                logger.info("✅ Капча решена!")
                return True
            
            logger.warning("❌ Не удалось решить капчу")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False
    
    async def _try_solve(self) -> bool:
        """Попытка решить капчу"""
        try:
            # Ищем элементы
            piece_selectors = [
                '[draggable="true"]',
                '.puzzle-piece', '.drag-piece', '.slider-piece',
                '[class*="piece"]', '[class*="drag"]', '[class*="slider"]',
                '.captcha-piece', '.verify-piece'
            ]
            
            piece = None
            for selector in piece_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        piece = element
                        logger.info(f"🔍 Найден кусок: {selector}")
                        break
                except:
                    continue
            
            if not piece:
                logger.warning("⚠️ Кусок не найден")
                return await self._try_buttons()
            
            # Ищем цель
            target_selectors = [
                '.puzzle-target', '.drop-target', '.slot', '.target-area', '.drop-zone',
                '[class*="target"]', '[class*="slot"]', '[class*="drop"]'
            ]
            
            target = None
            for selector in target_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        target = element
                        logger.info(f"🔍 Найдена цель: {selector}")
                        break
                except:
                    continue
            
            if not target:
                # Ищем большой контейнер
                containers = await self.page.query_selector_all('div, section, main')
                for container in containers:
                    try:
                        box = await container.bounding_box()
                        if box and box['width'] > 200 and box['height'] > 100:
                            target = container
                            logger.info("🔍 Найден контейнер как цель")
                            break
                    except:
                        continue
            
            if not target:
                logger.warning("⚠️ Цель не найдена")
                return await self._try_buttons()
            
            # Координаты
            piece_box = await piece.bounding_box()
            target_box = await target.bounding_box()
            
            if not piece_box or not target_box:
                return await self._try_buttons()
            
            start_x = piece_box['x'] + piece_box['width'] / 2
            start_y = piece_box['y'] + piece_box['height'] / 2
            target_x = target_box['x'] + target_box['width'] / 2 + random.randint(-20, 20)
            target_y = target_box['y'] + target_box['height'] / 2 + random.randint(-20, 20)
            
            logger.info(f"🎯 Перетаскиваю: ({start_x:.0f}, {start_y:.0f}) -> ({target_x:.0f}, {target_y:.0f})")
            
            # Drag & drop
            await self.page.mouse.move(start_x, start_y)
            await asyncio.sleep(0.2)
            await self.page.mouse.down()
            await asyncio.sleep(0.3)
            
            steps = random.randint(15, 25)
            for i in range(steps):
                progress = i / steps
                curve = 20 * (1 - (2 * progress - 1) ** 2) * random.choice([-1, 1])
                x = start_x + (target_x - start_x) * progress
                y = start_y + (target_y - start_y) * progress + curve + random.randint(-3, 3)
                await self.page.mouse.move(x, y)
                if i % 3 == 0:
                    await asyncio.sleep(0.05)
            
            await self.page.mouse.move(target_x, target_y)
            await asyncio.sleep(0.3)
            await self.page.mouse.up()
            await asyncio.sleep(0.5)
            
            await asyncio.sleep(2)
            
            # Проверяем результат
            return await self._check_result()
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return await self._try_buttons()
    
    async def _try_buttons(self) -> bool:
        """Попытка через кнопки"""
        try:
            elements = await self.page.query_selector_all(
                'button, input[type="submit"], [role="button"], [class*="btn"], [class*="button"]'
            )
            
            for element in elements:
                try:
                    text = await element.text_content()
                    if text:
                        text_lower = text.lower()
                        if any(word in text_lower for word in 
                               ['подтверд', 'confirm', 'verify', 'продолж', 'continue', 'готово', 'done']):
                            await element.click()
                            await asyncio.sleep(1)
                            logger.info(f"✅ Нажата кнопка: {text}")
                except:
                    continue
            
            return await self._check_result()
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return False
    
    async def _check_result(self) -> bool:
        """Проверка результата"""
        try:
            # Признаки успеха
            for selector in ['[class*="success"]', '[class*="completed"]', '[class*="verified"]', '[class*="passed"]']:
                try:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        logger.info(f"✅ Успех: {selector}")
                        return True
                except:
                    continue
            
            # Текст успеха
            try:
                body = await self.page.text_content('body')
                if body:
                    body_lower = body.lower()
                    if any(word in body_lower for word in ['успешно', 'пройдена', 'подтверждено', 'verified', 'success']):
                        logger.info("✅ Найден текст успеха")
                        return True
            except:
                pass
            
            # Проверяем исчезновение капчи
            for selector in ['.captcha-container', '.puzzle-container', '[class*="captcha"]', '[class*="puzzle"]']:
                try:
                    element = await self.page.query_selector(selector)
                    if not element or not await element.is_visible():
                        logger.info("✅ Капча исчезла")
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки: {e}")
            return False
    
    async def close(self):
        """Закрытие"""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self._initialized = False
            logger.info("✅ Браузер закрыт")
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")


async def solve_webapp_captcha(url: str, headless: bool = True) -> bool:
    """Решение капчи (headless=True для сервера)"""
    solver = CaptchaSolver(headless=True)
    try:
        return await solver.solve_drag_drop(url)
    finally:
        await solver.close()
