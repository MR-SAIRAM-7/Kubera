"""News and social signal scraping utilities.

This module fetches recent business and market news from multiple public feeds,
then filters and ranks items relevant to a given stock query.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests


logger = logging.getLogger(__name__)


class NewsScraperError(RuntimeError):
	"""Raised when all configured sources fail."""


class NewsScraper:
	DEFAULT_HEADERS = {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
		),
		"Accept": "application/rss+xml, application/xml, text/xml, */*",
		"Accept-Language": "en-US,en;q=0.9",
	}

	# Note: X has no stable public API without auth. Nitter RSS is used as best-effort.
	DEFAULT_SOURCES: List[Tuple[str, str]] = [
		("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
		("MoneycontrolMarkets", "https://www.moneycontrol.com/rss/MCtopnews.xml"),
		("LiveMintMarkets", "https://www.livemint.com/rss/markets"),
		("EconomicTimesMarkets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
		("BusinessStandardMarkets", "https://www.business-standard.com/rss/markets-106.rss"),
	]

	def __init__(self, timeout: int = 12):
		self.timeout = timeout
		self.session = requests.Session()
		self.session.headers.update(self.DEFAULT_HEADERS)

	def _build_sources(self, query: str) -> List[Tuple[str, str]]:
		encoded_query = requests.utils.quote(query.strip())
		dynamic_sources = [
			("X-Nitter", f"https://nitter.net/search/rss?f=tweets&q={encoded_query}"),
		]
		return self.DEFAULT_SOURCES + dynamic_sources

	@staticmethod
	def _clean_text(value: Optional[str]) -> str:
		if not value:
			return ""
		cleaned = re.sub(r"<[^>]+>", " ", value)
		cleaned = re.sub(r"\s+", " ", cleaned)
		return cleaned.strip()

	@staticmethod
	def _parse_datetime(value: Optional[str]) -> Optional[str]:
		if not value:
			return None

		try:
			dt = parsedate_to_datetime(value)
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
			return dt.astimezone(timezone.utc).isoformat()
		except (TypeError, ValueError):
			pass

		# ISO-like fallback
		try:
			dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
			return dt.astimezone(timezone.utc).isoformat()
		except (TypeError, ValueError):
			return None

	def _parse_rss(self, xml_text: str, source_name: str) -> List[Dict[str, Any]]:
		root = ET.fromstring(xml_text)
		items: List[Dict[str, Any]] = []

		# Handle RSS 2.0 item and Atom entry formats.
		rss_items = root.findall(".//item")
		atom_entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")

		for item in rss_items:
			title = self._clean_text(item.findtext("title"))
			link = self._clean_text(item.findtext("link"))
			description = self._clean_text(item.findtext("description"))
			published = self._parse_datetime(item.findtext("pubDate"))
			if title and link:
				items.append(
					{
						"source": source_name,
						"title": title,
						"link": link,
						"summary": description,
						"published_at": published,
					}
				)

		for entry in atom_entries:
			title = self._clean_text(entry.findtext("{http://www.w3.org/2005/Atom}title"))
			link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
			link = self._clean_text(link_elem.get("href") if link_elem is not None else "")
			summary = self._clean_text(
				entry.findtext("{http://www.w3.org/2005/Atom}summary")
				or entry.findtext("{http://www.w3.org/2005/Atom}content")
			)
			published = self._parse_datetime(
				entry.findtext("{http://www.w3.org/2005/Atom}published")
				or entry.findtext("{http://www.w3.org/2005/Atom}updated")
			)
			if title and link:
				items.append(
					{
						"source": source_name,
						"title": title,
						"link": link,
						"summary": summary,
						"published_at": published,
					}
				)

		return items

	@staticmethod
	def _score_item(item: Dict[str, Any], keywords: Iterable[str]) -> int:
		text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
		return sum(1 for token in keywords if token and token in text)

	def _fetch_source(self, source_name: str, source_url: str) -> List[Dict[str, Any]]:
		response = self.session.get(source_url, timeout=self.timeout)
		response.raise_for_status()
		return self._parse_rss(response.text, source_name=source_name)

	def get_latest_related_news(self, stock_query: str, limit: int = 20) -> Dict[str, Any]:
		if not stock_query or not stock_query.strip():
			raise ValueError("stock_query is required")

		normalized_query = stock_query.strip()
		keywords = [token.lower() for token in re.split(r"\W+", normalized_query) if token]
		keywords.append(normalized_query.lower())

		all_items: List[Dict[str, Any]] = []
		errors: List[Dict[str, str]] = []

		for source_name, source_url in self._build_sources(normalized_query):
			try:
				all_items.extend(self._fetch_source(source_name, source_url))
			except Exception as exc:  # noqa: BLE001
				logger.warning("Source failed: %s (%s)", source_name, exc)
				errors.append({"source": source_name, "error": str(exc)})

		if not all_items and errors:
			raise NewsScraperError("All news sources failed")

		# Deduplicate by canonical key.
		deduped: Dict[str, Dict[str, Any]] = {}
		for item in all_items:
			key = item.get("link") or item.get("title")
			if key and key not in deduped:
				deduped[key] = item

		ranked = []
		for item in deduped.values():
			score = self._score_item(item, keywords)
			if score > 0:
				ranked.append((score, item))

		# If keyword filtering is too strict, keep latest general market items.
		if ranked:
			ranked.sort(key=lambda row: (row[0], row[1].get("published_at") or ""), reverse=True)
			filtered_items = [row[1] for row in ranked[: max(limit, 0)]]
		else:
			general = list(deduped.values())
			general.sort(key=lambda row: row.get("published_at") or "", reverse=True)
			filtered_items = general[: max(limit, 0)]

		return {
			"query": normalized_query,
			"fetched_at": datetime.now(timezone.utc).isoformat(),
			"total_items": len(filtered_items),
			"items": filtered_items,
			"source_errors": errors,
		}


def get_latest_related_news(stock_query: str, limit: int = 20) -> Dict[str, Any]:
	scraper = NewsScraper()
	return scraper.get_latest_related_news(stock_query=stock_query, limit=limit)


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
	client = NewsScraper()
	try:
		print(client.get_latest_related_news("RELIANCE", limit=15))
	except Exception as err:  # noqa: BLE001
		print(f"News scraper failed: {err}")
