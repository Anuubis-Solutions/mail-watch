#!/usr/bin/env python3
"""Watcher de bandeja IMAP. Comprueba si hay correo NUEVO de un humano desde un
baseline temporal (WATCH_SINCE, epoch UTC fijado al encender el watcher). No usa
Claude: solo IMAP. Escribe watch.json (resultado) y notice.txt (aviso formateado
para Google Chat) cuando hay novedades. Persiste los Message-ID ya avisados en
state/seen.json para no repetir. STDLIB only.

Config por entorno:
  IMAP_HOST, IMAP_PORT (def 993), IMAP_USER, IMAP_PASS  -> conexión IMAP
  WATCH_SINCE  -> epoch UTC; solo cuentan correos con Date >= WATCH_SINCE
"""
import argparse
import datetime
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import sys

try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo("Europe/Madrid")
except Exception:
    MADRID = None

AUTOMATED_SENDER_RE = re.compile(
    r"(?:no-reply|noreply|no_reply|mailer-daemon|postmaster|donotreply|notifications|bounce)",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
QUOTE_MARKERS = [
    re.compile(r"\n\s*>"),
    re.compile(r"\n\s*El\s.{0,140}?escribió:", re.IGNORECASE | re.DOTALL),
    re.compile(r"\n\s*On\s.{0,140}?wrote:", re.IGNORECASE | re.DOTALL),
    re.compile(r"\n-{4,}\s*Original", re.IGNORECASE),
    re.compile(r"\n\s*De:\s.{0,80}\n\s*Enviado:", re.IGNORECASE),
    re.compile(r"\n_{5,}"),
]
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MESES_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def log(message):
    print(message, file=sys.stderr)


def decode_hdr(raw):
    if not raw:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(raw)))
    except Exception:
        return raw


def load_config():
    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USER")
    password = os.environ.get("IMAP_PASS")
    port = os.environ.get("IMAP_PORT", "993")
    missing = [n for n, v in (("IMAP_HOST", host), ("IMAP_USER", user), ("IMAP_PASS", password)) if not v]
    if missing:
        raise SystemExit("Faltan variables IMAP: " + ", ".join(missing))
    return {"host": host, "port": int(port), "user": user, "password": password}


def load_seen(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return set(json.load(handle))
    except (FileNotFoundError, ValueError):
        return set()


def save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sorted(seen), handle, ensure_ascii=False, indent=0)


def imap_since_date(epoch, fallback_days):
    if epoch:
        day = datetime.datetime.utcfromtimestamp(epoch)
    else:
        day = datetime.datetime.utcnow() - datetime.timedelta(days=fallback_days)
    return f"{day.day:02d}-{IMAP_MONTHS[day.month - 1]}-{day.year:04d}"


def fetch_message(mailbox, uid):
    status, data = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
    if status != "OK":
        return None
    for item in data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return email.message_from_bytes(item[1])
    return None


