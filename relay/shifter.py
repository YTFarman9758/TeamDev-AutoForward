"""
                      [TeamDev](https://t.me/team_x_og)
          
          Project Id -> 30.
          Project Name -> TeamDev Auto-Forward
          Project Age -> 1Month+ (Updated On 11/03/2026)
          Project Idea By -> @MR_ARMAN_08
          Project Dev -> @MR_ARMAN_08
          Powered By -> @Team_X_Og ( On Telegram )
          Updates -> @CrimeZone_Update ( On telegram )
    
    Setup Guides -> Read > README.md
    
          This Script Part Off https://t.me/Team_X_Og's Team.
          Copyright ©️ 2026 TeamDev | @Team_X_Og

    Compatible In BotApi 9.5 Fully
"""

import logging
import re as _re_module
from datetime import datetime, timezone
from re import sub as _re_sub
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton as IBtn
)
from vault import store
from relay.errors import handle_and_retry, ForwardResult
import environ

log = logging.getLogger("TeamDev.shifter")


def _media_type(msg: Message) -> str:
    if msg.photo:     return "photo"
    if msg.video:     return "video"
    if msg.document:  return "document"
    if msg.audio:     return "audio"
    if msg.voice:     return "audio"
    if msg.sticker:   return "sticker"
    if msg.animation: return "animation"
    return "text"


def _is_media(msg: Message) -> bool:
    return bool(
        msg.photo or msg.video or msg.document or msg.audio or
        msg.voice or msg.sticker or msg.animation or msg.video_note
    )


def _in_schedule(sched: dict) -> bool:
    if not sched.get("enabled"):
        return True
    try:
        import pytz
        tz   = pytz.timezone(sched.get("tz", "UTC"))
        now  = datetime.now(tz)
        sh   = sched.get("start_hour", 0)
        eh   = sched.get("end_hour", 23)
        h    = now.hour
        if sh <= eh:
            return sh <= h <= eh
        return h >= sh or h <= eh
    except Exception:
        return True


def _build_caption(original: str, mode: str, custom: str):
    if not custom and mode == "original":
        return None
    if mode == "replace":
        return custom or ""
    if mode == "prepend":
        return (f"{custom}\n\n{original}").strip() if original else custom
    if mode == "append":
        return (f"{original}\n\n{custom}").strip() if original else custom
    return None


def _build_reply_markup(existing_markup, pipe_buttons: list, remove_existing: bool):
    rows = []
    if not remove_existing and existing_markup:
        try:
            rows.extend(existing_markup.inline_keyboard)
        except Exception:
            pass
    for btn in pipe_buttons:
        text = (btn.get("text") or "").strip()
        url  = (btn.get("url")  or "").strip()
        if text and url:
            rows.append([IBtn(text, url=url)])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)

async def _send_to_target(
    client,
    msg,
    target,
    hide_tag,
    caption_override,
    reply_markup,
    pin_forwarded=False,
    silent_pin=True,
    no_link_preview=False,
):
    sent_msg = None
    needs_copy = hide_tag or (caption_override is not None) or (reply_markup is not None)

    if needs_copy:
        if _is_media(msg):
            kwargs = dict(
                chat_id      = target,
                from_chat_id = msg.chat.id,
                message_id   = msg.id,
            )
            if caption_override is not None:
                kwargs["caption"]    = caption_override
                kwargs["parse_mode"] = ParseMode.HTML
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            sent_msg = await client.copy_message(**kwargs)
        else:
            final_text = caption_override if caption_override is not None else (msg.text or "")
            kwargs = dict(
                chat_id    = target,
                text       = final_text,
                parse_mode = ParseMode.HTML,
            )
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            if no_link_preview:
                kwargs["disable_web_page_preview"] = True
            sent_msg = await client.send_message(**kwargs)
    else:
        if no_link_preview and not _is_media(msg):
            # forward_messages doesn't support disable_web_page_preview,
            # so we copy as send_message when link preview must be suppressed
            sent_msg = await client.send_message(
                chat_id                  = target,
                text                     = msg.text or "",
                parse_mode               = ParseMode.HTML,
                disable_web_page_preview = True,
            )
        else:
            sent_msg = await client.forward_messages(
                chat_id      = target,
                from_chat_id = msg.chat.id,
                message_ids  = msg.id,
            )

    if pin_forwarded and sent_msg:
        try:
            await client.pin_chat_message(
                chat_id              = target,
                message_id           = sent_msg.id,
                disable_notification = silent_pin,
            )
        except Exception:
            pass

    return sent_msg


