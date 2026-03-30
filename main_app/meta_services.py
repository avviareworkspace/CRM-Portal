"""
Meta webhooks: WhatsApp Cloud API, Instagram messaging, Facebook Page (Messenger).
https://developers.facebook.com/docs/graph-api/webhooks
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import requests
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def effective_meta_config():
    """DB settings with optional env override (env wins)."""
    from .models import MetaIntegrationSettings

    s = MetaIntegrationSettings.get_solo()
    return {
        "verify_token": _env("META_VERIFY_TOKEN") or s.verify_token,
        "app_secret": _env("META_APP_SECRET") or s.app_secret,
        "access_token": _env("META_ACCESS_TOKEN") or s.access_token,
        "whatsapp_phone_number_id": _env("META_WHATSAPP_PHONE_NUMBER_ID")
        or s.whatsapp_phone_number_id,
        "whatsapp_enabled": s.whatsapp_enabled,
        "instagram_enabled": s.instagram_enabled,
        "facebook_messenger_enabled": s.facebook_messenger_enabled,
        "notify_admins": s.notify_admins_on_message,
        "facebook_page_id": _env("META_FACEBOOK_PAGE_ID") or (s.facebook_page_id or ""),
    }


def verify_webhook(mode: str, token: str, challenge: str, cfg: dict) -> Optional[str]:
    if mode == "subscribe" and token and cfg.get("verify_token") and token == cfg["verify_token"]:
        return challenge
    logger.warning("Meta webhook verify failed (mode=%s)", mode)
    return None


def verify_signature(raw_body: bytes, sig_header: Optional[str], app_secret: str) -> bool:
    if not app_secret:
        logger.warning("META_APP_SECRET / app_secret empty — webhook signature not verified")
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = sig_header[7:]
    return hmac.compare_digest(expected, received)


def _digits_phone(s: str, max_len: int = 15) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-max_len:] if len(d) > max_len else d


def _placeholder_email(channel: str, sender_id: str) -> str:
    safe = re.sub(r"[^\w]", "", sender_id)[:40] or "unknown"
    return f"{channel.lower()}.{safe}@example.com"


def _ensure_lead_source(name: str) -> Any:
    from .models import LeadSource

    src, _ = LeadSource.objects.get_or_create(
        name=name,
        defaults={"description": f"Inbound from {name}", "is_active": True},
    )
    return src


def _append_notes(lead, line: str) -> None:
    line = (line or "").strip()
    if not line:
        return
    prev = (lead.notes or "").strip()
    lead.notes = f"{prev}\n{line}".strip() if prev else line


_CHANNEL_TO_THREAD = {
    "WhatsApp": "whatsapp",
    "Instagram": "instagram",
    "Facebook": "facebook",
}


def ingest_inbound_message(
    *,
    channel: str,
    sender_id: str,
    sender_label: str,
    text: str,
    notify_admins: bool,
    page_or_waba_id: str = "",
    external_message_id: str = "",
) -> None:
    from .models import (
        Admin,
        Lead,
        NotificationAdmin,
        SocialChatMessage,
        SocialChatThread,
    )

    if not text.strip():
        return

    thread_channel = _CHANNEL_TO_THREAD.get(
        channel, SocialChatThread.CHANNEL_WHATSAPP
    )
    if thread_channel == SocialChatThread.CHANNEL_WHATSAPP:
        phone = _digits_phone(sender_id)
        if not phone:
            phone = _digits_phone(sender_label) or "0"
    else:
        d = re.sub(r"\D", "", str(sender_id))
        phone = (d[-15:] if len(d) > 15 else d) or (str(sender_id)[:15] if sender_id else "0")

    source = _ensure_lead_source(channel)
    snippet = text.strip()[:2000]
    note_line = f"[{channel}] {snippet}"
    page_key = (page_or_waba_id or "")[:128]
    with transaction.atomic():
        lead = (
            Lead.objects.select_for_update()
            .filter(phone=phone, source=source)
            .order_by("-created_at")
            .first()
        )

        if lead is None:
            fn = (sender_label or channel)[:100]
            ln = "Chat"
            if " " in fn:
                parts = fn.split(None, 1)
                fn, ln = parts[0][:100], (parts[1][:100] if parts[1] else "Chat")

            lead = Lead(
                first_name=fn or channel,
                last_name=ln,
                email=_placeholder_email(channel, sender_id),
                phone=phone,
                source=source,
                status="NEW",
                notes=note_line,
            )
            lead.save()
            logger.info("Created lead %s from %s", lead.lead_id, channel)
        else:
            _append_notes(lead, note_line)
            lead.save(update_fields=["notes", "updated_at"])
            logger.info("Updated lead %s from %s", lead.lead_id, channel)

        thread, _ = SocialChatThread.objects.get_or_create(
            channel=thread_channel,
            external_user_id=str(sender_id)[:128],
            page_or_waba_id=page_key,
            defaults={
                "display_name": (sender_label or "")[:200],
                "lead": lead,
                "last_message_preview": snippet[:300],
                "last_message_at": timezone.now(),
            },
        )
        thread.display_name = ((sender_label or thread.display_name) or "")[:200]
        if thread.lead_id is None:
            thread.lead = lead
        thread.last_message_preview = snippet[:300]
        thread.last_message_at = timezone.now()
        thread.save(
            update_fields=[
                "display_name",
                "lead",
                "last_message_preview",
                "last_message_at",
                "updated_at",
            ]
        )
        SocialChatMessage.objects.create(
            thread=thread,
            direction=SocialChatMessage.DIRECTION_IN,
            body=snippet,
            external_message_id=(external_message_id or "")[:128],
        )

    if notify_admins:
        preview = f"{channel} ({sender_label or phone}): {text.strip()[:400]}"
        for row in Admin.objects.select_related("admin"):
            try:
                NotificationAdmin.objects.create(
                    admin=row.admin,
                    message=preview[:2000],
                )
            except Exception:
                logger.exception("Failed admin notification for Meta inbound")


def process_whatsapp_payload(body: Dict[str, Any], cfg: dict) -> None:
    if not cfg.get("whatsapp_enabled"):
        return
    if body.get("object") != "whatsapp_business_account":
        return

    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            contacts = {}
            for c in value.get("contacts") or []:
                wid = c.get("wa_id")
                if wid:
                    contacts[wid] = (c.get("profile") or {}).get("name") or ""

            for msg in value.get("messages") or []:
                if msg.get("type") != "text":
                    continue
                frm = msg.get("from")
                body_text = ((msg.get("text") or {}).get("body")) or ""
                name = contacts.get(frm, "WhatsApp user")
                metadata = value.get("metadata") or {}
                phone_number_id = str(metadata.get("phone_number_id") or "")
                mid = str(msg.get("id") or "")
                ingest_inbound_message(
                    channel="WhatsApp",
                    sender_id=str(frm),
                    sender_label=name,
                    text=body_text,
                    notify_admins=cfg.get("notify_admins", True),
                    page_or_waba_id=phone_number_id,
                    external_message_id=mid,
                )


def _ingest_messaging_webhook(body: Dict[str, Any], channel_label: str, cfg: dict) -> None:
    for entry in body.get("entry") or []:
        entry_id = str(entry.get("id") or "")
        for ev in entry.get("messaging") or []:
            message = ev.get("message") or {}
            if "text" not in message:
                continue
            sender_id = str((ev.get("sender") or {}).get("id") or "")
            text = message.get("text") or ""
            mid = str(message.get("mid") or "")
            ingest_inbound_message(
                channel=channel_label,
                sender_id=sender_id,
                sender_label=sender_id,
                text=text,
                notify_admins=cfg.get("notify_admins", True),
                page_or_waba_id=entry_id,
                external_message_id=mid,
            )


def process_instagram_payload(body: Dict[str, Any], cfg: dict) -> None:
    if not cfg.get("instagram_enabled"):
        return
    if body.get("object") != "instagram":
        return
    _ingest_messaging_webhook(body, "Instagram", cfg)


def process_facebook_page_payload(body: Dict[str, Any], cfg: dict) -> None:
    if not cfg.get("facebook_messenger_enabled"):
        return
    if body.get("object") != "page":
        return
    _ingest_messaging_webhook(body, "Facebook", cfg)


def handle_meta_webhook_json(raw: bytes, cfg: Optional[dict] = None) -> None:
    cfg = cfg or effective_meta_config()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Meta webhook JSON parse failed")
        return

    process_whatsapp_payload(body, cfg)
    process_instagram_payload(body, cfg)
    process_facebook_page_payload(body, cfg)


def send_whatsapp_text(to_digits: str, body: str) -> tuple[bool, str]:
    """
    Send a WhatsApp text message via Cloud API.
    `to_digits` should be country code + number, digits only (no +).
    """
    cfg = effective_meta_config()
    token = cfg.get("access_token")
    phone_id = cfg.get("whatsapp_phone_number_id")
    if not token or not phone_id:
        return False, "Missing access token or WhatsApp phone number ID"

    to_clean = _digits_phone(to_digits, 15)
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_clean,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    r = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code >= 400:
        return False, r.text[:500]
    return True, "ok"


def send_messenger_text(
    page_id: str, recipient_psid: str, body: str, access_token: str
) -> tuple[bool, str]:
    """Send Facebook Page or Instagram (via connected Page) text; requires Page access token."""
    if not access_token:
        return False, "Missing access token"
    if not page_id:
        return False, "Missing Page ID (webhook entry id or Facebook Page ID in settings)"
    pid = page_id.strip()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{pid}/messages"
    payload = {
        "recipient": {"id": recipient_psid},
        "messaging_type": "RESPONSE",
        "message": {"text": body[:2000]},
    }
    r = requests.post(
        url,
        params={"access_token": access_token},
        json=payload,
        timeout=30,
    )
    if r.status_code >= 400:
        return False, r.text[:500]
    return True, "ok"


def send_thread_reply(thread, body: str) -> tuple[bool, str]:
    """Dispatch outbound by thread channel (WhatsApp vs Messenger/Instagram)."""
    from .models import SocialChatThread

    cfg = effective_meta_config()
    token = cfg.get("access_token") or ""
    text = (body or "").strip()
    if not text:
        return False, "Empty message"

    if thread.channel == SocialChatThread.CHANNEL_WHATSAPP:
        return send_whatsapp_text(thread.external_user_id, text)

    page_id = (thread.page_or_waba_id or cfg.get("facebook_page_id") or "").strip()
    if thread.channel in (
        SocialChatThread.CHANNEL_FACEBOOK,
        SocialChatThread.CHANNEL_INSTAGRAM,
    ):
        return send_messenger_text(
            page_id, thread.external_user_id, text, token
        )

    return False, "Unsupported channel"
