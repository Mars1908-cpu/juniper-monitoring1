"""
emailer.py - send the daily DR health-check report by SMTP.

Drop this in app/ and call send_report(devices) after the daily collection.
Each device dict:
    {
      "name": "MITS-DR-QRSSW2", "ip": "172.31.0.7",
      "outputs": {                      # raw CLI text, same commands as your checklist
          "show system uptime": "...",
          "show chassis alarms": "...",
          "show ntp status": "...",
          "show ntp associations": "...",
      },
      "error": None                     # or "SSH timeout" if login failed
    }

Config (environment variables):
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_SECURITY = starttls | ssl | none   (default starttls)
    MAIL_FROM, MAIL_TO (comma separated), MAIL_CC (optional)
"""
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape


# ---------- health evaluation (parses the same output your screenshots show) ----------
def evaluate(dev):
    out = dev.get("outputs", {})
    if dev.get("error"):
        return {"status": "FAIL", "uptime": "-", "alarms": "-", "ntp": dev["error"]}

    # uptime: "9:57AM  up 57 days, 23:57, 1 users, ..."
    m = re.search(r"\bup (.+?), \d+ users?", out.get("show system uptime", ""))
    uptime = m.group(1) if m else "n/a"

    alarms_txt = out.get("show chassis alarms", "")
    alarms_ok = "No alarms currently active" in alarms_txt
    alarms = "None" if alarms_ok else "ACTIVE"

    # ntp: peer marked with '*' in 'show ntp associations'
    peer = re.search(r"^\*(\S+)", out.get("show ntp associations", ""), re.M)
    synced = "sync_ntp" in out.get("show ntp status", "") and peer is not None
    ntp = f"Synced to {peer.group(1)}" if synced else "NOT SYNCED"

    status = "OK" if (alarms_ok and synced) else "ATTENTION"
    return {"status": status, "uptime": uptime, "alarms": alarms, "ntp": ntp}


# ---------- message building ----------
COLORS = {"OK": "#1b7f3b", "ATTENTION": "#b36b00", "FAIL": "#b00020"}


def build_message(devices, sender, to, cc=None, subject=None):
    now = datetime.now().strftime("%d-%b-%Y %H:%M IST")
    rows, results = [], []
    for d in devices:
        r = evaluate(d)
        results.append(r)
        rows.append(
            "<tr>"
            f"<td>{escape(d['name'])}</td><td>{escape(d.get('ip', ''))}</td>"
            f"<td>{escape(r['uptime'])}</td><td>{escape(r['alarms'])}</td>"
            f"<td>{escape(r['ntp'])}</td>"
            f"<td style='color:#fff;background:{COLORS[r['status']]};"
            f"text-align:center'><b>{r['status']}</b></td></tr>"
        )

    bad = sum(1 for r in results if r["status"] != "OK")
    subject = subject or (
        f"DR Health Check {datetime.now():%d-%b-%Y} - "
        + ("ALL OK" if bad == 0 else f"{bad} device(s) need attention")
    )

    html = f"""
    <html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:13px">
    <p>Hi Team,</p>
    <p>DR network health check completed on <b>{now}</b>.
       Detailed command output is attached per device.</p>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse;border-color:#ccc">
      <tr style="background:#f0f0f0">
        <th>Device</th><th>IP</th><th>Uptime</th><th>Chassis alarms</th>
        <th>NTP</th><th>Status</th></tr>
      {''.join(rows)}
    </table>
    <p>Regards,<br>Network Team</p>
    </body></html>"""

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content("HTML mail - please view in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    # one text attachment per device = the 'screenshot' evidence
    for d in devices:
        body = "\n\n".join(
            f"### {cmd}\n{txt}" for cmd, txt in d.get("outputs", {}).items()
        ) or (d.get("error") or "no data")
        msg.add_attachment(
            body.encode(), maintype="text", subtype="plain",
            filename=f"{d['name']}_{datetime.now():%Y%m%d}.txt",
        )
    return msg


# ---------- sending ----------
def send_report(devices, subject=None):
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    security = os.getenv("SMTP_SECURITY", "starttls").lower()
    user, pwd = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    sender = os.environ["MAIL_FROM"]
    to = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]
    cc = [a.strip() for a in os.getenv("MAIL_CC", "").split(",") if a.strip()]

    msg = build_message(devices, sender, to, cc, subject)
    recipients = to + cc

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server:
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if user and pwd:                      # internal relays often need no login
            server.login(user, pwd)
        server.send_message(msg, from_addr=sender, to_addrs=recipients)


if __name__ == "__main__":
    # quick test: python emailer.py   (set the env vars first)
    sample = [{
        "name": "TEST-SW1", "ip": "10.0.0.1",
        "outputs": {
            "show system uptime": "9:57AM  up 57 days, 23:57, 1 users, load averages: 0.4, 0.5, 0.5",
            "show chassis alarms": "No alarms currently active",
            "show ntp status": "associd=0 status=0615 leap_none, sync_ntp, 1 event, clock_sync",
            "show ntp associations": "*172.31.0.30   65.0.119.56   -   5 u 875 1024 377",
        },
    }]
    send_report(sample, subject="TEST - DR health check mail")
    print("sent")
