import logging

from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .forms import MetaIntegrationSettingsForm
from .meta_services import (
    effective_meta_config,
    handle_meta_webhook_json,
    send_thread_reply,
    verify_signature,
    verify_webhook,
)
from .models import MetaIntegrationSettings, SocialChatMessage, SocialChatThread
from .utils import admin_perm_required, user_type_required

logger = logging.getLogger(__name__)

admin_required = user_type_required("1")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request):
    """
    Public endpoint for Meta (WhatsApp, Instagram, Page messaging).
    Meta sends GET for verification and POST for events.
    """
    cfg = effective_meta_config()

    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        if not challenge:
            return HttpResponseForbidden("Missing challenge")
        out = verify_webhook(mode or "", token or "", challenge, cfg)
        if out is None:
            return HttpResponseForbidden("Verification failed")
        return HttpResponse(out, content_type="text/plain")

    raw = request.body
    sig = request.META.get("HTTP_X_HUB_SIGNATURE_256")
    if not verify_signature(raw, sig, cfg.get("app_secret") or ""):
        logger.warning("Meta webhook rejected: bad signature")
        return HttpResponseForbidden("Invalid signature")

    try:
        handle_meta_webhook_json(raw, cfg)
    except Exception:
        logger.exception("Meta webhook handler error")
    return HttpResponse(status=200)


meta_webhook.allow_without_login = True


@admin_required
@admin_perm_required("settings")
def manage_meta_integration(request):
    inst = MetaIntegrationSettings.get_solo()
    if request.method == "POST":
        form = MetaIntegrationSettingsForm(request.POST, instance=inst)
        if form.is_valid():
            form.save()
            messages.success(request, "Integration settings saved.")
            return redirect(reverse("manage_meta_integration"))
    else:
        form = MetaIntegrationSettingsForm(instance=inst)

    webhook_path = reverse("meta_webhook")
    base = (inst.public_base_url or "").rstrip("/")
    webhook_full = f"{base}{webhook_path}" if base else ""

    return render(
        request,
        "admin_template/manage_meta_integration.html",
        {
            "page_title": "WhatsApp, Instagram & Facebook",
            "form": form,
            "webhook_full_url": webhook_full,
            "webhook_path": webhook_path,
        },
    )


@admin_required
@admin_perm_required("settings")
def social_chat_inbox(request):
    threads = SocialChatThread.objects.select_related("lead").order_by("-last_message_at")[
        :300
    ]
    active = None
    message_list = []
    tid = request.GET.get("thread")
    if tid:
        try:
            active = get_object_or_404(
                SocialChatThread.objects.select_related("lead"),
                pk=int(tid),
            )
            message_list = list(
                active.messages.select_related("thread").order_by("created_at")[:500]
            )
        except ValueError:
            pass

    return render(
        request,
        "admin_template/social_chat_inbox.html",
        {
            "page_title": "Social inbox",
            "threads": threads,
            "active_thread": active,
            "message_list": message_list,
        },
    )


@admin_required
@admin_perm_required("settings")
@require_POST
def social_chat_send(request, thread_id):
    thread = get_object_or_404(SocialChatThread, pk=thread_id)
    body = (request.POST.get("body") or "").strip()
    redirect_url = f"{reverse('social_chat_inbox')}?thread={thread.pk}"
    if not body:
        messages.warning(request, "Enter a message to send.")
        return redirect(redirect_url)

    ok, err = send_thread_reply(thread, body)
    now = timezone.now()
    if ok:
        SocialChatMessage.objects.create(
            thread=thread,
            direction=SocialChatMessage.DIRECTION_OUT,
            body=body,
        )
        thread.last_message_at = now
        thread.last_message_preview = body[:300]
        thread.save(update_fields=["last_message_at", "last_message_preview", "updated_at"])
        messages.success(request, "Message sent.")
    else:
        SocialChatMessage.objects.create(
            thread=thread,
            direction=SocialChatMessage.DIRECTION_OUT,
            body=body,
            error_detail=err[:500],
        )
        thread.last_message_at = now
        thread.last_message_preview = body[:300]
        thread.save(update_fields=["last_message_at", "last_message_preview", "updated_at"])
        messages.error(request, f"Could not send: {err[:300]}")

    return redirect(redirect_url)
