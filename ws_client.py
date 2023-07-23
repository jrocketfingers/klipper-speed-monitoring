import asyncio
import os
import websockets
import json
import requests
import influxdb_client
from datetime import datetime, timedelta

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

INFLUXDB_URL = os.environ["INFLUXDB_URL"]
INFLUXDB_TOKEN = os.environ["INFLUXDB_TOKEN"]
INFLUXDB_BUCKET = os.environ["INFLUXDB_BUCKET"]
INFLUXDB_ORG = os.environ["INFLUXDB_ORG"]
BASE_MOONRAKER_HOST = os.environ["BASE_MOONRAKER_HOST"]


write_client = influxdb_client.InfluxDBClient(
    url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
)
write_api = write_client.write_api(write_options=SYNCHRONOUS)


async def start_websocket_connection():
    uri = f"ws://{BASE_MOONRAKER_HOST}/websocket"

    async with websockets.connect(uri) as ws:
        connection_id = await identify_client(ws)

        # Now use connection_id to subscribe to the required objects via HTTP
        subscribe_to_objects(connection_id)

        # Listen for messages
        while True:
            data = await ws.recv()
            process_data(data)


async def identify_client(ws):
    identification_payload = {
        "jsonrpc": "2.0",
        "method": "server.connection.identify",
        "params": {
            "client_name": "YourClientName",
            "version": "0.0.1",
            "type": "web",
            "url": "http://your_client_url",
        },
        "id": 1,
    }

    await ws.send(json.dumps(identification_payload))

    # Wait for acknowledgment
    response_data = await ws.recv()
    response_json = json.loads(response_data)

    # Extract the connection_id from the response (assuming it's directly in the response)
    connection_id = response_json.get("result", {}).get("connection_id")

    return connection_id


def subscribe_to_objects(connection_id):
    endpoint = f"/printer/objects/subscribe?connection_id={connection_id}&motion_report"
    url = f"http://{BASE_MOONRAKER_HOST}{endpoint}"
    response = requests.post(url)

    # Log or handle the response as necessary
    if response.status_code == 200:
        print("Subscribed successfully")
    else:
        print(f"Subscription failed: {response.text}")


last_zero_timestamp = None


def process_data(data):
    global last_zero_timestamp
    current_time = datetime.utcnow()

    data_json = json.loads(data)
    if "motion_report" in data_json.get("params", [{}])[0]:
        motion_report = data_json["params"][0]["motion_report"]

        point = Point("motion_report").tag("source", "moonraker")

        if "live_position" in motion_report:
            point.field("live_position_x", motion_report["live_position"][0]).field(
                "live_position_y", motion_report["live_position"][1]
            ).field("live_position_z", motion_report["live_position"][2]).field(
                "live_position_e", motion_report["live_position"][3]
            )

        if "live_velocity" in motion_report:
            point.field("live_velocity", motion_report["live_velocity"])

            # Check if live_velocity is 0; track time spent standing still
            if (
                motion_report["live_velocity"] <= 1
            ):  # not 0, but slow enough that we don't care -- there's some variance w/ fp data
                last_zero_timestamp = current_time
                microseconds_since_last_zero = 0  # It's 0 since we just encountered it
                microseconds_since_last_zero = None  # Do not post a new zero motion, we're measuring the current one
            elif last_zero_timestamp:
                microseconds_since_last_zero = int(
                    (current_time - last_zero_timestamp) / timedelta(microseconds=1)
                )
                last_zero_timestamp = None  # stop counting, we're only interested in the first time we see movement after zero
            else:
                microseconds_since_last_zero = None  # No new zero motions

            if microseconds_since_last_zero is not None:
                point.field(
                    "microseconds_since_last_zero", microseconds_since_last_zero
                )

        if "live_extruder_velocity" in motion_report:
            point.field(
                "live_extruder_velocity", motion_report["live_extruder_velocity"]
            )

        point.time(datetime.utcnow(), WritePrecision.NS)

        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)


# Start the WebSocket connection, identify your client, and subscribe to the desired objects
asyncio.get_event_loop().run_until_complete(start_websocket_connection())
