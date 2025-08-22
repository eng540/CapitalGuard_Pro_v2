def rec_summary(rec) -> str:
    return (
        "📌 <b>Recommendation</b>\n"
        f"Asset: <b>{rec.asset.value}</b> | Side: <b>{rec.side.value}</b>\n"
        f"Entry: <b>{rec.entry.value}</b> | SL: <b>{rec.stop_loss.value}</b>\n"
        f"Targets: <b>{', '.join(map(str, rec.targets.values))}</b>\n"
        f"ID: <code>{rec.id}</code>"
    )
