from capitalguard.interfaces.telegram.conversation_handlers import _normalize_publication_queue_notice


def test_publication_queue_notice_never_claims_immediate_delivery():
    message = "✅ تم الحفظ!\n\nجاري النشر الآن في 2 قناة..."

    normalized = _normalize_publication_queue_notice(message)

    assert normalized is not None
    assert "Publication Outbox" in normalized
    assert "QUEUED" in normalized
    assert "لم يتأكد التسليم بعد" in normalized
    assert "جاري النشر الآن" not in normalized


def test_unrelated_telegram_message_is_unchanged():
    message = "✅ تم الحفظ بنجاح"

    assert _normalize_publication_queue_notice(message) == message
