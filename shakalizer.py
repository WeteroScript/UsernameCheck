"""
Модуль "Шакализатор": ухудшение качества присланного фото —
уменьшение разрешения + сильное jpeg-сжатие с растяжением обратно,
классический эффект "шакала".
"""

import io
import logging
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from PIL import Image

router = Router()

# Целевые "высоты" — по аналогии с качеством видео (240p и ниже),
# уменьшаются до предела, при котором фото ещё остаётся фото (не точка).
QUALITY_LEVELS = [240, 180, 144, 120, 96, 64, 48, 32, 16, 8]


class ShakalStates(StatesGroup):
    waiting_photo = State()


_pending_photos: dict = {}  # key -> file_id


def get_shakal_quality_keyboard(key: str) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for q in QUALITY_LEVELS:
        row.append(InlineKeyboardButton(text=f"{q}p", callback_data=f"shakal_q_{key}_{q}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(lambda c: c.data == "shakalizer_menu")
async def shakalizer_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ShakalStates.waiting_photo)
    await callback.message.edit_text(
        "📉 <b>Шакализатор</b>\n\n"
        "Пришли фото — ухудшу его качество на выбор, от 240p и ниже.\n\n"
        "/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


@router.message(ShakalStates.waiting_photo, lambda m: m.photo is not None)
async def shakal_photo_input(message: types.Message, state: FSMContext):
    await state.clear()
    key = f"{message.from_user.id}_{message.message_id}"
    _pending_photos[key] = message.photo[-1].file_id
    await message.answer(
        "📉 Выбери качество:",
        reply_markup=get_shakal_quality_keyboard(key)
    )


@router.message(ShakalStates.waiting_photo)
async def shakal_photo_wrong_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ]))
        return
    await message.answer("❌ Пришли именно фото (как изображение, не файлом).")


def _shakalize(image_bytes: bytes, target_height: int) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = img.size
    scale = target_height / orig_h
    small_w = max(1, round(orig_w * scale))
    small_h = max(1, target_height)
    
    # Уменьшаем и сильно сжимаем в jpeg — это и даёт "шакальные" артефакты.
    small = img.resize((small_w, small_h), Image.BILINEAR)
    buf = io.BytesIO()
    # Чем ниже целевое качество, тем сильнее сжатие jpeg.
    jpeg_quality = max(3, min(15, target_height // 4))
    small.save(buf, format="JPEG", quality=jpeg_quality)
    buf.seek(0)
    degraded_small = Image.open(buf).convert("RGB")
    
    # Растягиваем обратно до исходного размера, чтобы блочность/артефакты
    # были хорошо заметны (классический эффект "шакала").
    result = degraded_small.resize((orig_w, orig_h), Image.NEAREST)
    out = io.BytesIO()
    result.save(out, format="JPEG", quality=40)
    out.seek(0)
    return out.read()


@router.callback_query(lambda c: c.data.startswith("shakal_q_"))
async def shakal_quality_callback(callback: types.CallbackQuery):
    try:
        rest = callback.data[len("shakal_q_"):]
        key, _, quality_str = rest.rpartition("_")
        quality = int(quality_str)
        
        file_id = _pending_photos.get(key)
        if not file_id:
            await callback.answer("❌ Сессия устарела, пришли фото заново", show_alert=True)
            return
        
        await callback.answer("📉 Ухудшаю качество...")
        
        file = await callback.bot.get_file(file_id)
        file_bytes_io = await callback.bot.download_file(file.file_path)
        image_bytes = file_bytes_io.read()
        
        result_bytes = _shakalize(image_bytes, quality)
        
        await callback.message.answer_photo(
            BufferedInputFile(result_bytes, filename=f"shakal_{quality}p.jpg"),
            caption=f"📉 Качество: {quality}p"
        )
    except Exception as e:
        logging.error(f"❌ shakal_quality_callback: {e}")
        await callback.message.answer(f"❌ Ошибка обработки фото: {e}")


# ============ ИНИЦИАЛИЗАЦИЯ ============

_shakalizer_router_initialized = False


def init_shakalizer(dp):
    global _shakalizer_router_initialized
    if not _shakalizer_router_initialized:
        dp.include_router(router)
        _shakalizer_router_initialized = True
        logging.info("✅ Модуль 'Шакализатор' инициализирован")


__all__ = [
    'router',
    'init_shakalizer',
]
