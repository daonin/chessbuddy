from __future__ import annotations
import io
import os
import asyncio
from typing import Optional
import re

import httpx
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from .chess_images import fen_to_png_bytes

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

USERS: dict[int, dict] = {}
LAST_TASK_MSG: dict[int, dict] = {}  # deprecated; no longer used for routing


async def api_get(path: str, **params):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()


async def api_post(path: str, json_body: Optional[dict] = None, params: Optional[dict] = None):
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{API_BASE}{path}", json=json_body, params=params)
        r.raise_for_status()
        return r.json()


async def ensure_internal_user(update: Update) -> int:
    tg_id = str(update.effective_user.id)
    rec = USERS.setdefault(update.effective_user.id, {})
    if rec.get("user_id"):
        return rec["user_id"]
    display = (update.effective_user.full_name or update.effective_user.username or f"tg_{tg_id}")
    res = await api_post("/users/ensure_external", json_body={
        "provider": "telegram",
        "external_user_id": tg_id,
        "external_username": update.effective_user.username,
        "display_name": display,
    })
    rec["user_id"] = res["user_id"]
    return rec["user_id"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_internal_user(update)
    await update.message.reply_text(
        "Привет! Я ChessBuddy. Команды:\n"
        "/import_chesscom <username> [months] — импортировать партии с chess.com\n"
        "/status — статус индексации\n"
        "/task [category] — задачка; по умолчанию blunder\n"
        "/analyse — проанализировать все ожидающие партии (только твои)\n"
        "/reanalyse <game_id>|last [--clear] — переанализ одной игры\n"
        "/reclassify <game_id>|last — переклассифицировать хайлайты без движка\n"
        "Ответ на задачу отправляй реплаем на картинку в формате UCI (e2e4) или командой /answer e2e4."
    )


async def import_chesscom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи username: /import_chesscom <username> [months]")
        return
    username = context.args[0]
    months = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 3
    internal_user_id = await ensure_internal_user(update)
    USERS[update.effective_user.id]["username"] = username
    await update.message.reply_text(f"Запускаю импорт {months} мес. для {username}…")
    try:
        res = await api_post("/import/chesscom/job", json_body={
            "username": username,
            "months": months,
            "initiated_by_user_id": internal_user_id,
        })
        await update.message.reply_text(f"Импорт запущен: job_id={res.get('job_id')}, imported={res.get('imported')}, skipped={res.get('skipped')}")
    except Exception as e:  # noqa
        await update.message.reply_text(f"Ошибка запуска импорта: {e}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    internal_user_id = await ensure_internal_user(update)
    try:
        res = await api_get("/status", user_id=internal_user_id)
        job = res.get("last_import_job")
        job_line = ""
        if job:
            pm = job.get("processed_months", 0)
            tm = job.get("total_months", 0)
            job_line = f"\nПоследний импорт: {job.get('status')} {pm}/{tm}, игр: +{job.get('imported_games',0)}/~{job.get('total_games',0)}"
        await update.message.reply_text(
            f"Пользователь id={internal_user_id}\n"
            f"Партии: {res['analysed_games']}/{res['total_games']} ({res['progress_percent'] or 0}%)\n"
            f"Хайлайты: {res['total_highlights']}\n"
            f"Задачи: new={res.get('tasks',{}).get('new',0)}, answered={res.get('tasks',{}).get('answered',0)}" + job_line
        )
    except Exception as e:
        await update.message.reply_text(f"API недоступно: {e}")


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    internal_user_id = await ensure_internal_user(update)
    category = context.args[0] if context.args else "blunder"
    try:
        body = {"user_id": internal_user_id, "category": category, "own_side_only": True}
        res = await api_post("/tasks/random", json_body=body)
        task_id = res["task_id"]
        task = await api_get(f"/tasks/{task_id}")
        fen = task.get("fen")
        last_move_uci = None
        try:
            if task and task.get("game_id") and task.get("position_ply") is not None:
                g = await api_get(f"/games/{task['game_id']}")
                for mv in g.get("moves", []):
                    if int(mv.get("ply", -1)) == int(task["position_ply"]):
                        last_move_uci = mv.get("uci")
                        break
        except Exception:
            last_move_uci = None
        if not fen:
            await update.message.reply_text("Не удалось получить задачу (нет позиции).")
            return
        img = fen_to_png_bytes(fen, last_move_uci=last_move_uci)
        caption = (
            f"Задача #{task_id} ({category}). "
            f"Ответь реплаем ходом в формате UCI (e2e4) или /answer e2e4 в ответ на это сообщение."
        )
        await update.message.reply_photo(InputFile(io.BytesIO(img), filename="task.png"), caption=caption)
    except Exception as e:  # noqa
        await update.message.reply_text(f"Не удалось получить задачу: {e}")


async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    internal_user_id = await ensure_internal_user(update)
    total_processed = 0
    total_selected = 0
    errors = 0
    for _ in range(20):
        try:
            # Обрабатываем по одной партии за вызов, чтобы избежать 3-минутных таймаутов
            data = await api_post("/analyse/pending", params={"user_id": internal_user_id, "limit": 1, "background": "true"})
            total_selected += data.get("selected", 0)
            total_processed += data.get("processed", 0)
            errors += len(data.get("errors", []))
            if data.get("selected", 0) == 0 or data.get("processed", 0) == 0:
                break
        except Exception as e:  # noqa
            await update.message.reply_text(f"Ошибка анализа: {e}")
            break
    await update.message.reply_text(f"Анализ завершен: обработано {total_processed}, ошибок {errors}.")


async def _resolve_game_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    internal_user_id = await ensure_internal_user(update)
    args = list(context.args or [])
    args_wo_flags = [a for a in args if not a.startswith("--")]
    if not args_wo_flags:
        return None
    first = args_wo_flags[0]
    if first.isdigit():
        return int(first)
    if first.lower() == "last":
        try:
            res = await api_get("/games", user_id=internal_user_id, limit=1)
            items = res.get("items", [])
            if items:
                return int(items[0]["id"])
        except Exception:
            return None
    return None


async def reanalyse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_internal_user(update)
    game_id = await _resolve_game_id(update, context)
    if not game_id:
        await update.message.reply_text("Формат: /reanalyse <game_id>|last [--clear]")
        return
    clear = any(a == "--clear" for a in (context.args or []))
    await update.message.reply_text(f"Запускаю переанализ игры {game_id}{' с очисткой' if clear else ''}…")
    try:
        await api_post(f"/games/{game_id}/reanalyse", params={"clear_tasks": str(clear).lower()})
        await update.message.reply_text("Переанализ завершен.")
    except Exception as e:  # noqa
        await update.message.reply_text(f"Ошибка реанализа: {e}")


async def reclassify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_internal_user(update)
    game_id = await _resolve_game_id(update, context)
    if not game_id:
        await update.message.reply_text("Формат: /reclassify <game_id>|last")
        return
    await update.message.reply_text(f"Переклассификация хайлайтов для игры {game_id}…")
    try:
        await api_post(f"/games/{game_id}/reclassify_highlights")
        await update.message.reply_text("Готово.")
    except Exception as e:  # noqa
        await update.message.reply_text(f"Ошибка переклассификации: {e}")


async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Ответь реплаем на сообщение с задачей, либо используй /answer <ход> в ответ на сообщение с задачей")
        return
    # Extract task_id from replied-to message caption/text
    replied = update.message.reply_to_message
    source_text = (replied.caption or replied.text or "")
    m = re.search(r"Задача\s*#(\d+)", source_text)
    if not m:
        await update.message.reply_text("Не удалось определить задачу. Ответь на сообщение с задачей.")
        return
    task_id = int(m.group(1))
    move = (update.message.text or "").strip()
    if len(move) < 4:
        await update.message.reply_text("Формат хода должен быть UCI, например e2e4")
        return
    try:
        internal_user_id = await ensure_internal_user(update)
        res = await api_post(f"/tasks/{task_id}/verify", json_body={
            "move_uci": move,
            "user_id": internal_user_id,
            "response_ms": 0,
        })
        ok = res.get("is_correct")
        best = res.get("engine_best_move_uci")
        await update.message.reply_text("Верно!" if ok else f"Неверно. Лучший ход: {best}")
    except Exception as e:  # noqa
        await update.message.reply_text(f"Ошибка проверки: {e}")


async def answer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Формат: /answer <ход в UCI> — используйте в ответ на сообщение с задачей")
        return
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Используй /answer в ответ на сообщение с задачей")
        return
    replied = update.message.reply_to_message
    source_text = (replied.caption or replied.text or "")
    m = re.search(r"Задача\s*#(\d+)", source_text)
    if not m:
        await update.message.reply_text("Не удалось определить задачу. Ответь на сообщение с задачей.")
        return
    task_id = int(m.group(1))
    move = (context.args[0] or "").strip()
    if len(move) < 4:
        await update.message.reply_text("Формат хода должен быть UCI, например e2e4")
        return
    try:
        internal_user_id = await ensure_internal_user(update)
        res = await api_post(f"/tasks/{task_id}/verify", json_body={
            "move_uci": move,
            "user_id": internal_user_id,
            "response_ms": 0,
        })
        ok = res.get("is_correct")
        best = res.get("engine_best_move_uci")
        await update.message.reply_text("Верно!" if ok else f"Неверно. Лучший ход: {best}")
    except Exception as e:  # noqa
        await update.message.reply_text(f"Ошибка проверки: {e}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("import_chesscom", import_chesscom))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("task", task))
    app.add_handler(CommandHandler("analyse", analyse))
    app.add_handler(CommandHandler("answer", answer_cmd))
    app.add_handler(CommandHandler("reanalyse", reanalyse_cmd))
    app.add_handler(CommandHandler("reclassify", reclassify_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), reply_handler))
    app.run_polling()


if __name__ == "__main__":
    main()
