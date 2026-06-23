# mail-watch

Watcher de bandeja de correo bajo demanda. Comprueba un buzón IMAP cada 2 horas y,
en cuanto llega correo nuevo de un humano, avisa por Google Chat y **se apaga solo**.

Pensado para cuando esperas una respuesta concreta pero no sueles abrir el correo:
lo enciendes, te avisa cuando llega algo, y deja de gastar minutos automáticamente.

**Repo público a propósito** → GitHub Actions es gratis e ilimitado, no consume cuota
de ninguna cuenta ni de la organización. El código no contiene ningún secreto; las
credenciales viven en *Settings → Secrets and variables → Actions* (GitHub nunca las
expone, ni en repo público ni en los logs).

## Encender / apagar

Desde el repo VanguardIA:

```
tools/mailwatch.sh on       # empieza a vigilar (baseline = ahora)
tools/mailwatch.sh off      # deja de vigilar (0 minutos)
tools/mailwatch.sh status   # ¿está encendido? ¿desde cuándo?
tools/mailwatch.sh check    # fuerza una comprobación ya, sin esperar al cron
```

Apagado, el cron **no se ejecuta** (no es que arranque y salga: directamente no corre).

## Cómo funciona

1. `mailwatch.sh on` fija la variable `WATCH_SINCE` (epoch UTC = ahora) y habilita el workflow.
2. Cada 2h, `watch.py` lee por IMAP los correos NO leídos llegados **después** de ese baseline,
   ignorando remitentes automáticos (no-reply, notificaciones, mailer-daemon…).
3. Si hay alguno de un humano: avisa por Google Chat (`gchat_send.py`), guarda el Message-ID
   en `state/seen.json` (para no repetir) y **se auto-deshabilita**.

El baseline evita que los correos viejos sin leer de la bandeja disparen el aviso: solo
cuenta lo que llega a partir del momento en que lo enciendes.

## Configuración (una sola vez)

En *Settings → Secrets and variables → Actions* del repo:

**Secrets:**
- `WATCH_IMAP_USER` — buzón a vigilar (p. ej. `alex@anubis.es`)
- `WATCH_IMAP_PASS` — contraseña IMAP de ese buzón (IONOS)
- `GCHAT_CREDENTIALS` — mismo JSON que `anubis-mail-drafter`
- `GCHAT_TOKEN` — mismo JSON que `anubis-mail-drafter`

**Variables:**
- `WATCH_NOTIFY_EMAIL` — cuenta de Google Chat que recibe el aviso (DM)
- `WATCH_SINCE` — la fija `mailwatch.sh on` automáticamente (no tocar a mano)

IMAP fijado a IONOS (`imap.ionos.es:993`) en el workflow; cámbialo ahí si el buzón es de otro proveedor.

## Prueba manual

Con los secrets puestos: `tools/mailwatch.sh on` y luego `tools/mailwatch.sh check`
para forzar una comprobación inmediata.
