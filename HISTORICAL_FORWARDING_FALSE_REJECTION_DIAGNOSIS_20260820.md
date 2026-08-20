# Historical Forwarding — False Channel Rejection Diagnosis (2026-08-20)

## New real-test evidence

The owner started the batch with:

```text
/historical_forward_start -1002433437205
```

The bot resolved it to `CH-000002` and displayed:

```text
Expected source_chat_id: -1002433437205
```

After forwarding source message `4448`, the receipt displayed:

```text
source_chat=-1002433437205
expected_source_chat=-1002433437205
REJECTED_CHANNEL
```

The finish command then reported `total=0`, `accepted=0`, `rejected=0`, because the handler removed the active batch key after staging the message and the preview queried a different empty batch state. This is separate from the false channel rejection and requires a preview lifecycle regression test.

## Root cause hypothesis

The comparison in `HistoricalForwardingService.stage_message` compares raw runtime values. Telegram and JSON metadata can represent the same channel ID using different runtime types (`int` versus numeric `str`). Their string rendering is identical in the Telegram reply, but Python's strict comparison rejects them. The fix must normalize both values to a signed integer before comparison and before persistence.

The `/historical_forward_finish` zero-record result indicates that the live batch state and receipt persistence are not aligned across the real update sequence. The service-level test and handler path must verify that the same `batch_id` remains attached to the receipt and that finish previews that exact batch before clearing the user state.
