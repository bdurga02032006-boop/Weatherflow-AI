import os

# ---- Fill these in (placeholders only - never share or commit real values) ----
# Colab tip: you can instead use  from google.colab import userdata
#            os.environ["OPENWEATHER_API_KEY"] = userdata.get("OPENWEATHER_API_KEY")
os.environ["OPENWEATHER_API_KEY"] = "3f99a36850fc4a09943df45698361445"
os.environ["SMTP_HOST"] = "smtp.gmail.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USERNAME"] = "deepathangadurai923@gmail.com"
os.environ["SMTP_PASSWORD"] = "yhon lsce gnkc iott"   # Gmail App Password, not your normal password
os.environ["EMAIL_TO"] = "bdurgabalakrishnan@gmail.com"
# --------------------------------------------------------------------------------


"""
Tokyo Weather Automation (single-file, Python 3.10)

Fetches the current weather for Tokyo, Japan from the OpenWeather API,
formats a simple plain-text report, and sends it through Gmail SMTP.

Everything lives in this one file: configuration, API request, parsing,
Kelvin -> Celsius conversion, report formatting, SMTP email and the entry point.

Required environment variables:
    OPENWEATHER_API_KEY, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_TO
Optional environment variables (defaults shown):
    SMTP_HOST=smtp.gmail.com, SMTP_PORT=587

SECURITY NOTE: Exception text from `requests` can contain the request URL
(which includes the API key), so raw exception messages are never printed.
"""

import os
import smtplib
import socket
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
LOCATION_QUERY = "Tokyo,Japan"
LOCATION_LABEL = "Tokyo, Japan"
EMAIL_SUBJECT = "Tokyo Weather Update"

REQUEST_TIMEOUT_SECONDS = 15
SMTP_TIMEOUT_SECONDS = 30

# Japan Standard Time is UTC+9 all year (no daylight saving time).
TOKYO_TZ = timezone(timedelta(hours=9), "JST")

NA = "N/A"

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
SMTP_HOST = os.getenv("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = os.getenv("SMTP_PORT") or "587"
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")


class AutomationError(Exception):
    """Raised for any expected failure. The message is always safe to print."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_environment() -> None:
    """Make sure all required environment variables are set (values never shown)."""
    required = {
        "OPENWEATHER_API_KEY": OPENWEATHER_API_KEY,
        "SMTP_USERNAME": SMTP_USERNAME,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "EMAIL_TO": EMAIL_TO,
    }
    missing = [name for name, value in required.items() if not (value and value.strip())]
    if missing:
        # Only variable NAMES are listed, never values.
        raise AutomationError(
            "ERROR: Required environment variable is missing.\n"
            "Missing: " + ", ".join(missing)
        )
    try:
        int(SMTP_PORT)
    except ValueError:
        raise AutomationError("ERROR: SMTP_PORT must be a number (for example 587).")


def kelvin_to_celsius(kelvin):
    """Celsius = Kelvin - 273.15. Returns None if the value is missing/invalid."""
    try:
        return float(kelvin) - 273.15
    except (TypeError, ValueError):
        return None


def _to_float(value):
    """Convert a value to float, or None if missing/invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value, decimals=0):
    """Format a number to a fixed number of decimals, or N/A."""
    number = _to_float(value)
    if number is None:
        return NA
    return f"{number:.{decimals}f}"


def _fmt_with_unit(value, unit, decimals=0):
    """Format 'value unit' (e.g. '21.5 °C'), or N/A when the value is missing."""
    text = _fmt_number(value, decimals)
    return NA if text == NA else f"{text} {unit}"


def _fmt_degrees(value):
    """Wind direction such as '250°', or N/A."""
    text = _fmt_number(value, 0)
    return NA if text == NA else f"{text}°"


def _fmt_clock(unix_ts):
    """Convert a Unix timestamp to Tokyo local time 'HH:MM', or N/A."""
    ts = _to_float(unix_ts)
    if ts is None:
        return NA
    try:
        return datetime.fromtimestamp(ts, tz=TOKYO_TZ).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return NA


def _text_or_na(value):
    """Return a non-empty string, or N/A."""
    if value is None:
        return NA
    text = str(value).strip()
    return text if text else NA


