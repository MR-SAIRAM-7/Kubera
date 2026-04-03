"""NSE scraping utilities.

This module provides resilient wrappers for NSE data endpoints with:
- Browser-like headers and cookie bootstrap.
- curl_cffi impersonation support (best effort against 403).
- Retries with exponential backoff.
- Generic endpoint access for any requested NSE API path.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

try:
    from curl_cffi import requests as curl_requests
except Exception:  # noqa: BLE001
    curl_requests = None


logger = logging.getLogger(__name__)


class NSEScraperError(RuntimeError):
    """Raised when NSE data cannot be fetched or parsed reliably."""


class NSEScraper:
    """Small client for NSE public APIs."""

    BASE_URL = "https://www.nseindia.com"
    ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com/corporate"

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://www.nseindia.com/",
    }

    def __init__(
        self,
        timeout: int = 12,
        max_retries: int = 3,
        backoff_seconds: float = 1.2,
        prefer_curl: bool = True,
        impersonate: str = "chrome110",
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

        self.prefer_curl = bool(prefer_curl and curl_requests is not None)
        self.curl_session = None
        if self.prefer_curl:
            self.curl_session = curl_requests.Session(impersonate=impersonate)
            self.curl_session.headers.update(self.DEFAULT_HEADERS)

        self._bootstrapped_requests = False
        self._bootstrapped_curl = False

    def _bootstrap_session(self, use_curl: bool) -> None:
        if use_curl:
            if self._bootstrapped_curl or self.curl_session is None:
                return
            self.curl_session.get(self.BASE_URL, timeout=self.timeout)
            self._bootstrapped_curl = True
            return

        if self._bootstrapped_requests:
            return
        self.session.get(self.BASE_URL, timeout=self.timeout)
        self._bootstrapped_requests = True

    def _request_json_once(self, path: str, params: Optional[Dict[str, Any]], use_curl: bool) -> Any:
        url = f"{self.BASE_URL}{path}"
        self._bootstrap_session(use_curl=use_curl)

        if use_curl and self.curl_session is not None:
            response = self.curl_session.get(url, params=params, timeout=self.timeout)
        else:
            response = self.session.get(url, params=params, timeout=self.timeout)

        response.raise_for_status()
        return response.json()

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            request_order = [True, False] if self.prefer_curl else [False, True]

            for use_curl in request_order:
                if use_curl and self.curl_session is None:
                    continue

                try:
                    return self._request_json_once(path=path, params=params, use_curl=use_curl)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    engine = "curl_cffi" if use_curl else "requests"
                    logger.warning(
                        "NSE request failed using %s (%s/%s) for %s: %s",
                        engine,
                        attempt,
                        self.max_retries,
                        path,
                        exc,
                    )

            if attempt < self.max_retries:
                time.sleep(self.backoff_seconds * attempt)
                self._bootstrapped_requests = False
                self._bootstrapped_curl = False

        raise NSEScraperError(f"NSE request failed for {path}: {last_error}")

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value in (None, "", "-"):
            return None
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    def get_any_data(self, api_path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Fetch any NSE API path and return raw JSON."""
        if not api_path:
            raise ValueError("api_path is required")

        clean_path = api_path.strip()
        if not clean_path.startswith("/api/"):
            if clean_path.startswith("api/"):
                clean_path = f"/{clean_path}"
            else:
                clean_path = f"/api/{clean_path.lstrip('/')}"

        return self._get_json(clean_path, params=params)

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        if not symbol:
            raise ValueError("symbol is required")
        return self.get_any_data("/api/quote-equity", params={"symbol": symbol.upper().strip()})

    def get_market_status(self) -> List[Dict[str, Any]]:
        data = self.get_any_data("/api/marketStatus")
        if isinstance(data, dict):
            return data.get("marketState", [])
        return []

    def get_indices_overview(self) -> List[Dict[str, Any]]:
        data = self.get_any_data("/api/allIndices")
        if isinstance(data, dict):
            return data.get("data", [])
        return []

    def get_corporate_announcements(self, symbol: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"index": "equities"}
        if symbol:
            params["symbol"] = symbol.upper().strip()

        data = self.get_any_data("/api/corporate-announcements", params=params)

        if isinstance(data, dict):
            rows = data.get("data", [])
        elif isinstance(data, list):
            rows = data
        else:
            rows = []

        announcements: List[Dict[str, Any]] = []
        for item in rows[: max(limit, 0)]:
            attachment = item.get("attchmntFile")
            announcements.append(
                {
                    "symbol": item.get("symbol"),
                    "company": item.get("sm_name"),
                    "subject": item.get("subject"),
                    "announcement_time": item.get("an_dt"),
                    "attachment": attachment,
                    "pdf_link": urljoin(f"{self.ARCHIVE_BASE_URL}/", attachment) if attachment else None,
                }
            )

        return announcements

    def get_stock_and_market_overview(self, symbol: str, filings_limit: int = 10) -> Dict[str, Any]:
        quote = self.get_quote(symbol)
        price_info = quote.get("priceInfo", {}) if isinstance(quote, dict) else {}
        security_info = quote.get("securityInfo", {}) if isinstance(quote, dict) else {}

        market_status = self.get_market_status()
        indices = self.get_indices_overview()
        filings = self.get_corporate_announcements(symbol=symbol, limit=filings_limit)

        normalized_quote = {
            "symbol": security_info.get("symbol") or symbol.upper().strip(),
            "company_name": security_info.get("companyName"),
            "industry": security_info.get("industry"),
            "last_price": self._to_float(price_info.get("lastPrice")),
            "change": self._to_float(price_info.get("change")),
            "percent_change": self._to_float(price_info.get("pChange")),
            "open": self._to_float(price_info.get("open")),
            "high": self._to_float(price_info.get("intraDayHighLow", {}).get("max")),
            "low": self._to_float(price_info.get("intraDayHighLow", {}).get("min")),
            "previous_close": self._to_float(price_info.get("previousClose")),
            "year_high": self._to_float(price_info.get("weekHighLow", {}).get("max")),
            "year_low": self._to_float(price_info.get("weekHighLow", {}).get("min")),
        }

        return {
            "source": "NSE",
            "requested_symbol": symbol.upper().strip(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "stock": normalized_quote,
            "market_status": market_status,
            "indices": indices,
            "corporate_announcements": filings,
            "raw_quote": quote,
        }


def get_latest_nse_filings_with_links(limit: int = 5, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Convenience function for latest NSE filings with PDF links."""
    scraper = NSEScraper()
    return scraper.get_corporate_announcements(symbol=symbol, limit=limit)


def get_nse_data(api_path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Convenience function for arbitrary NSE API path access."""
    scraper = NSEScraper()
    return scraper.get_any_data(api_path=api_path, params=params)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    client = NSEScraper()
    try:
        demo = client.get_stock_and_market_overview("RELIANCE", filings_limit=5)
        print(demo)
    except Exception as err:  # noqa: BLE001
        print(f"NSE scraper failed: {err}")
