"""
Модуль скачивания видео: YouTube (с выбором качества) и TikTok.

Специально используются только уже смешанные (видео+аудио вместе)
форматы YouTube — это исключает необходимость в ffmpeg на сервере для
склейки, но ограничивает максимальное доступное качество (обычно до
720p — более высокие качества YouTube почти всегда отдаёт раздельными
потоками).
"""

import logging
import os
import asyncio
import tempfile
import glob
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

router = Router()

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ — лимит Bot API на загрузку файла ботом


class VideoStates(StatesGroup):
    waiting_youtube_link = State()
    waiting_tiktok_link = State()


# Временное хранилище ссылок между "прислал ссылку" и "выбрал качество".
_pending_youtube: dict = {}


def get_video_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ YouTube", callback_data="video_youtube")],
        [InlineKeyboardButton(text="🎵 TikTok", callback_data="video_tiktok")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


@router.callback_query(lambda c: c.data == "video_menu")
async def video_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎥 <b>Скачать видео</b>\n\nВыбери источник:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_video_menu_keyboard()
    )


# ============ YOUTUBE ============

@router.callback_query(lambda c: c.data == "video_youtube")
async def video_youtube_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(VideoStates.waiting_youtube_link)
    await callback.message.edit_text(
        "▶️ Пришли ссылку на видео YouTube.\n\n/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


def _extract_youtube_formats(url: str):
    import yt_dlp
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    formats = info.get("formats", [])
    # Берём только форматы, где видео и аудио УЖЕ склеены в один поток
    # (progressive) — раздельные bestvideo+bestaudio требуют ffmpeg для
    # склейки, а он не гарантированно доступен на хостинге.
    seen_heights = {}
    for f in formats:
        height = f.get("height")
        if not height:
            continue
        if f.get("vcodec") in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        seen_heights[height] = True
    return info, sorted(seen_heights.keys(), reverse=True)


@router.message(VideoStates.waiting_youtube_link)
async def video_youtube_link_input(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    if url.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_video_menu_keyboard())
        return
    if "youtube.com" not in url and "youtu.be" not in url:
        await message.answer("❌ Это не похоже на ссылку YouTube. Попробуй снова, или /cancel.")
        return
    
    await state.clear()
    wait_msg = await message.answer("🔎 Получаю доступные качества...")
    
    try:
        loop = asyncio.get_event_loop()
        info, heights = await loop.run_in_executor(None, _extract_youtube_formats, url)
    except Exception as e:
        logging.error(f"❌ video_youtube_link_input: {e}")
        await wait_msg.edit_text(f"❌ Не удалось получить информацию о видео: {e}")
        return
    
    if not heights:
        await wait_msg.edit_text("❌ Не удалось определить доступные качества для этого видео.")
        return
    
    key = f"{message.from_user.id}_{message.message_id}"
    _pending_youtube[key] = {"url": url, "title": info.get("title", "video")}
    
    buttons = []
    row = []
    for height in heights[:9]:
        row.append(InlineKeyboardButton(text=f"{height}p", callback_data=f"yt_dl_{key}_{height}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="video_menu")])
    
    title = (info.get('title') or 'Видео')[:150]
    await wait_msg.edit_text(
        f"▶️ <b>{title}</b>\n\nВыбери качество для скачивания:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


def _download_youtube(url: str, height: int, out_path: str):
    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        # Только уже смешанные (видео+аудио вместе) форматы — без "+" и
        # без merge_output_format, чтобы не требовался ffmpeg на сервере.
        "format": f"best[height<={height}][vcodec!=none][acodec!=none]/best[height<={height}]",
        "outtmpl": out_path,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


@router.callback_query(lambda c: c.data.startswith("yt_dl_"))
async def video_youtube_download(callback: types.CallbackQuery):
    try:
        rest = callback.data[len("yt_dl_"):]
        key, _, height_str = rest.rpartition("_")
        height = int(height_str)
        
        pending = _pending_youtube.get(key)
        if not pending:
            await callback.answer("❌ Сессия скачивания устарела, начни заново", show_alert=True)
            return
        
        await callback.answer("⬇️ Начинаю скачивание...")
        await callback.message.edit_text(f"⬇️ Скачиваю в {height}p...")
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_template = os.path.join(tmp_dir, "video.%(ext)s")
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, _download_youtube, pending["url"], height, out_template)
            except Exception as e:
                logging.error(f"❌ video_youtube_download: {e}")
                await callback.message.edit_text(f"❌ Ошибка скачивания: {e}")
                return
            
            files = [f for f in glob.glob(os.path.join(tmp_dir, "video.*")) if not f.endswith(('.part', '.ytdl'))]
            if not files:
                await callback.message.edit_text("❌ Файл не найден после скачивания.")
                return
            
            file_path = files[0]
            size = os.path.getsize(file_path)
            if size > MAX_TELEGRAM_FILE_SIZE:
                await callback.message.edit_text(
                    f"❌ Файл слишком большой ({size // (1024*1024)} МБ) — Telegram-боты "
                    f"не могут отправлять файлы больше 50 МБ. Попробуй меньшее качество."
                )
                return
            
            await callback.message.edit_text("📤 Отправляю видео...")
            await callback.message.answer_video(
                FSInputFile(file_path),
                caption=pending.get("title", "")[:1024]
            )
        
        _pending_youtube.pop(key, None)
    except Exception as e:
        logging.error(f"❌ video_youtube_download outer: {e}")
        await callback.message.answer(f"❌ Ошибка: {e}")


# ============ TIKTOK ============

TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def get_tiktok_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Скачать видео", callback_data="tiktok_video")],
        [InlineKeyboardButton(text="🖼 Скачать фото", callback_data="tiktok_photo")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="video_menu")],
    ])


