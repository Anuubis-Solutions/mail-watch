#!/usr/bin/env python3
"""Envia un DM de Google Chat como claudio.anuubis vía OAuth de usuario. STDLIB only.
Reusa las mismas credenciales/token que src/Anubis/gchat (refresh_token de usuario).
Credenciales y token via env GCHAT_CREDENTIALS / GCHAT_TOKEN (JSON) o por --credentials/--token."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URI = "https://oauth2.googleapis.com/token"
CHAT = "https://chat.googleapis.com/v1"


def _request(url, data=None, headers=None, method="GET"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def refresh_access_token(creds, token):
    cfg = creds.get("installed") or creds.get("web") or creds
    payload = urllib.parse.urlencode({
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("utf-8")
    out = _request(TOKEN_URI, data=payload,
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    return out["access_token"]


def auth_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def resolve_or_create_dm(access_token, email):
    headers = auth_headers(access_token)
    query = urllib.parse.urlencode({"name": f"users/{email}"})
    try:
        found = _request(f"{CHAT}/spaces:findDirectMessage?{query}", headers=headers, method="GET")
        return found["name"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    body = json.dumps({
        "space": {"spaceType": "DIRECT_MESSAGE"},
        "memberships": [{"member": {"name": f"users/{email}", "type": "HUMAN"}}],
    }).encode("utf-8")
    created = _request(f"{CHAT}/spaces:setup", data=body, headers=headers, method="POST")
    return created["name"]


def send_message(access_token, space, request_body):
    body = json.dumps(request_body).encode("utf-8")
    return _request(f"{CHAT}/{space}/messages", data=body, headers=auth_headers(access_token), method="POST")


def build_text_report(report):
    """Texto con formato de Google Chat (negritas *, vinetas) — bonito y legible.
    (No se pueden usar tarjetas cardsV2 con credenciales de usuario: solo bots.)"""
    metrics = report.get("metricas", {}) or {}
    fecha = str(report.get("fecha", "")).strip()
    titular = str(report.get("titular", "")).strip()
    resumen = str(report.get("resumen", "")).strip()
    destacados = [str(x) for x in (report.get("destacados") or [])]
    acciones = [str(x) for x in (report.get("acciones") or [])]

    def fmt(value):
        return "-" if value is None else value

    lines = ["👋 ¡Hola Iratxe! Aquí va tu resumen del día:", ""]
    lines.append("📬 *Informe diario · Correos*" + (f"  ·  {fecha}" if fecha else ""))
    lines.append("")
    if titular:
        lines.append(f"💡 *{titular}*")
    if resumen:
        lines.append(resumen)
    lines.append("")
    lines.append("📊 *Métricas de hoy*")
    detalle = ""
    if metrics.get("respuestas") is not None:
        detalle = f"   ({fmt(metrics.get('respuestas'))} resp. · {fmt(metrics.get('nuevos'))} nuevos)"
    lines.append(f"   📤  Enviados: *{fmt(metrics.get('enviados'))}*{detalle}")
    lines.append(f"   📝  Borradores listos para revisar: *{fmt(metrics.get('borradores_pendientes'))}*")
    lines.append(f"   📥  Sin leer en bandeja: *{fmt(metrics.get('sin_leer'))}*")
    if destacados:
        lines += ["", "⭐ *Destacados*"] + [f"   •  {d}" for d in destacados]
    if acciones:
        lines += ["", "📌 *Para mañana / a tener en cuenta*"] + [f"   •  {a}" for a in acciones]
    lines += ["", "_Resumen automático de Claudio_ 🤖 _· que tengas buena tarde_ ✨"]
    return "\n".join(lines)


def load_json(env_name, path):
    raw = os.environ.get(env_name)
    if raw:
        return json.loads(raw)
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    raise SystemExit(f"Falta {env_name} (env) o su archivo")


def main():
    parser = argparse.ArgumentParser(description="Enviar DM de Google Chat")
    parser.add_argument("--email", required=True, help="email destino (DM)")
    parser.add_argument("--report-json", default=None, help="report.json estructurado -> tarjeta")
    parser.add_argument("--text-file", default=None, help="archivo con texto plano (alternativa)")
    parser.add_argument("--credentials", default=None, help="ruta gchat-credentials.json (fallback)")
    parser.add_argument("--token", default=None, help="ruta gchat-token.json (fallback)")
    args = parser.parse_args()

    creds = load_json("GCHAT_CREDENTIALS", args.credentials)
    token = load_json("GCHAT_TOKEN", args.token)

    if args.report_json:
        with open(args.report_json, "r", encoding="utf-8") as handle:
            request_body = {"text": build_text_report(json.load(handle))}
    elif args.text_file:
        text = open(args.text_file, "r", encoding="utf-8").read().strip() or "Sin datos."
        request_body = {"text": text}
    else:
        raise SystemExit("Falta --report-json o --text-file")

    access_token = refresh_access_token(creds, token)
    space = resolve_or_create_dm(access_token, args.email)
    send_message(access_token, space, request_body)
    print(f"DM enviado -> {space}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"ERROR HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}\n")
        sys.exit(1)
