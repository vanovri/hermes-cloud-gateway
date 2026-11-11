"""Fetch official source text independently; render quotes instead of model-authored claims."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

WIB = timezone(timedelta(hours=7))
MONTHS = {name: index for index, names in enumerate([
    ["jan", "january", "januari"], ["feb", "february", "februari"], ["mar", "march", "maret"],
    ["apr", "april"], ["may", "mei"], ["jun", "june", "juni"], ["jul", "july", "juli"],
    ["aug", "august", "agustus", "agu"], ["sep", "september"], ["oct", "october", "okt", "oktober"],
    ["nov", "november"], ["dec", "december", "des", "desember"]], 1) for name in names}


def normalize(text: str) -> str:
    return " ".join(html.unescape(text).split()).casefold()


def checked_url(raw: str, hosts: dict) -> str:
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or host not in hosts):
        raise ValueError("URL is outside the reviewed official hosts")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "igshid"}]
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", urlencode(query), ""))


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_page(url: str, hosts: dict) -> str:
    opener = urllib.request.build_opener(NoRedirect)
    for _ in range(4):
        checked_url(url, hosts)
        host = urlsplit(url).hostname
        for address in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise ValueError("Source resolves to a non-public address")
        try:
            response = opener.open(urllib.request.Request(url, headers={"User-Agent": "HermesEvidenceCheck/1.0"}), timeout=8)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                from urllib.parse import urljoin
                url = checked_url(urljoin(url, exc.headers.get("Location", "")), hosts)
                continue
            raise ValueError("Official source is unavailable") from None
        with response:
            if "text/html" not in response.headers.get("Content-Type", ""):
                raise ValueError("Source must be an HTML page")
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("Source page exceeds the validation limit")
            encoding = response.headers.get_content_charset() or "utf-8"
        parser = PageText()
        parser.feed(raw.decode(encoding, errors="replace"))
        return " ".join(parser.parts)
    raise ValueError("Too many source redirects")


def dates_in_quote(quote: str) -> set[date]:
    dates = set()
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", quote):
        try:
            dates.add(date(*map(int, match.groups())))
        except ValueError:
            pass
    for match in re.finditer(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", quote):
        day, month, year = match.groups()
        try:
            dates.add(date(int(year), MONTHS[month.lower()], int(day)))
        except (ValueError, KeyError):
            pass
    for match in re.finditer(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", quote):
        month, day, year = match.groups()
        try:
            dates.add(date(int(year), MONTHS[month.lower()], int(day)))
        except (ValueError, KeyError):
            pass
    return dates


def database(home: Path):
    path = home / "state/startup_digest.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    connection.execute("CREATE TABLE IF NOT EXISTS prepared (digest TEXT PRIMARY KEY, payload TEXT NOT NULL, prepared_at TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS delivered (item_key TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, delivered_at TEXT NOT NULL)")
    connection.commit()
    return connection


def validate_items(items, hosts, *, fetcher=fetch_page, today=None, seen=frozenset()):
    today = today or datetime.now(WIB).date()
    accepted, rejected, pages, words_by_host, batch = [], [], {}, {}, set()
    if not isinstance(items, list) or len(items) > 15:
        raise ValueError("Candidate list must contain at most fifteen items")
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("Invalid candidate")
            url = checked_url(str(item.get("official_url", "")), hosts)
            title = str(item.get("program_title", "")).strip()
            if not 3 <= len(title) <= 160:
                raise ValueError("Missing source title")
            if url not in pages:
                pages[url] = normalize(fetcher(url, hosts))
            page = pages[url]
            if normalize(title) not in page:
                raise ValueError("Title is absent from the fetched page")
            deadline_quote = " ".join(str(item.get("verbatim_deadline_quote") or "").split())
            benefit_quote = " ".join(str(item.get("verbatim_benefit_quote") or "").split())
            for quote in [deadline_quote, benefit_quote]:
                if quote and (len(quote.split()) > 12 or len(quote) < 8 or normalize(quote) not in page):
                    raise ValueError("Quote does not match the independently fetched source")
            if not benefit_quote:
                raise ValueError("A sourced benefit or investor mandate quote is required")
            status = item.get("deadline_status")
            deadline = None
            if status == "EXACT_DATE":
                deadline = date.fromisoformat(str(item.get("deadline_iso")))
                if deadline < today or deadline not in dates_in_quote(deadline_quote):
                    raise ValueError("Deadline contradicts its quote or has expired")
            elif status == "ROLLING":
                if not re.search(r"\b(rolling|bergulir)\b", deadline_quote, re.I):
                    raise ValueError("Rolling status lacks explicit source evidence")
            elif status != "NOT_STATED" or item.get("deadline_iso"):
                raise ValueError("Unsupported deadline status")
            category = item.get("category")
            if category not in {"open_call", "investor_reference"}:
                raise ValueError("Unsupported category")
            if category == "open_call" and status == "NOT_STATED":
                raise ValueError("An open call requires a future date or explicit rolling evidence")
            if category == "open_call" and not re.search(r"\b(apply|applications?|proposals?|pendaftaran|daftar|open call)\b", deadline_quote + " " + benefit_quote, re.I):
                raise ValueError("No quoted application evidence")
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            words = len((deadline_quote + " " + benefit_quote).split())
            if words_by_host.get(host, 0) + words > 24:
                raise ValueError("Quote budget for this source is exhausted")
            cohort = str(deadline) if deadline else str(today.year)
            key = hashlib.sha256(f"{host}|{normalize(title)}|{cohort}".encode()).hexdigest()
            if key in seen or key in batch:
                continue
            batch.add(key)
            words_by_host[host] = words_by_host.get(host, 0) + words
            accepted.append({"key": key, "title": title, "url": url, "organizer": hosts[host],
                             "category": category, "deadline": str(deadline) if deadline else None,
                             "deadline_status": status, "deadline_quote": deadline_quote,
                             "benefit_quote": benefit_quote, "source_sha256": hashlib.sha256(page.encode()).hexdigest()})
        except (ValueError, OSError, TypeError) as exc:
            rejected.append(type(exc).__name__)
        if len(accepted) == 5:
            break
    return accepted, rejected


def render(items, today):
    lines = [f"Radar Peluang dan Investor ENVIID, {today.isoformat()}"]
    if not items:
        return "\n\n".join(lines + ["Belum ada kandidat baru yang lolos pemeriksaan kutipan dan sumber resmi pada run ini."])
    for number, item in enumerate(items, 1):
        deadline = item["deadline"] or ("Rolling, sesuai kutipan" if item["deadline_status"] == "ROLLING" else "Tanggal tidak tercantum pada bukti")
        lines += [f"{number}. {item['title']}\nPenyelenggara: {item['organizer']}\n"
                  + ("Profil investor. Bukan bukti open call.\n" if item["category"] == "investor_reference" else "Pendaftaran perlu ditinjau.\n")
                  + f"Deadline: {deadline}\nKutipan deadline: {item['deadline_quote'] or 'Tidak dicantumkan'}\n"
                  + f"Kutipan manfaat atau mandat: {item['benefit_quote']}\nSumber: {item['url']}"]
    return "\n\n".join(lines + ["Kutipan cocok dengan halaman yang diambil ulang. Eligibility dan status usaha tetap perlu ditinjau."])


def prepare(home: Path, raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    obj = json.loads(text)
    hosts = json.loads((home / "ops/startup_sources.json").read_text())["hosts"]
    now = datetime.now(WIB)
    with database(home) as db:
        cutoff = (now - timedelta(days=45)).isoformat()
        db.execute("DELETE FROM delivered WHERE delivered_at < ?", (cutoff,))
        db.execute("DELETE FROM prepared WHERE prepared_at < ?", (cutoff,))
        seen = {row[0] for row in db.execute("SELECT item_key FROM delivered")}
        items, rejected = validate_items(obj.get("opportunities"), hosts, today=now.date(), seen=seen)
        output = render(items, now.date())
        digest = hashlib.sha256(output.encode()).hexdigest()
        db.execute("INSERT OR REPLACE INTO prepared VALUES (?, ?, ?)", (digest, json.dumps(items), now.isoformat()))
    return output


def commit(home: Path, text: str) -> None:
    digest = hashlib.sha256(text.rstrip().encode()).hexdigest()
    now = datetime.now(WIB).isoformat()
    with database(home) as db:
        row = db.execute("SELECT payload FROM prepared WHERE digest=?", (digest,)).fetchone()
        if row is None:
            raise ValueError("Delivered text has no matching validation record")
        for item in json.loads(row[0]):
            db.execute("INSERT OR IGNORE INTO delivered VALUES (?, ?, ?, ?)", (item["key"], item["title"], item["url"], now))
        db.execute("DELETE FROM delivered WHERE rowid NOT IN (SELECT rowid FROM delivered ORDER BY delivered_at DESC LIMIT 200)")
        db.execute("DELETE FROM prepared WHERE rowid NOT IN (SELECT rowid FROM prepared ORDER BY prepared_at DESC LIMIT 200)")


def compact_context(home: Path) -> str:
    hosts = json.loads((home / "ops/startup_sources.json").read_text())["hosts"]
    with database(home) as db:
        rows = list(db.execute("SELECT title, url FROM delivered ORDER BY delivered_at DESC LIMIT 60"))
    return json.dumps({"reviewed_official_hosts": hosts, "already_delivered": rows}, ensure_ascii=False)