async def relay_message(client: Client, msg: Message, pipe: dict):
    uid     = pipe["owner"]
    pid     = pipe["pipe_id"]
    targets = pipe.get("targets", [])

    if not targets:
        return

    if not _in_schedule(pipe.get("schedule", {})):
        await store.bump_stat(pid, uid, "skipped")
        return

    limit = pipe.get("fwd_limit", 0)
    if limit > 0:
        fwd_count = pipe.get("stats", {}).get("forwarded", 0)
        if fwd_count >= limit:
            if pipe.get("active"):
                await store.update_pipeline(uid, pid, active=False)
                log.info(f"[shifter] pipe={pid} auto-paused — limit {limit} reached")
                from core.logger import emit
                await emit(uid, "Pipeline Auto-Paused",
                           f"Pipe #{pid} hit limit {limit}", level="warn")
            return

    if pipe.get("dedup", True):
        if await store.is_seen(pid, msg.id):
            await store.bump_stat(pid, uid, "deduped")
            return
        await store.mark_seen(pid, msg.id)

    mf = pipe.get("media_filter", "all")
    if mf != "all" and _media_type(msg) != mf:
        await store.bump_stat(pid, uid, "skipped")
        return

    keywords = pipe.get("keywords", [])
    if keywords:
        content = (msg.text or msg.caption or "").lower()
        if not any(kw.lower() in content for kw in keywords):
            await store.bump_stat(pid, uid, "skipped")
            return

    blacklist = pipe.get("blacklist", [])
    if blacklist:
        content = (msg.text or msg.caption or "").lower()
        if any(bl.lower() in content for bl in blacklist):
            await store.bump_stat(pid, uid, "skipped")
            return

    transform = pipe.get("transform", {})
    if transform.get("regex_enabled") and transform.get("regex_pattern"):
        import re
        content = msg.text or msg.caption or ""
        try:
            matched = bool(re.search(transform["regex_pattern"], content, re.IGNORECASE))
        except Exception:
            matched = True
        invert = transform.get("invert_filter", False)
        if (matched and invert) or (not matched and not invert):
            await store.bump_stat(pid, uid, "skipped")
            return

    min_len = pipe.get("min_length", 0)
    if min_len > 0:
        content_len = len(msg.text or msg.caption or "")
        if content_len < min_len:
            await store.bump_stat(pid, uid, "skipped")
            return

    if pipe.get("dry_run", False):
        content_preview = (msg.text or msg.caption or "")[:80]
        log.info(f"[DRY RUN] pipe={pid} msg={msg.id} would forward to {len(targets)} targets: {content_preview!r}")
        try:
            from core.logger import emit
            await emit(uid, "Dry Run",
                       f"Pipe #{pid} — msg_id={msg.id} targets={len(targets)} preview={content_preview!r}",
                       level="info")
        except Exception:
            pass
        await store.bump_stat(pid, uid, "skipped")
        return

    original_text = msg.caption or msg.text or ""

    find_replace = pipe.get("find_replace", [])
    if find_replace and original_text:
        modified = original_text
        for rule in find_replace:
            find_str    = rule.get("find", "")
            replace_str = rule.get("replace", "")
            if find_str:
                try:
                    modified = modified.replace(find_str, replace_str)
                except Exception:
                    pass
        if modified != original_text:
            original_text = modified

    strip_opts = pipe.get("strip_opts", {})
    if original_text:
        if strip_opts.get("mentions"):
            original_text = _re_sub(r"@\w+", "", original_text).strip()
        if strip_opts.get("hashtags"):
            original_text = _re_sub(r"#\w+", "", original_text).strip()

    watermark = pipe.get("watermark", "")
    caption_custom = pipe.get("caption_text", "")
    if watermark:
        caption_custom = f"{watermark}\n{caption_custom}" if caption_custom else watermark

    cap_override = _build_caption(
        original = original_text,
        mode     = pipe.get("caption_mode", "original"),
        custom   = caption_custom,
    )
    raw_original = msg.caption or msg.text or ""
    if original_text != raw_original and cap_override is None:
        cap_override = original_text

    pipe_buttons   = pipe.get("inline_buttons", [])
    remove_buttons = transform.get("remove_buttons", False)
    reply_markup   = _build_reply_markup(
        existing_markup = msg.reply_markup,
        pipe_buttons    = pipe_buttons,
        remove_existing = remove_buttons,
    )

    hide_tag        = pipe.get("hide_tag", True)
    pin_fwd         = transform.get("pin_forwarded", False)
    silent_pin      = transform.get("silent_pin", True)
    no_link_preview = transform.get("no_link_preview", False)

    broken_targets = []
    sent_msgs      = []

    for target in targets:
        _sent_ref = []

        async def _do_send(t=target, ref=_sent_ref):
            m = await _send_to_target(
                client, msg, t,
                hide_tag         = hide_tag,
                caption_override = cap_override,
                reply_markup     = reply_markup,
                pin_forwarded    = pin_fwd,
                silent_pin       = silent_pin,
                no_link_preview  = no_link_preview,
            )
            if m:
                ref.append(m)

        result = await handle_and_retry(_do_send, max_retries=3)

        if result == ForwardResult.OK:
            await store.bump_stat(pid, uid, "forwarded")
            log.debug(f"[shifter] pipe={pid} forwarded msg={msg.id} -> {target}")
            if _sent_ref:
                sent_msgs.append((target, _sent_ref[0].id))

        elif result == ForwardResult.PERM_ERR:
            await store.bump_stat(pid, uid, "errors")
            broken_targets.append(target)
            log.warning(f"[shifter] pipe={pid} permanent error for target={target}")

        else:
            await store.bump_stat(pid, uid, "skipped")


    auto_delete_mins = pipe.get("auto_delete", 0)
    if auto_delete_mins > 0 and sent_msgs:
        import asyncio
        async def _delete_later(c, msgs, delay_secs):
            await asyncio.sleep(delay_secs)
            for chat_id, msg_id in msgs:
                try:
                    await c.delete_messages(chat_id, msg_id)
                except Exception:
                    pass
        asyncio.create_task(_delete_later(client, sent_msgs, auto_delete_mins * 60))

    if broken_targets:
        try:
            from core.logger import emit
            detail = f"Pipe #{pid} broken targets: {', '.join(broken_targets)}"
            await emit(uid, "Broken Targets Detected", detail, level="error")
            await client.send_message(
                uid,
                f"<b>⚑ TeamDev Warning</b>\n"
                f"Pipeline <b>#{pid}</b> has unreachable targets:\n"
                + "\n".join(f"<code>{t}</code>" for t in broken_targets)
                + "\n<i>Remove or fix these targets to suppress this warning.</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
