import datetime
import zoneinfo

import requests
from astral import LocationInfo
from astral.sun import sun

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/London")

STATION_NAME = "Erith"
LAT = 51.48
LON = 0.18

DAYS_TO_CHECK = 7
PREP_START_HOUR = 11
SUNSET_BUFFER_HOURS = 1

SLIPWAY_HEIGHT_CD = 2.0  # TODO: update once calibrated

API_BASE = "https://admiraltyapi.azure-api.net/uktidalapi/api/V1"

TWELFTHS_CUMULATIVE = [0, 1/12, 3/12, 6/12, 9/12, 11/12, 12/12]


def get_sunset(date):
    loc = LocationInfo(
        name="Erith", region="UK", timezone="Europe/London",
        latitude=LAT, longitude=LON,
    )
    return sun(loc.observer, date=date)["sunset"]


def _api_get(api_keys, path, params=None):
    for key in api_keys:
        try:
            resp = requests.get(
                f"{API_BASE}{path}",
                headers={"Ocp-Apim-Subscription-Key": key},
                params=params,
            )
        except requests.ConnectionError:
            raise RuntimeError("Could not connect to Admiralty API")
        if resp.ok:
            return resp.json()
        if resp.status_code in (401, 403, 429):
            continue
        raise RuntimeError(f"API request failed ({resp.status_code})")
    raise RuntimeError("All API keys failed")


def find_station_id(api_keys, name):
    data = _api_get(api_keys, "/Stations", params={"name": name})
    features = data.get("features", [])
    if not features:
        raise RuntimeError(f"No tidal station found matching '{name}'")
    return features[0]["properties"]["Id"]


def get_tide_events(api_keys, station_id):
    raw = _api_get(api_keys, f"/Stations/{station_id}/TidalEvents", params={"duration": DAYS_TO_CHECK})
    events = []
    for event in raw:
        if "DateTime" not in event or "Height" not in event:
            continue
        dt = datetime.datetime.fromisoformat(event["DateTime"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        events.append({
            "type": event["EventType"],
            "datetime": dt,
            "height": event["Height"],
        })
    return events


def tide_height_at_time(low, high, t):
    duration = (high["datetime"] - low["datetime"]).total_seconds()
    if duration <= 0:
        return None
    elapsed = (t - low["datetime"]).total_seconds()
    if elapsed < 0 or elapsed > duration:
        return None
    fraction = elapsed / duration
    tidal_range = high["height"] - low["height"]
    slot = fraction * 6
    slot_idx = min(int(slot), 5)
    slot_frac = slot - slot_idx
    cumulative = TWELFTHS_CUMULATIVE[slot_idx] + slot_frac * (TWELFTHS_CUMULATIVE[slot_idx + 1] - TWELFTHS_CUMULATIVE[slot_idx])
    return low["height"] + cumulative * tidal_range


def time_tide_reaches_height(low, high, target_height):
    if target_height <= low["height"]:
        return low["datetime"]
    if target_height > high["height"]:
        return None
    tidal_range = high["height"] - low["height"]
    if tidal_range <= 0:
        return None
    target_fraction = (target_height - low["height"]) / tidal_range
    duration = (high["datetime"] - low["datetime"]).total_seconds()
    for i in range(6):
        if target_fraction <= TWELFTHS_CUMULATIVE[i + 1]:
            slot_range = TWELFTHS_CUMULATIVE[i + 1] - TWELFTHS_CUMULATIVE[i]
            slot_frac = 0 if slot_range == 0 else (target_fraction - TWELFTHS_CUMULATIVE[i]) / slot_range
            time_fraction = (i + slot_frac) / 6
            seconds = time_fraction * duration
            return low["datetime"] + datetime.timedelta(seconds=seconds)
    return None


def find_launch_windows(events, min_float_height):
    windows = []
    for i in range(len(events) - 1):
        low = events[i]
        high = events[i + 1]
        if low["type"] != "LowWater" or high["type"] != "HighWater":
            continue
        if high["height"] < min_float_height:
            continue
        float_time = time_tide_reaches_height(low, high, min_float_height)
        if float_time is None:
            continue
        local_float = float_time.astimezone(LOCAL_TZ)
        local_high = high["datetime"].astimezone(LOCAL_TZ)
        date = local_high.date()
        sunset = get_sunset(date).astimezone(LOCAL_TZ)
        earliest = local_high.replace(hour=PREP_START_HOUR, minute=0, second=0, microsecond=0)
        latest = sunset - datetime.timedelta(hours=SUNSET_BUFFER_HOURS)
        local_low = low["datetime"].astimezone(LOCAL_TZ)
        if local_high > earliest and local_float < latest:
            windows.append({
                "date": date.isoformat(),
                "low_tide_time": local_low.strftime("%H:%M"),
                "boat_floats_time": local_float.strftime("%H:%M"),
                "high_tide_time": local_high.strftime("%H:%M"),
                "high_tide_height_m": round(high["height"], 1),
                "sunset_time": sunset.strftime("%H:%M"),
            })
    return windows


def get_windows(api_keys, trailer_height=0.5, yacht_draft=1.14):
    """Main entry point: returns launch windows as a list of dicts."""
    min_float_height = SLIPWAY_HEIGHT_CD + trailer_height + yacht_draft
    station_id = find_station_id(api_keys, STATION_NAME)
    events = get_tide_events(api_keys, station_id)
    return find_launch_windows(events, min_float_height)
