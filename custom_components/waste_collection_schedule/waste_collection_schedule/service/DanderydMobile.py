"""Retrieval and parsing for Danderyd's mobile recycling-centre schedule."""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from waste_collection_schedule import parsers, response_shape, retrievers
from waste_collection_schedule.service.ICS import IcsEvent

if TYPE_CHECKING:
    from waste_collection_schedule.base_source import BaseSource
    from waste_collection_schedule.retrievers import Response

_PAGE_URL = (
    "https://www.danderyd.se/bygga-bo-och-miljo/avfall-atervinning-och-aterbruk/"
    "mobil-atervinningscentral/"
)
_PDF_LINK = re.compile(r"schema-hela-(20\d{2})-till-webb\.pdf$", re.IGNORECASE)
_MONTHS = {
    name.casefold(): number
    for number, name in enumerate(
        (
            "januari",
            "februari",
            "mars",
            "april",
            "maj",
            "juni",
            "juli",  # codespell:ignore juli
            "augusti",
            "september",
            "oktober",
            "november",
            "december",
        ),
        1,
    )
}
_DATE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-zÅÄÖåäö]+)\b")
_WEEKDAY = re.compile(
    r"^(?:måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag)\s+",
    re.IGNORECASE,
)
_MONTHLY_EVENT = re.compile(
    r"^[A-Za-zÅÄÖåäö]+\s+den\s+(?P<day>\d{1,2})\s+"
    r"(?P<month>[A-Za-zÅÄÖåäö]+),\s*(?P<location>.*?),\s*klockan\s*"
    r"(?P<start>\d{1,2}\.\d{2})\s*[–-]\s*(?P<end>\d{1,2}\.\d{2})\.?$",
    re.IGNORECASE,
)


class RetrievedSchedule(NamedTuple):
    monthly: Response
    annual: Response | None
    year: int


class DanderydMobileRetriever:
    """Fetch the current monthly page and, when linked, its annual PDF."""

    def __init__(self) -> None:
        self._page = retrievers.Request(_PAGE_URL, timeout=60)
        self._pdf = retrievers.Request(lambda url, **_: url, timeout=60)

    def __call__(self, source: BaseSource) -> RetrievedSchedule:
        year = datetime.date.today().year
        monthly = self._page(source)
        soup = BeautifulSoup(monthly.text, "html.parser")
        pdf_url = next(
            (
                urljoin(monthly.url, href)
                for anchor in soup.find_all("a", href=True)
                if (match := _PDF_LINK.search(href := str(anchor["href"])))
                and int(match.group(1)) == year
            ),
            None,
        )
        annual = self._pdf(source, pdf_url) if pdf_url else None
        return RetrievedSchedule(monthly, annual, year)


