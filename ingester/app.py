"""
Ingester module that converts Apple Health export zip file
into Victoria Metrics datapoints
"""
import os
import re
import time
import requests
from lxml import etree
from shutil import unpack_archive
from typing import Any
from formatters import parse_date_as_timestamp, parse_float_with_try, AppleStandHourFormatter, SleepAnalysisFormatter
import gpxpy
from gpxpy.gpx import GPXTrackPoint

ZIP_PATH = "/export.zip"
ROUTES_PATH = "/export/apple_health_export/workout-routes/"
EXPORT_PATH = "/export/apple_health_export"
EXPORT_XML_REGEX = re.compile("(export|导出)\\.xml", re.IGNORECASE)

points_sources = set()

victoriametrics_url = "http://victoriametrics:8428/write"

def send_to_victoriametrics(data: list[dict[str, Any]], url: str):
    lines = []
    for point in data:
        tags = ",".join([f"{k}={v}" for k, v in point["tags"].items()])
        fields = ",".join([f"{k}={v}" for k, v in point["fields"].items()])
        lines.append(f'{point["measurement"]},{tags} {fields} {int(time.mktime(point["time"].timetuple()))}')
    
    payload = "\n".join(lines)
    
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
        print(f"Successfully sent {len(data)} points")
    except Exception as err:
        print(f"Error sending data: {err}")

def process_health_data():
    # ... process records
    send_to_victoriametrics(records, victoriametrics_url)
