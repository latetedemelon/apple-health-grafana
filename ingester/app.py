"""
Ingester module that converts an Apple Health export zip file into
VictoriaMetrics datapoints (InfluxDB line protocol).

Part of the unified personal-health-data platform: every datapoint is
tagged ``provider=apple`` (and ``ingest=export``) so it can be stored
alongside the other sources yet remain independently filterable.
"""
import os
import re
import subprocess
import time
from shutil import unpack_archive
from typing import Any, Dict, List

import gpxpy
from gpxpy.gpx import GPXTrackPoint
from lxml import etree

from formatters import (
    AppleStandHourFormatter,
    SleepAnalysisFormatter,
    parse_date_as_timestamp,
    parse_float_with_try,
)
from victoria import VictoriaMetricsWriter

ZIP_PATH = os.environ.get("EXPORT_ZIP_PATH", "/export.zip")
ROUTES_PATH = "/export/apple_health_export/workout-routes/"
EXPORT_PATH = "/export/apple_health_export"
# support both the english "export.xml" and the chinese "导出.xml"
EXPORT_XML_REGEX = re.compile("(export|导出)\\.xml", re.IGNORECASE)

# VictoriaMetrics base URL, e.g. http://victoriametrics:8428
VICTORIA_METRICS_URL = (
    os.environ.get("VICTORIA_METRICS_URL")
    or os.environ.get("VICTORIAMETRICS_URL")
    or "http://victoriametrics:8428"
)
PROVIDER = os.environ.get("PROVIDER", "apple")

points_sources = set()


def format_route_point(name: str, point: GPXTrackPoint, next_point=None) -> Dict[str, Any]:
    """For a given `point`, create a datapoint and compute speed/distance
    if `next_point` exists."""
    slug_name = name.replace(" ", "-").replace(":", "-").lower()
    datapoint = {
        "measurement": "workout-routes",
        "tags": {"workout": slug_name},
        "time": point.time,
        "fields": {
            "latitude": point.latitude,
            "longitude": point.longitude,
            "elevation": point.elevation,
        },
    }
    if next_point:
        datapoint["fields"]["speed"] = point.speed_between(next_point) if next_point else 0
        datapoint["fields"]["distance"] = point.distance_3d(next_point)
    return datapoint


def format_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Format an Apple Health export xml `Record` element."""
    measurement = (
        record.get("type", "Record")
        .removeprefix("HKQuantityTypeIdentifier")
        .removeprefix("HKCategoryTypeIdentifier")
        .removeprefix("HKDataType")
    )

    if measurement == "AppleStandHour":
        return AppleStandHourFormatter(record)
    if measurement == "SleepAnalysis":
        return SleepAnalysisFormatter(record)

    date = parse_date_as_timestamp(record.get("startDate", 0))
    value = parse_float_with_try(record.get("value", 1))
    unit = record.get("unit", "unit")
    device = record.get("sourceName", "unknown")

    return [
        {
            "measurement": measurement,
            "time": date,
            "fields": {"value": value},
            "tags": {"unit": unit, "device": device},
        }
    ]


def format_workout(record: Dict[str, Any]) -> Dict[str, Any]:
    """Format an Apple Health export xml `Workout` element."""
    measurement = record.get("workoutActivityType", "Workout").removeprefix(
        "HKWorkoutActivityType"
    )
    date = parse_date_as_timestamp(record.get("startDate", 0))
    value = parse_float_with_try(record.get("duration", 0))
    unit = record.get("durationUnit", "unit")
    device = record.get("sourceName", "unknown")

    return {
        "measurement": measurement,
        "time": date,
        "fields": {"value": value},
        "tags": {"unit": unit, "device": device},
    }


def parse_workout_route(writer: VictoriaMetricsWriter, route_xml_file: str) -> None:
    with open(route_xml_file, "r") as gpx_file:
        gpx = gpxpy.parse(gpx_file)
        for track in gpx.tracks:
            track_points = []
            print("opening", track.name)
            for segment in track.segments:
                num_points = len(segment.points)
                for i in range(num_points):
                    track_points.append(
                        format_route_point(
                            track.name,
                            segment.points[i],
                            segment.points[i + 1] if i + 1 < num_points else None,
                        )
                    )
            writer.add(track_points)


def process_workout_routes(writer: VictoriaMetricsWriter) -> None:
    if os.path.exists(ROUTES_PATH) and os.path.isdir(ROUTES_PATH):
        print("loading workout routes ...")
        for file in os.listdir(ROUTES_PATH):
            if file.endswith(".gpx"):
                parse_workout_route(writer, os.path.join(ROUTES_PATH, file))
    else:
        print("no workout routes found, skipping ...")


def process_health_data(writer: VictoriaMetricsWriter) -> None:
    export_xml_files = [f for f in os.listdir(EXPORT_PATH) if EXPORT_XML_REGEX.match(f)]
    if not export_xml_files:
        print("no export file found, skipping...")
        return
    export_file = os.path.join(EXPORT_PATH, export_xml_files[0])
    print("export file is", export_file)

    print("removing potentially malformed XML..")
    p = subprocess.run(
        "sed -i '/<HealthData/,$!d' " + export_file, shell=True, capture_output=True
    )
    if p.returncode != 0:
        print(p.stdout, p.stderr)

    context = etree.iterparse(export_file, recover=True)
    for _, elem in context:
        points_sources.add(elem.get("sourceName", "unknown"))
        if elem.tag == "Record":
            writer.add(format_record(elem))
        elif elem.tag == "Workout":
            writer.add([format_workout(elem)])
        elem.clear()

    writer.flush()
    print("health records processed")


def push_sources(writer: VictoriaMetricsWriter) -> None:
    sources_points = [
        {"measurement": "data-sources", "tags": {"device": s}, "fields": {"value": 1}, "time": time.time()}
        for s in points_sources
    ]
    print("pushing", len(sources_points), "sources !")
    writer.add(sources_points)
    writer.flush()


def main() -> None:
    print("unzipping the export file...")
    try:
        unpack_archive(ZIP_PATH, "/export")
    except Exception as unzip_err:  # noqa: BLE001
        print("unable to open export zip:", unzip_err)
        raise SystemExit(1)
    print("export file unzipped!")

    writer = VictoriaMetricsWriter(
        base_url=VICTORIA_METRICS_URL,
        provider=PROVIDER,
        extra_tags={"ingest": "export"},
    )
    if not writer.wait_ready():
        print("victoriametrics never became ready, aborting")
        raise SystemExit(1)

    process_workout_routes(writer)
    process_health_data(writer)
    push_sources(writer)
    print("all done! you can now check grafana.")


if __name__ == "__main__":
    main()
