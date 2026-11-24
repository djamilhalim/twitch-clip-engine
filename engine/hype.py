"""
Live-Hype-Erkennung: Chat anonym mitlesen (kein Login nötig) und Ausbrüche erkennen.
Bei einem Ausbruch wird ein Callback aufgerufen, der einen Twitch-Clip erstellt.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import ssl
import threading
import time
from collections import defaultdict, deque
from typing import Callable

log = logging.getLogger(__name__)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
PRIVMSG = re.compile(r"^(?:@\S+ )?:(\S+?)!\S+ PRIVMSG #(\S+) :(.*)$")


class HypeDetector:
    """Zählt Nachrichten pro Kanal und meldet Spitzen gegenüber dem gleitenden Durchschnitt."""

    def __init__(self, cfg: dict, on_hype: Callable[[str, dict], None]):
        self.basis = int(cfg.get("basis_fenster_s", 120))
        self.spitze = int(cfg.get("spitzen_fenster_s", 10))
        self.faktor = float(cfg.get("faktor", 3.5))
        self.min_rate = float(cfg.get("min_nachrichten_pro_s", 4))
        self.cooldown = int(cfg.get("cooldown_s", 300))
        self.trigger = [w.lower() for w in cfg.get("trigger_woerter", [])]
        self.on_hype = on_hype
        self.msgs: dict[str, deque[tuple[float, bool]]] = defaultdict(deque)
        self.last_fire: dict[str, float] = {}
        self.channels: set[str] = set()
        self._want: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── Logik (rein, testbar) ──
    def feed(self, channel: str, text: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        low = text.lower()
        hit = any(w in low for w in self.trigger)
        q = self.msgs[channel]
        q.append((now, hit))
        while q and q[0][0] < now - self.basis:
            q.popleft()
        return self.check(channel, now)

    def check(self, channel: str, now: float) -> dict | None:
        q = self.msgs[channel]
        if not q or now - self.last_fire.get(channel, 0) < self.cooldown:
            return None
        # Basis erst bewerten, wenn genug Historie da ist
        if now - q[0][0] < self.basis * 0.6:
            return None
        spitze = [h for t, h in q if t >= now - self.spitze]
        rate_spitze = len(spitze) / self.spitze
        rate_basis = len(q) / self.basis
        trigger_anteil = (sum(spitze) / len(spitze)) if spitze else 0
        schwelle = max(self.min_rate, rate_basis * self.faktor)
        # Trigger-Wörter senken die Schwelle etwas (KEKW-Wellen = lustiger Moment)
        if trigger_anteil > 0.3:
            schwelle *= 0.8
        if rate_spitze >= schwelle:
            self.last_fire[channel] = now
            info = {
                "rate_spitze": round(rate_spitze, 2),
                "rate_basis": round(rate_basis, 2),
                "trigger_anteil": round(trigger_anteil, 2),
            }
            return info
        return None

    # ── Netzwerk ──
    def set_channels(self, logins: set[str]) -> None:
        self._want = {l.lower() for l in logins}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name="hype")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    async def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 2
            except Exception as e:  # Verbindung neu aufbauen
                log.warning("Chat-Verbindung getrennt: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(IRC_HOST, IRC_PORT, ssl=ssl.create_default_context())
        nick = f"justinfan{random.randint(10000, 99999)}"  # anonymer Lesezugang
        writer.write(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\n".encode())
        await writer.drain()
        self.channels = set()
        last_sync = 0.0
        while not self._stop.is_set():
            if time.time() - last_sync > 15:
                await self._sync_channels(writer)
                last_sync = time.time()
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=30)
            except asyncio.TimeoutError:
                writer.write(b"PING :tmi.twitch.tv\r\n")
                await writer.drain()
                continue
            if not line:
                raise ConnectionError("EOF")
            text = line.decode("utf-8", "ignore").rstrip("\r\n")
            if text.startswith("PING"):
                writer.write(b"PONG :tmi.twitch.tv\r\n")
                await writer.drain()
                continue
            m = PRIVMSG.match(text)
            if m:
                channel, msg = m.group(2).lower(), m.group(3)
                info = self.feed(channel, msg)
                if info:
                    log.info("🔥 Hype bei %s: %s", channel, info)
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, self.on_hype, channel, info)

    async def _sync_channels(self, writer: asyncio.StreamWriter) -> None:
        join = self._want - self.channels
        part = self.channels - self._want
        for ch in part:
            writer.write(f"PART #{ch}\r\n".encode())
            self.msgs.pop(ch, None)
        for ch in join:
            writer.write(f"JOIN #{ch}\r\n".encode())
            await asyncio.sleep(0.6)  # Twitch-Join-Limit respektieren
        self.channels = set(self._want)
        await writer.drain()