def email_epoch(date_raw):
    try:
        dt = email.utils.parsedate_to_datetime(date_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def human_date(date_raw):
    try:
        dt = email.utils.parsedate_to_datetime(date_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        tz = MADRID or datetime.timezone.utc
        dt = dt.astimezone(tz)
        delta = (datetime.datetime.now(tz).date() - dt.date()).days
        hhmm = f"{dt.hour:02d}:{dt.minute:02d}"
        if delta == 0:
            return f"hoy {hhmm}"
        if delta == 1:
            return f"ayer {hhmm}"
        return f"{dt.day} {MESES_ES[dt.month - 1]} {dt.year}, {hhmm}"
    except (TypeError, ValueError):
        return (date_raw or "")[:31]


def extract_preview(message, limit=220):
    text = ""
    if message.is_multipart():
        for part in message.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(part.get_content_charset() or "utf-8", "replace")
                    break
        if not text:
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        raw = payload.decode(part.get_content_charset() or "utf-8", "replace")
                        text = HTML_TAG_RE.sub(" ", raw)
                        break
    else:
        payload = message.get_payload(decode=True)
        if payload:
            text = payload.decode(message.get_content_charset() or "utf-8", "replace")
            if message.get_content_type() == "text/html":
                text = HTML_TAG_RE.sub(" ", text)
    cuts = [m.start() for m in (rx.search(text) for rx in QUOTE_MARKERS) if m]
    if cuts:
        text = text[:min(cuts)]
    text = SPACE_RE.sub(" ", text).strip()
    if not text:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def build_notice(items, total_unseen=None):
    n = len(items)
    cab = f"📬  *Tienes {n} correos nuevos*" if n > 1 else "📬  *Tienes 1 correo nuevo*"
    lines = [cab, ""]
    for it in items:
        quien = it["from_name"] or it["from_email"] or "(desconocido)"
        lines.append(f"*{it['subject'] or '(sin asunto)'}*")
        lines.append(f"De {quien} · {it['date_human']}")
        lines.append("")
        if it.get("preview"):
            lines.append(f"💬  _{it['preview']}_")
            lines.append("")
    if total_unseen and total_unseen > n:
        lines.append(f"📥  {total_unseen} sin leer en total en la bandeja.")
        lines.append("")
    lines.append("— Watcher pausado (no gasta minutos).")
    lines.append("`tools/mailwatch.sh on` para seguir vigilando.")
    return "\n".join(lines)


def count_unseen(mailbox):
    status, data = mailbox.uid("search", None, "(UNSEEN)")
    if status != "OK" or not data or not data[0]:
        return 0
    return len(data[0].split())


def main():
    parser = argparse.ArgumentParser(description="Watcher IMAP: avisa de correo nuevo de humano")
    parser.add_argument("--fallback-days", type=int, default=2, help="ventana IMAP si no hay WATCH_SINCE")
    parser.add_argument("--out", default="watch.json")
    parser.add_argument("--notice", default="notice.txt")
    parser.add_argument("--state", default="state/seen.json")
    args = parser.parse_args()

    since_epoch = int(os.environ.get("WATCH_SINCE") or 0)
    config = load_config()
    seen = load_seen(args.state)

    mailbox = imaplib.IMAP4_SSL(config["host"], config["port"])
    mailbox.login(config["user"], config["password"])
    try:
        mailbox.select("INBOX", readonly=True)
        criteria = f"(UNSEEN SINCE {imap_since_date(since_epoch, args.fallback_days)})"
        status, data = mailbox.uid("search", None, criteria)
        uids = data[0].split() if status == "OK" and data and data[0] else []

        items = []
        for uid in uids:
            message = fetch_message(mailbox, uid)
            if message is None:
                continue
            from_value = decode_hdr(message.get("From", ""))
            from_name, from_email = email.utils.parseaddr(from_value)
            from_email = from_email.lower()
            message_id = decode_hdr(message.get("Message-ID", "")).strip()
            date_raw = decode_hdr(message.get("Date", ""))
            if AUTOMATED_SENDER_RE.search(from_email):
                continue
            if message_id and message_id in seen:
                continue
            epoch = email_epoch(date_raw)
            if since_epoch and epoch is not None and epoch < since_epoch:
                continue
            items.append({
                "message_id": message_id,
                "from_name": decode_hdr(from_name),
                "from_email": from_email,
                "subject": decode_hdr(message.get("Subject", "")),
                "date": date_raw,
                "date_human": human_date(date_raw),
                "preview": extract_preview(message),
            })
        total_unseen = count_unseen(mailbox)
    finally:
        try:
            mailbox.logout()
        except Exception:
            pass

    result = {"checked": len(uids), "new_human": len(items), "total_unseen": total_unseen, "items": items}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    if items:
        with open(args.notice, "w", encoding="utf-8") as handle:
            handle.write(build_notice(items, total_unseen))
        for it in items:
            if it["message_id"]:
                seen.add(it["message_id"])
        save_seen(args.state, seen)

    log(f"checked={len(uids)} new_human={len(items)} total_unseen={total_unseen} since_epoch={since_epoch}")


if __name__ == "__main__":
    main()
