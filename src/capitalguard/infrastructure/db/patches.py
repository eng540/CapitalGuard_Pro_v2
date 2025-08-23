# -*- coding: utf-8 -*-
from __future__ import annotations
import logging
from sqlalchemy import create_engine, text, inspect
from capitalguard.config import settings

def run_patches() -> None:
    """
    يفحص recommendations.channel_id/user_id وإذا كانت INTEGER يحوّلها إلى BIGINT.
    يعمل مرة واحدة فقط. آمن للتشغيل المتكرر (idempotent).
    """
    try:
        engine = create_engine(settings.DATABASE_URL)
    except Exception as e:
        logging.warning("patches: couldn't create engine: %s", e)
        return

    with engine.connect() as conn:
        try:
            insp = inspect(conn)
            if "recommendations" not in insp.get_table_names():
                logging.info("patches: table recommendations not found, skip")
                return

            cols = {c["name"]: c for c in insp.get_columns("recommendations")}
            need_alter = []

            # بعضドرايفرات ترجع النوع كسلسلة نصية مختلفة؛ نفحص بالـ str
            for colname in ("channel_id", "user_id"):
                if colname in cols:
                    coltype = str(cols[colname].get("type", "")).lower()
                    if "int" in coltype and "big" not in coltype:
                        need_alter.append(colname)

            if not need_alter:
                logging.info("patches: no integer->bigint changes needed")
                return

            # نجري التحويل لكل عمود يحتاج
            for col in need_alter:
                logging.info("patches: altering column %s to BIGINT", col)
                conn.execute(
                    text(f"ALTER TABLE recommendations "
                         f"ALTER COLUMN {col} TYPE BIGINT USING {col}::bigint;")
                )
            conn.commit()
            logging.info("patches: done")
        except Exception as e:
            logging.exception("patches: failed: %s", e)