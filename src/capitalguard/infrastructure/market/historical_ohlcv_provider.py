from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from capitalguard.domain.coverage import CoverageReport, CoverageStatus
from capitalguard.domain.ports import OHLCVProviderPort

logger = logging.getLogger(__name__)

PROVIDER_PAGE_LIMIT = 1000


class HistoricalOHLCVProvider(OHLCVProviderPort):
    """
    مزود الشموع التاريخية المعتمد للنظام.
    يدعم التغطية، الترقيم (Pagination)، والمحاذاة الزمنية الدقيقة لعقد بينانس.
    """

    def __init__(
        self,
        client: Any,
        cache: Optional[Any] = None,
        max_pages: int = 10,
    ) -> None:
        self.client = client
        self.cache = cache
        self.max_pages = max_pages

    def _utc(self, dt: Optional[datetime]) -> datetime:
        if dt is None:
            return datetime.now(timezone.utc)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _floor_to_day(self, dt: datetime) -> datetime:
        """محاذاة التاريخ إلى منتصف الليل 00:00:00 UTC بدقة لتوافق عقد بينانس."""
        utc_dt = self._utc(dt)
        return datetime.combine(utc_dt.date(), time(0, 0, 0, 0), tzinfo=timezone.utc)

    def _ceil_to_day(self, dt: datetime) -> datetime:
        """محاذاة التاريخ إلى نهاية اليوم 23:59:59.999 UTC."""
        utc_dt = self._utc(dt)
        return datetime.combine(utc_dt.date(), time(23, 59, 59, 999999), tzinfo=timezone.utc)

    async def fetch_daily(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        asset: Optional[str] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        محول دلالي لمسح الماكرو اليومي (1D).
        يضمن أن يبدأ الطلب دائمًا من 00:00:00 UTC لتجنب استبعاد بينانس لشمعة اليوم.
        """
        aligned_start = self._floor_to_day(start)
        aligned_end = self._ceil_to_day(end)

        target_symbol = symbol or asset or ""
        logger.info(
            f"fetch_daily: Aligned window for {target_symbol}: "
            f"raw_start={start} -> aligned_start={aligned_start}, end={aligned_end}"
        )

        res = await self.fetch_with_coverage(
            symbol=target_symbol,
            interval="1d",
            start_time=aligned_start,
            end_time=aligned_end,
            limit=limit,
            asset=asset,
            market=market,
        )

        if hasattr(res, "candles"):
            return res.candles
        if isinstance(res, tuple) and len(res) >= 1:
            return res[0]
        if isinstance(res, list):
            return res
        return []

    async def fetch_minute_day(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        asset: Optional[str] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        جلب شموع الدقيقة (1m) لليوم الحرج أو اليوم صفر.
        يبدأ من الدقيقة المحددة سببيًا دون العبور إلى اليوم التالي.
        """
        target_symbol = symbol or asset or ""
        utc_start = self._utc(start)
        utc_end = self._utc(end)

        res = await self.fetch_with_coverage(
            symbol=target_symbol,
            interval="1m",
            start_time=utc_start,
            end_time=utc_end,
            limit=limit,
            asset=asset,
            market=market,
        )

        if hasattr(res, "candles"):
            return res.candles
        if isinstance(res, tuple) and len(res) >= 1:
            return res[0]
        if isinstance(res, list):
            return res
        return []

    async def fetch_with_coverage(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        asset: Optional[str] = None,
        market: Optional[str] = None,
    ) -> CoverageReport:
        """
        جلب الشموع مع حساب التغطية والترقيم التلقائي (Pagination).
        """
        target_symbol = (symbol or asset or "").upper()
        start_utc = self._utc(start_time)
        end_utc = self._utc(end_time)

        if start_utc >= end_utc:
            return CoverageReport(
                candles=[],
                status=CoverageStatus.INSUFFICIENT_COVERAGE,
                expected_count=0,
                actual_count=0,
                missing_ranges=[],
            )

        # فحص الكاش إذا كان متاحًا
        cache_key = f"ohlcv:{target_symbol}:{interval}:{int(start_utc.timestamp())}:{int(end_utc.timestamp())}"
        if self.cache:
            cached_data = await self.cache.get(cache_key)
            if cached_data:
                return CoverageReport(
                    candles=cached_data,
                    status=CoverageStatus.FULL_COVERAGE,
                    expected_count=len(cached_data),
                    actual_count=len(cached_data),
                    missing_ranges=[],
                )

        candles: List[Dict[str, Any]] = []
        cursor = start_utc
        interval_delta = self._get_interval_delta(interval)

        for _ in range(self.max_pages):
            if cursor >= end_utc:
                break

            page_candles = await self._fetch_page(
                symbol=target_symbol,
                interval=interval,
                start_time=cursor,
                end_time=end_utc,
                limit=min(limit, PROVIDER_PAGE_LIMIT),
                market=market,
            )

            if not page_candles:
                break

            candles.extend(page_candles)
            last_candle_time = self._parse_candle_time(page_candles[-1])

            if last_candle_time <= cursor:
                # منع الحلقات التكرارية اللانهائية
                cursor = cursor + (interval_delta * len(page_candles))
            else:
                cursor = last_candle_time + interval_delta

            if len(page_candles) < min(limit, PROVIDER_PAGE_LIMIT):
                break

        # إزالة التكرار إن وجد وترتيب الشموع زمنيًا
        unique_candles = self._deduplicate_and_sort(candles)

        # حساب حالة التغطية
        expected_candles = self._calculate_expected_count(start_utc, end_utc, interval)
        actual_count = len(unique_candles)

        status = CoverageStatus.FULL_COVERAGE
        if actual_count == 0:
            status = CoverageStatus.INSUFFICIENT_COVERAGE
        elif actual_count < (expected_candles * 0.85):
            status = CoverageStatus.PARTIAL_COVERAGE

        if self.cache and unique_candles and status == CoverageStatus.FULL_COVERAGE:
            await self.cache.set(cache_key, unique_candles, ttl=3600)

        return CoverageReport(
            candles=unique_candles,
            status=status,
            expected_count=expected_candles,
            actual_count=actual_count,
            missing_ranges=[],
        )

    async def _fetch_page(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)

            if hasattr(self.client, "get_historical_ohlcv"):
                return await self.client.get_historical_ohlcv(
                    symbol=symbol,
                    interval=interval,
                    start_time=start_ms,
                    end_time=end_ms,
                    limit=limit,
                    market=market,
                )
            if hasattr(self.client, "get_klines"):
                return await self.client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    startTime=start_ms,
                    endTime=end_ms,
                    limit=limit,
                )
        except Exception as e:
            logger.error(f"HistoricalOHLCVProvider: Failed to fetch page for {symbol}: {e}")
            return []
        return []

    def _get_interval_delta(self, interval: str) -> timedelta:
        if interval.endswith("m"):
            return timedelta(minutes=int(interval[:-1]))
        if interval.endswith("h"):
            return timedelta(hours=int(interval[:-1]))
        if interval.endswith("d"):
            return timedelta(days=int(interval[:-1]))
        return timedelta(minutes=1)

    def _calculate_expected_count(self, start: datetime, end: datetime, interval: str) -> int:
        total_seconds = max(0, int((end - start).total_seconds()))
        delta_seconds = int(self._get_interval_delta(interval).total_seconds())
        if delta_seconds <= 0:
            return 0
        return max(1, total_seconds // delta_seconds)

    def _parse_candle_time(self, candle: Dict[str, Any]) -> datetime:
        t_val = candle.get("open_time") or candle.get("timestamp") or candle.get("time")
        if isinstance(t_val, datetime):
            return self._utc(t_val)
        if isinstance(t_val, (int, float)):
            if t_val > 1e11:
                t_val = t_val / 1000.0
            return datetime.fromtimestamp(t_val, tz=timezone.utc)
        if isinstance(t_val, str):
            try:
                dt = datetime.fromisoformat(t_val.replace("Z", "+00:00"))
                return self._utc(dt)
            except Exception:
                pass
        return datetime.now(timezone.utc)

    def _deduplicate_and_sort(self, candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        deduped = []
        for c in candles:
            t = self._parse_candle_time(c)
            if t not in seen:
                seen.add(t)
                deduped.append(c)
        deduped.sort(key=lambda x: self._parse_candle_time(x))
        return deduped

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        report = await self.fetch_with_coverage(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        return report.candles if hasattr(report, "candles") else []