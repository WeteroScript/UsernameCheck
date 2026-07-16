"""
Модуль для решения Drag & Drop капчи через Playwright
"""

import asyncio
import logging
import random
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Page, Browser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CaptchaSolver:
    def __init__(self, headless: bool = False, timeout: int = 30000):
        """
        Инициализация солвера
        
        Args:
            headless: Запускать браузер в фоне или с GUI
            timeout: Таймаут в миллисекундах
        """
        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None
    
    async def start(self):
        """Запуск браузера"""
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            )
            
            self.page = await self.browser.new_page(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            
            # Установка таймаута
            self.page.set_default_timeout(self.timeout)
            
            logger.info("✅ Браузер запущен")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка запуска браузера: {e}")
            return False
    
    async def solve_drag_drop(self, url: str) -> bool:
        """
        Решение Drag & Drop капчи
        
        Args:
            url: URL WebApp с капчей
            
        Returns:
            True если капча решена, False если нет
        """
        try:
            if not self.page:
                if not await self.start():
                    return False
            
            logger.info(f"🌐 Открываю WebApp: {url}")
            await self.page.goto(url, wait_until='networkidle')
            await asyncio.sleep(2)
            
            # Ждем загрузки капчи
            await self.page.wait_for_selector('.puzzle-container, .captcha-container, [class*="captcha"], [class*="puzzle"]', 
                                             timeout=10000, state='visible')
            
            # Пробуем разные селекторы для пазла
            puzzle_selectors = [
                # Общие селекторы
                '.puzzle',
                '.drag-target',
                '.slider-target',
                '.puzzle-target',
                # Специфичные для Gram
                '.captcha-puzzle',
                '.drag-drop-puzzle',
                '.verify-puzzle',
                # По классам с частью слова
                '[class*="puzzle"]',
                '[class*="drag"]',
                '[class*="slider"]',
                '[class*="target"]',
                '[class*="piece"]',
                '[class*="captcha"]',
                '[class*="verify"]',
            ]
            
            # Пробуем найти элементы пазла
            piece_element = None
            target_element = None
            
            for selector in puzzle_selectors:
                try:
                    # Ищем кусок пазла
                    piece = await self.page.query_selector(selector)
                    if piece:
                        # Проверяем, что это не пустой элемент
                        is_visible = await piece.is_visible()
                        if is_visible:
                            piece_element = piece
                            logger.info(f"🔍 Найден кусок пазла: {selector}")
                            break
                except:
                    continue
            
            if not piece_element:
                # Если не нашли явный кусок, ищем любой перемещаемый элемент
                # Пробуем найти элемент с атрибутами draggable
                try:
                    piece_element = await self.page.query_selector('[draggable="true"]')
                    if piece_element:
                        logger.info("🔍 Найден draggable элемент")
                except:
                    pass
            
            # Ищем цель (куда перетаскивать)
            for selector in puzzle_selectors:
                try:
                    target = await self.page.query_selector(selector)
                    if target and target != piece_element:
                        is_visible = await target.is_visible()
                        if is_visible:
                            target_element = target
                            logger.info(f"🔍 Найдена цель: {selector}")
                            break
                except:
                    continue
            
            # Если не нашли цель, пробуем искать контейнер с относительными координатами
            if not target_element:
                try:
                    # Ищем большой элемент, который может быть целью
                    containers = await self.page.query_selector_all('div, section, [class*="container"], [class*="area"]')
                    for container in containers:
                        # Проверяем размер
                        box = await container.bounding_box()
                        if box and box['width'] > 200 and box['height'] > 100:
                            target_element = container
                            logger.info(f"🔍 Найден контейнер как цель")
                            break
                except:
                    pass
            
            # Если ничего не нашли - пробуем общие методы
            if not piece_element or not target_element:
                logger.warning("⚠️ Не удалось найти элементы пазла, пробую общий подход...")
                return await self._solve_generic()
            
            # Получаем координаты
            piece_box = await piece_element.bounding_box()
            target_box = await target_element.bounding_box()
            
            if not piece_box or not target_box:
                logger.error("❌ Не удалось получить координаты")
                return False
            
            # Вычисляем позиции
            start_x = piece_box['x'] + piece_box['width'] / 2
            start_y = piece_box['y'] + piece_box['height'] / 2
            
            # Цель - середина целевой области
            target_x = target_box['x'] + target_box['width'] / 2
            target_y = target_box['y'] + target_box['height'] / 2
            
            # Добавляем случайное смещение (чтобы выглядело естественно)
            offset_x = random.randint(-20, 20)
            offset_y = random.randint(-20, 20)
            target_x += offset_x
            target_y += offset_y
            
            logger.info(f"🎯 Перетаскиваю: ({start_x:.1f}, {start_y:.1f}) -> ({target_x:.1f}, {target_y:.1f})")
            
            # Выполняем drag & drop с эмуляцией человеческого движения
            success = await self._drag_and_drop_with_curve(start_x, start_y, target_x, target_y)
            
            if success:
                await asyncio.sleep(1)
                # Проверяем результат
                result = await self._check_result()
                
                if result:
                    logger.info("✅ Капча успешно решена!")
                    return True
                else:
                    # Пробуем еще раз с небольшим смещением
                    logger.info("🔄 Повторная попытка с другим смещением...")
                    target_x2 = target_box['x'] + target_box['width'] / 2 + random.randint(-30, 30)
                    target_y2 = target_box['y'] + target_box['height'] / 2 + random.randint(-30, 30)
                    
                    success2 = await self._drag_and_drop_with_curve(start_x, start_y, target_x2, target_y2)
                    
                    if success2:
                        await asyncio.sleep(1)
                        return await self._check_result()
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка решения капчи: {e}")
            return False
    
    async def _drag_and_drop_with_curve(self, start_x: float, start_y: float, end_x: float, end_y: float) -> bool:
        """
        Эмуляция drag & drop с кривой движения (человекоподобное)
        """
        try:
            # Наводим на начальную позицию
            await self.page.mouse.move(start_x, start_y)
            await asyncio.sleep(0.2)
            
            # Нажимаем
            await self.page.mouse.down()
            await asyncio.sleep(0.3)
            
            # Двигаем с кривой (не по прямой)
            steps = random.randint(15, 25)
            for i in range(steps):
                progress = i / steps
                # Синусоидальная кривая + случайное отклонение
                curve_offset = 20 * (1 - (2 * progress - 1) ** 2) * random.choice([-1, 1])
                curve_offset += random.randint(-5, 5)
                
                x = start_x + (end_x - start_x) * progress
                y = start_y + (end_y - start_y) * progress + curve_offset
                
                # Добавляем небольшие паузы для реалистичности
                await self.page.mouse.move(x, y)
                if i % 3 == 0:
                    await asyncio.sleep(0.05 + random.random() * 0.03)
            
            # Финальная точка
            await self.page.mouse.move(end_x, end_y)
            await asyncio.sleep(0.3)
            
            # Отпускаем
            await self.page.mouse.up()
            await asyncio.sleep(0.5)
            
            logger.info("✅ Drag & Drop выполнен")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка drag & drop: {e}")
            return False
    
    async def _solve_generic(self) -> bool:
        """
        Общий метод решения, когда не удалось найти элементы
        """
        try:
            # Пробуем нажать на все активные элементы
            clickable = await self.page.query_selector_all('button, input[type="submit"], [role="button"], [class*="btn"], [class*="button"]')
            
            for element in clickable:
                try:
                    text = await element.text_content()
                    if text and any(word in text.lower() for word in ['подтверд', 'confirm', 'verify', 'продолж', 'continue']):
                        await element.click()
                        await asyncio.sleep(1)
                        logger.info(f"✅ Нажата кнопка: {text}")
                except:
                    continue
            
            # Проверяем результат
            return await self._check_result()
            
        except Exception as e:
            logger.error(f"❌ Ошибка общего метода: {e}")
            return False
    
    async def _check_result(self) -> bool:
        """
        Проверка, пройдена ли капча
        """
        try:
            # Проверяем наличие признаков успеха
            success_selectors = [
                '[class*="success"]',
                '[class*="completed"]',
                '[class*="verified"]',
                '[class*="passed"]',
                '.captcha-success',
                '.verify-success',
                # Текст успеха
                'text=успешно',
                'text=пройдена',
                'text=подтверждено',
                'text=verified',
                'text=success',
            ]
            
            for selector in success_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            logger.info(f"✅ Найден признак успеха: {selector}")
                            return True
                except:
                    continue
            
            # Проверяем наличие признаков неудачи
            fail_selectors = [
                '[class*="error"]',
                '[class*="failed"]',
                '[class*="invalid"]',
                '.captcha-error',
            ]
            
            for selector in fail_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            logger.info(f"❌ Найден признак неудачи: {selector}")
                            return False
                except:
                    continue
            
            # Проверяем, исчезла ли капча
            captcha_selectors = [
                '.captcha-container',
                '.puzzle-container',
                '[class*="captcha"]',
                '[class*="puzzle"]',
            ]
            
            for selector in captcha_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if not is_visible:
                            logger.info("✅ Капча исчезла")
                            return True
                    else:
                        logger.info("✅ Капча не найдена")
                        return True
                except:
                    continue
            
            # Если ничего не нашли - считаем, что капча прошла
            logger.info("⚠️ Не удалось определить статус, считаем успешным")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки результата: {e}")
            return False
    
    async def close(self):
        """Закрытие браузера"""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("✅ Браузер закрыт")
        except Exception as e:
            logger.error(f"❌ Ошибка закрытия браузера: {e}")


# ============ ФУНКЦИЯ ДЛЯ ВЫЗОВА ИЗ GRAM_BOT ============

async def solve_webapp_captcha(url: str, headless: bool = False) -> bool:
    """
    Решение WebApp капчи
    
    Args:
        url: URL WebApp
        headless: Запускать браузер в фоне
    
    Returns:
        True если капча решена
    """
    solver = CaptchaSolver(headless=headless)
    try:
        result = await solver.solve_drag_drop(url)
        return result
    finally:
        await solver.close()


# ============ ТЕСТ ============

async def test_solver():
    """Тестирование солвера"""
    url = "https://web.telegram.org/k/..."
    result = await solve_webapp_captcha(url, headless=False)
    print(f"Результат: {result}")


if __name__ == "__main__":
    asyncio.run(test_solver())