class DanderydMobileParser:
    """Parse both published schedules and prefer the monthly page on overlap."""

    def __call__(
        self, responses: RetrievedSchedule, source: BaseSource
    ) -> list[IcsEvent]:
        monthly_events, month_key = self._monthly_events(responses.monthly, source)
        notices: list[str] = []
        annual_events: list[IcsEvent] = []

        if responses.annual is None:
            notices.append(
                f"Kommunens sida saknar en länk till {responses.year} års "
                "helårsschema. Endast aktuell månadslista visas tills PDF-schemat "
                "publiceras."
            )
        else:
            annual_events = self._annual_events(responses.annual, source)
            notices.extend(self._compare(monthly_events, annual_events, month_key))

        source.notifications[:] = notices
        events = [
            event
            for event in annual_events
            if (event.date.year, event.date.month) != month_key
        ]
        events.extend(monthly_events)
        return sorted(events, key=lambda event: event.date)

    @staticmethod
    def _monthly_events(
        response: Response, source: BaseSource
    ) -> tuple[list[IcsEvent], tuple[int, int]]:
        soup = BeautifulSoup(response.text, "html.parser")
        heading = next(
            (
                element
                for element in soup.find_all(re.compile(r"^h[1-6]$"))
                if element.get_text(" ", strip=True).casefold().startswith("körningar ")
            ),
            None,
        )
        heading_text = heading.get_text(" ", strip=True) if heading else ""
        heading_match = re.fullmatch(
            r"Körningar\s+([A-Za-zÅÄÖåäö]+)\s+(20\d{2})",
            heading_text,
            re.IGNORECASE,
        )
        response_shape.expect(
            heading_match is not None,
            source_name=response_shape.source_name(source),
            detail="monthly schedule heading or month/year was not found",
            raw=heading_text or response.text[:500],
        )
        assert heading is not None
        assert heading_match is not None
        month = _MONTHS.get(heading_match.group(1).casefold())
        response_shape.expect(
            month is not None,
            source_name=response_shape.source_name(source),
            detail=f"unknown Swedish month {heading_match.group(1)!r}",
            raw=heading_text,
        )
        assert month is not None
        year = int(heading_match.group(2))

        items = []
        for element in heading.find_all_next(
            ["h1", "h2", "h3", "h4", "h5", "h6", "ul"]
        ):
            if element.name.startswith("h"):
                break
            items.extend(element.find_all("li", recursive=False))

        events: list[IcsEvent] = []
        for item in items:
            text = item.get_text(" ", strip=True)
            match = _MONTHLY_EVENT.fullmatch(text)
            response_shape.expect(
                match is not None,
                source_name=response_shape.source_name(source),
                detail="unrecognized entry in the monthly schedule",
                raw=text,
            )
            assert match is not None and month is not None
            event_date = datetime.date(year, month, int(match.group("day")))
            events.append(
                IcsEvent(
                    event_date,
                    "Mobil återvinningscentral",
                    match.group("location").strip(),
                    f"Öppet {match.group('start')}–{match.group('end')}",
                )
            )

        response_shape.expect(
            bool(events),
            source_name=response_shape.source_name(source),
            detail="monthly schedule contained no events",
            raw=heading_text,
        )
        return events, (year, month)

    @staticmethod
    def _annual_events(response: Response, source: BaseSource) -> list[IcsEvent]:
        year_match = _PDF_LINK.search(response.url)
        response_shape.expect(
            year_match is not None,
            source_name=response_shape.source_name(source),
            detail="annual PDF URL does not contain its schedule year",
            raw=response.url,
        )
        assert year_match is not None
        year = int(year_match.group(1))

        rows = parsers.PdfTableParser(min_words=100)(response, source)
        table_rows = [row for row in rows if row.page == 1]
        response_shape.expect(
            bool(table_rows),
            source_name=response_shape.source_name(source),
            detail="annual PDF did not contain a second-page schedule table",
            raw=" ".join(word.text for row in rows for word in row.words)[:500],
        )
        left_edge = min(word.x0 for row in table_rows for word in row.words)
        right_edge = max(word.x1 for row in table_rows for word in row.words)
        midpoint = (left_edge + right_edge) / 2
        events: list[IcsEvent] = []

        for row in table_rows:
            for side in (
                [word for word in row.words if word.x0 < midpoint],
                [word for word in row.words if word.x0 >= midpoint],
            ):
                text = " ".join(word.text.strip() for word in side).strip()
                match = _DATE.match(text)
                if match is None:
                    continue
                month = _MONTHS.get(match.group(2).casefold())
                if month is None:
                    continue
                remainder = text[match.end() :].strip()
                remainder = _WEEKDAY.sub("", remainder)
                if not remainder:
                    continue
                events.append(
                    IcsEvent(
                        datetime.date(year, month, int(match.group(1))),
                        "Mobil återvinningscentral",
                        remainder,
                        None,
                    )
                )

        response_shape.expect(
            len(events) >= 50,
            source_name=response_shape.source_name(source),
            detail=(
                f"annual PDF yielded only {len(events)} schedule entries; "
                "expected at least 50"
            ),
            raw=" ".join(word.text for row in table_rows for word in row.words)[:500],
        )
        return events

    @staticmethod
    def _compare(
        monthly: Iterable[IcsEvent],
        annual: Iterable[IcsEvent],
        month_key: tuple[int, int],
    ) -> list[str]:
        def normalize(value: str | None) -> str:
            return re.sub(r"\s+", " ", value or "").strip().rstrip(".").casefold()

        monthly_by_date = {event.date: event.location for event in monthly}
        annual_by_date = {
            event.date: event.location
            for event in annual
            if (event.date.year, event.date.month) == month_key
        }
        differences = []
        for day in sorted(monthly_by_date.keys() | annual_by_date.keys()):
            monthly_location = monthly_by_date.get(day)
            annual_location = annual_by_date.get(day)
            if normalize(monthly_location) == normalize(annual_location):
                continue
            monthly_value = monthly_location or "ingen körning"
            annual_value = annual_location or "ingen körning"
            differences.append(
                f"{day.isoformat()}: månadslistan anger {monthly_value}; "
                f"PDF-schemat anger {annual_value}"
            )
        if not differences:
            return []
        return [
            "Avvikelse mellan kommunens aktuella månadslista och PDF-schemat:\n"
            + "\n".join(differences)
            + "\nMånadslistan används för den aktuella månaden."
        ]