@router.callback_query(lambda c: c.data == "video_tiktok")
async def video_tiktok_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎵 <b>TikTok</b>\n\nЧто скачать — видео или фото (слайд-шоу)?",
        parse_mode=ParseMode.HTML,
        reply_markup=get_tiktok_menu_keyboard()
    )


@router.callback_query(lambda c: c.data == "tiktok_video")
async def video_tiktok_video_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(tiktok_mode="video")
    await state.set_state(VideoStates.waiting_tiktok_link)
    await callback.message.edit_text(
        "🎥 Пришли ссылку на видео TikTok.\n\n/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(lambda c: c.data == "tiktok_photo")
async def video_tiktok_photo_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(tiktok_mode="photo")
    await state.set_state(VideoStates.waiting_tiktok_link)
    await callback.message.edit_text(
        "🖼 Пришли ссылку на фото-пост (слайд-шоу) TikTok.\n\n/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


def _extract_tiktok_info(url: str):
    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": TIKTOK_HEADERS,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_tiktok_video(url: str, out_path: str):
    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "best",
        "outtmpl": out_path,
        "http_headers": TIKTOK_HEADERS,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


@router.message(VideoStates.waiting_tiktok_link)
async def video_tiktok_link_input(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    data = await state.get_data()
    mode = data.get("tiktok_mode", "video")
    
    if url.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_tiktok_menu_keyboard())
        return
    if "tiktok.com" not in url:
        await message.answer("❌ Это не похоже на ссылку TikTok. Попробуй снова, или /cancel.")
        return
    
    await state.clear()
    
    if mode == "photo":
        await _handle_tiktok_photo(message, url)
    else:
        await _handle_tiktok_video(message, url)


async def _handle_tiktok_video(message: types.Message, url: str):
    wait_msg = await message.answer("⬇️ Скачиваю видео...")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_template = os.path.join(tmp_dir, "tiktok.%(ext)s")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _download_tiktok_video, url, out_template)
        except Exception as e:
            logging.error(f"❌ _handle_tiktok_video: {e}")
            await wait_msg.edit_text(
                f"❌ Ошибка скачивания: {e}\n\n"
                f"<i>TikTok часто меняет защиту от скачивания — если ошибка "
                f"про 'Unable to extract' или похожая, попробуй ещё раз позже "
                f"или обнови yt-dlp на сервере (pip install -U yt-dlp). Также "
                f"проверь, что ссылка ведёт на видео, а не на фото-слайд-шоу — "
                f"для них есть отдельная кнопка «Скачать фото».</i>",
                parse_mode=ParseMode.HTML
            )
            return
        
        files = [f for f in glob.glob(os.path.join(tmp_dir, "tiktok.*")) if not f.endswith(('.part', '.ytdl'))]
        if not files:
            await wait_msg.edit_text("❌ Файл не найден после скачивания.")
            return
        
        file_path = files[0]
        size = os.path.getsize(file_path)
        if size > MAX_TELEGRAM_FILE_SIZE:
            await wait_msg.edit_text(
                f"❌ Файл слишком большой ({size // (1024*1024)} МБ) для отправки ботом."
            )
            return
        
        await wait_msg.edit_text("📤 Отправляю видео...")
        await message.answer_video(FSInputFile(file_path))


async def _handle_tiktok_photo(message: types.Message, url: str):
    wait_msg = await message.answer("🔎 Получаю фото...")
    loop = asyncio.get_event_loop()
    
    try:
        info = await loop.run_in_executor(None, _extract_tiktok_info, url)
    except Exception as e:
        logging.error(f"❌ _handle_tiktok_photo extract: {e}")
        await wait_msg.edit_text(f"❌ Не удалось получить информацию о посте: {e}")
        return
    
    # TikTok фото-слайд-шоу отдаются yt-dlp как набор изображений в
    # info['image'] / info['images'] / info['entries'] в зависимости от
    # версии yt-dlp — проверяем все варианты.
    image_urls = []
    if info.get("images"):
        image_urls = [img.get("url") if isinstance(img, dict) else img for img in info["images"]]
    elif info.get("image"):
        image_urls = [info["image"]]
    elif info.get("entries"):
        for entry in info["entries"]:
            if entry.get("ext") in ("jpg", "jpeg", "png", "webp"):
                image_urls.append(entry.get("url"))
    
    image_urls = [u for u in image_urls if u]
    
    if not image_urls:
        await wait_msg.edit_text(
            "❌ Это похоже на обычное видео, а не фото-слайд-шоу. "
            "Попробуй кнопку «Скачать видео»."
        )
        return
    
    await wait_msg.edit_text(f"📤 Отправляю {len(image_urls)} фото...")
    
    from aiogram.types import InputMediaPhoto
    media = [InputMediaPhoto(media=u) for u in image_urls[:10]]
    try:
        await message.answer_media_group(media)
    except Exception as e:
        logging.error(f"❌ _handle_tiktok_photo send: {e}")
        await message.answer(f"❌ Ошибка отправки фото: {e}")


# ============ ИНИЦИАЛИЗАЦИЯ ============

_video_router_initialized = False


def init_video_download(dp):
    global _video_router_initialized
    if not _video_router_initialized:
        dp.include_router(router)
        _video_router_initialized = True
        logging.info("✅ Модуль скачивания видео инициализирован")


__all__ = [
    'router',
    'init_video_download',
    'get_video_menu_keyboard',
            ]