def _as_dict(value):
    """Return value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# OpenWeather API
# ---------------------------------------------------------------------------

def get_weather() -> dict:
    """Request current weather for Tokyo, Japan and return the parsed JSON dict."""
    params = {"q": LOCATION_QUERY, "appid": OPENWEATHER_API_KEY}

    try:
        response = requests.get(OPENWEATHER_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        raise AutomationError("ERROR: OpenWeather API request timed out.")
    except requests.exceptions.ConnectionError:
        raise AutomationError("ERROR: Could not connect to the OpenWeather API. Check the network connection.")
    except requests.exceptions.RequestException:
        raise AutomationError("ERROR: OpenWeather API request failed.")

    status = response.status_code
    if status == 401:
        raise AutomationError("ERROR: Invalid OpenWeather API key.")
    if status == 404:
        raise AutomationError("ERROR: Tokyo weather location was not found.")
    if status == 429:
        raise AutomationError("ERROR: OpenWeather API rate limit exceeded.")
    if status != 200:
        raise AutomationError(f"ERROR: OpenWeather API returned an unexpected HTTP status ({status}).")

    try:
        data = response.json()
    except ValueError:
        raise AutomationError("ERROR: OpenWeather API returned invalid JSON.")

    if not isinstance(data, dict):
        raise AutomationError("ERROR: OpenWeather API returned an unexpected response format.")

    return data


def extract_weather(data: dict) -> dict:
    """Pull the required values out of the API response as display-ready strings.

    Any missing/invalid field becomes 'N/A' and never raises.
    """
    main = _as_dict(data.get("main"))
    wind = _as_dict(data.get("wind"))
    clouds = _as_dict(data.get("clouds"))
    sys_info = _as_dict(data.get("sys"))

    weather_list = data.get("weather")
    first_weather = {}
    if isinstance(weather_list, list) and weather_list:
        first_weather = _as_dict(weather_list[0])

    now_tokyo = datetime.now(tz=TOKYO_TZ)

    return {
        "location": LOCATION_LABEL,
        "temperature": _fmt_with_unit(kelvin_to_celsius(main.get("temp")), "°C", 1),
        "feels_like": _fmt_with_unit(kelvin_to_celsius(main.get("feels_like")), "°C", 1),
        "temp_min": _fmt_with_unit(kelvin_to_celsius(main.get("temp_min")), "°C", 1),
        "temp_max": _fmt_with_unit(kelvin_to_celsius(main.get("temp_max")), "°C", 1),
        "humidity": _fmt_with_unit(main.get("humidity"), "%", 0),
        "pressure": _fmt_with_unit(main.get("pressure"), "hPa", 0),
        "wind_speed": _fmt_with_unit(wind.get("speed"), "m/s", 1),
        "wind_direction": _fmt_degrees(wind.get("deg")),
        "weather": _text_or_na(first_weather.get("main")),
        "description": _text_or_na(first_weather.get("description")),
        "cloudiness": _fmt_with_unit(clouds.get("all"), "%", 0),
        "visibility": _fmt_with_unit(data.get("visibility"), "m", 0),
        "sunrise": _fmt_clock(sys_info.get("sunrise")),
        "sunset": _fmt_clock(sys_info.get("sunset")),
        "updated_at": now_tokyo.strftime("%Y-%m-%d %H:%M"),
    }


def format_weather_report(w: dict) -> str:
    """Build the plain-text email body."""
    lines = [
        f"Location: {w['location']}",
        f"Temperature: {w['temperature']}",
        f"Feels Like: {w['feels_like']}",
        f"Minimum Temperature: {w['temp_min']}",
        f"Maximum Temperature: {w['temp_max']}",
        f"Humidity: {w['humidity']}",
        f"Pressure: {w['pressure']}",
        f"Wind Speed: {w['wind_speed']}",
        f"Wind Direction: {w['wind_direction']}",
        f"Weather: {w['weather']}",
        f"Description: {w['description']}",
        f"Cloudiness: {w['cloudiness']}",
        f"Visibility: {w['visibility']}",
        f"Sunrise: {w['sunrise']}",
        f"Sunset: {w['sunset']}",
        f"Updated At: {w['updated_at']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SMTP email (implemented directly in this file - no separate SMTP module)
# ---------------------------------------------------------------------------

def send_email(report: str) -> None:
    """Send the weather report through Gmail SMTP using STARTTLS."""
    recipients = [addr.strip() for addr in EMAIL_TO.split(",") if addr.strip()]
    if not recipients:
        raise AutomationError("ERROR: Required environment variable is missing.\nMissing: EMAIL_TO")

    server = None
    try:
        # 1. Create SMTP connection
        server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=SMTP_TIMEOUT_SECONDS)
        server.ehlo()

        # 2. Start TLS
        server.starttls()
        server.ehlo()

        # 3. Login (use a Gmail App Password, not the normal password)
        server.login(SMTP_USERNAME, SMTP_PASSWORD)

        # 4-6. Create email, add subject, add weather report
        message = MIMEMultipart()
        message["From"] = SMTP_USERNAME
        message["To"] = ", ".join(recipients)
        message["Subject"] = EMAIL_SUBJECT
        message.attach(MIMEText(report, "plain", "utf-8"))

        # 7. Send email
        server.sendmail(SMTP_USERNAME, recipients, message.as_string())

    except smtplib.SMTPAuthenticationError:
        raise AutomationError(
            "ERROR: SMTP authentication failed. Check SMTP username and App Password."
        )
    except smtplib.SMTPRecipientsRefused:
        raise AutomationError("ERROR: The recipient address was refused by the SMTP server.")
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        raise AutomationError("ERROR: Could not establish a connection to the SMTP server.")
    except smtplib.SMTPException:
        raise AutomationError("ERROR: SMTP error while sending the email.")
    except (socket.timeout, TimeoutError):
        raise AutomationError("ERROR: SMTP connection timed out.")
    except (socket.gaierror, ConnectionError, OSError):
        raise AutomationError("ERROR: Could not connect to the SMTP server. Check SMTP host, port and network.")
    finally:
        # 8. Close SMTP connection
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main() -> bool:
    """Run the automation. Returns True on success, False on failure."""
    try:
        check_environment()
        raw = get_weather()
        weather = extract_weather(raw)
        report = format_weather_report(weather)
        send_email(report)
    except AutomationError as exc:
        print(str(exc), file=sys.stderr)
        return False
    except Exception:
        # Deliberately do not print exception details: they could contain secrets.
        print("ERROR: Unexpected failure in the weather automation.", file=sys.stderr)
        return False

    print("Weather report sent successfully.")
    return True


success = main()
if not success and "get_ipython" not in globals():
    sys.exit(1)   # non-zero exit when run as a plain script (e.g. GitHub Actions)