"""BSE scraping utilities.

This module uses BSE public endpoints and guarded fallbacks for environments
where anti-bot protection intermittently blocks requests.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)


class BSEScraperError(RuntimeError):
	"""Raised when BSE data cannot be fetched or parsed reliably."""


class BSEScraper:
	BASE_SITE = "https://www.bseindia.com"
	BASE_API = "https://api.bseindia.com/BseIndiaAPI/api"

	DEFAULT_HEADERS = {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
		),
		"Accept": "application/json, text/plain, */*",
		"Accept-Language": "en-US,en;q=0.9",
		"Connection": "keep-alive",
		"Referer": "https://www.bseindia.com/",
	}

	def __init__(self, timeout: int = 12, max_retries: int = 3, backoff_seconds: float = 1.2):
		self.timeout = timeout
		self.max_retries = max_retries
		self.backoff_seconds = backoff_seconds
		self.session = requests.Session()
		self.session.headers.update(self.DEFAULT_HEADERS)
		self._bootstrapped = False

	def _bootstrap_session(self) -> None:
		if self._bootstrapped:
			return
		self.session.get(self.BASE_SITE, timeout=self.timeout)
		self._bootstrapped = True

	def _looks_like_html(self, content_type: str, body: str) -> bool:
		return "text/html" in content_type.lower() or body.lstrip().startswith("<!DOCTYPE html")

	def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
		url = f"{self.BASE_API}/{path}"
		last_error: Optional[Exception] = None

		for attempt in range(1, self.max_retries + 1):
			try:
				self._bootstrap_session()
				response = self.session.get(url, params=params, timeout=self.timeout)
				response.raise_for_status()
				return response
			except Exception as exc:  # noqa: BLE001
				last_error = exc
				logger.warning("BSE request failed (%s/%s): %s", attempt, self.max_retries, exc)
				if attempt < self.max_retries:
					time.sleep(self.backoff_seconds * attempt)
					self._bootstrapped = False

		raise BSEScraperError(f"BSE request failed for {path}: {last_error}")

	def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
		response = self._request(path, params=params)
		body = response.text or ""

		if self._looks_like_html(response.headers.get("Content-Type", ""), body):
			raise BSEScraperError(
				"BSE anti-bot page received instead of JSON. "
				"Retry later or run behind a trusted network."
			)

		try:
			return response.json()
		except json.JSONDecodeError as exc:
			match = re.search(r"(\[.*\]|\{.*\})", body, flags=re.DOTALL)
			if match:
				return json.loads(match.group(1))
			raise BSEScraperError(f"BSE JSON parse failed: {exc}") from exc

	@staticmethod
	def _to_float(value: Any) -> Optional[float]:
		if value in (None, "", "-"):
			return None
		try:
			return float(str(value).replace(",", ""))
		except (TypeError, ValueError):
			return None

	def search_scrip(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
		if not query:
			raise ValueError("query is required")

		data = self._request_json("Autocomplete/w", params={"query": query.strip()})
		if not isinstance(data, list):
			return []

		results: List[Dict[str, Any]] = []
		for item in data[: max(limit, 0)]:
			results.append(
				{
					"symbol": item.get("SYMBOL") or item.get("symbol") or item.get("label"),
					"company_name": item.get("CompanyName") or item.get("name") or item.get("value"),
					"scrip_code": str(
						item.get("SCRIP_CD")
						or item.get("scrip_cd")
						or item.get("scripcode")
						or item.get("SCRIPCODE")
						or ""
					).strip(),
				}
			)
		return results

	def _resolve_scrip_code(self, stock_query: str) -> str:
		token = stock_query.strip()
		if token.isdigit() and len(token) >= 5:
			return token

		results = self.search_scrip(token, limit=5)
		for result in results:
			code = (result.get("scrip_code") or "").strip()
			if code:
				return code

		raise BSEScraperError(f"Unable to resolve BSE scrip code for query '{stock_query}'")

	def get_quote(self, stock_query: str) -> Dict[str, Any]:
		scrip_code = self._resolve_scrip_code(stock_query)

		# BSE has changed quote endpoints over time; try known variants.
		quote_endpoints = [
			("GetQuoteData/w", {"Type": "EQ", "scripcode": scrip_code}),
			("GetQuoteHeader/w", {"scripcode": scrip_code}),
			("StockReachGraph/w", {"scripcode": scrip_code, "flag": "0"}),
		]

		last_error: Optional[Exception] = None
		for path, params in quote_endpoints:
			try:
				payload = self._request_json(path, params=params)
				if isinstance(payload, dict) and payload:
					return {"scrip_code": scrip_code, "data": payload, "endpoint": path}
				if isinstance(payload, list) and payload:
					return {"scrip_code": scrip_code, "data": payload, "endpoint": path}
			except Exception as exc:  # noqa: BLE001
				last_error = exc

		raise BSEScraperError(f"No quote data found for scrip code {scrip_code}: {last_error}")

	def get_market_overview(self) -> Dict[str, Any]:
		# API path observed in BSE ecosystem; if blocked, fallback to minimal homepage parse.
		try:
			market_watch = self._request_json("MktWatch/w")
			return {"source": "BSE_API", "data": market_watch}
		except Exception as api_error:  # noqa: BLE001
			logger.info("BSE market watch API unavailable, using homepage fallback: %s", api_error)

		response = self.session.get(self.BASE_SITE, timeout=self.timeout)
		html = response.text
		sensex_match = re.search(r"SENSEX[^\d]{0,40}([\d,]+(?:\.\d+)?)", html, flags=re.IGNORECASE)
		return {
			"source": "BSE_HOMEPAGE_FALLBACK",
			"sensex": self._to_float(sensex_match.group(1)) if sensex_match else None,
			"note": "Limited fallback data. API likely blocked by anti-bot protection.",
		}

	def get_stock_and_market_overview(self, stock_query: str) -> Dict[str, Any]:
		quote_payload = self.get_quote(stock_query)
		market_payload = self.get_market_overview()

		return {
			"source": "BSE",
			"requested_stock": stock_query,
			"fetched_at": datetime.now(timezone.utc).isoformat(),
			"quote": quote_payload,
			"market_overview": market_payload,
		}


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
	scraper = BSEScraper()
	try:
		print(scraper.get_stock_and_market_overview("RELIANCE"))
	except Exception as err:  # noqa: BLE001
		print(f"BSE scraper failed: {err}")
