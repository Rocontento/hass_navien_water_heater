"""A simulated NaviLink cloud: fake REST endpoints and a fake AWS IoT broker."""
import json
import threading

from custom_components.navien_water_heater import navien_api as api

# --------------------------- fake REST ------------------------------------- #
USER_DATA = {
    "userInfo": {"userSeq": 4242},
    "token": {
        "accessToken": "tok",
        "accessKeyId": "AK",
        "secretKey": "SK",
        "sessionToken": "ST",
    },
}

def make_device(mac, name, home_seq):
    return {"deviceInfo": {"macAddress": mac, "deviceName": name, "homeSeq": home_seq,
                           "deviceType": 52, "additionalValue": "AV"}}

# Deliberately return the heaters in a *different order* on the second call,
# which is exactly what used to make the old index based lookup break.
DEVICE_PAGES = [
    [make_device("AA11", "Calentador Cocina", 11), make_device("BB22", "Calentador Bano", 22)],
    [make_device("BB22", "Calentador Bano", 22), make_device("AA11", "Calentador Cocina", 11)],
]

class FakeResponse:
    def __init__(self, data): self.status = 200; self._data = data
    async def json(self, content_type=None): return self._data

class FakeCM:
    def __init__(self, data): self._data = data
    async def __aenter__(self): return FakeResponse(self._data)
    async def __aexit__(self, *exc): return False

class FakeSession:
    def __init__(self): self.list_calls = 0
    def post(self, url, **kwargs):
        if url.endswith("/user/sign-in"):
            return FakeCM({"msg": "OK", "data": USER_DATA})
        page = DEVICE_PAGES[min(self.list_calls, len(DEVICE_PAGES) - 1)]
        self.list_calls += 1
        return FakeCM({"msg": "OK", "data": page})


# --------------------------- fake MQTT ------------------------------------- #
class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()

# mac -> (unit_type, temperature_type)
PROFILES = {"AA11": (11, 2), "BB22": (8, 1)}   # NPE2/Fahrenheit and NFC/Celsius

class FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.subs = {}
        self.published = []
        self.onOnline = None
        self.onOffline = None
        FakeClient.instances.append(self)

    def configureEndpoint(self, **kwargs): pass
    def configureUsernamePassword(self, **kwargs): pass
    def configureLastWill(self, **kwargs): pass
    def configureCredentials(self, path): pass
    def configureIAMCredentials(self, **kwargs): pass
    def configureConnectDisconnectTimeout(self, value): pass
    def configureMQTTOperationTimeout(self, value): pass
    def configureOfflinePublishQueueing(self, value): pass

    def connect(self):
        if self.onOnline:
            self.onOnline()
        return True

    def disconnect(self):
        return True

    def subscribe(self, topic, QoS, callback):
        self.subs[topic] = callback

    def publish(self, topic, payload, QoS):
        self.published.append((topic, payload))
        threading.Thread(target=self._respond, args=(payload,), daemon=True).start()

    def _respond(self, payload):
        request = json.loads(payload)
        command = request["request"]["command"]
        mac = request["request"]["macAddress"]
        response_topic = request["responseTopic"]
        unit_type, temp_type = PROFILES[mac]

        if command == api.CMD_CHANNEL_INFO:
            body = {"channelInfo": {"channelList": [{
                "channelNumber": 1,
                "channel": {
                    "temperatureType": temp_type,
                    "unitCount": 1,
                    "onDemandUse": 1,
                    "deviceSorting": unit_type,
                    "setupDHWTempMin": 90 if temp_type == 2 else 80,
                    "setupDHWTempMax": 140 if temp_type == 2 else 130,
                },
            }]}}
        else:
            body = {"channelStatus": {"channelNumber": 1, "channel": {
                "unitType": unit_type,
                "unitCount": 1,
                "powerStatus": 1,
                "onDemandUseFlag": 2,
                "avgCalorie": 84,
                "DHWSettingTemp": 120 if temp_type == 2 else 100,
                "avgOutletTemp": 118 if temp_type == 2 else 98,
                "avgInletTemp": 70 if temp_type == 2 else 40,
                "errorCode": 0,
                # Flags and fields the curated tables do not all cover, so the
                # dynamic discovery and the boolean handling get exercised.
                "wwsdFlag": 2,
                "freezeProtectionUse": 2,
                "unitInfo": {"unitStatusList": [{
                    "unitNumber": 1,
                    "currentOutletTemp": 118 if temp_type == 2 else 98,
                    "currentInletTemp": 70 if temp_type == 2 else 40,
                    "DHWFlowRate": 25,
                    "accumulatedGasUsage": 1234,
                    "gasInstantUsage": 100 if temp_type == 2 else 5,
                    "errorCode": 0,
                    "fanRPM": 3200,
                    "someNewValue": 7,
                }]},
            }}}

        message = FakeMessage(response_topic, json.dumps(
            {"sessionID": request["sessionID"], "response": body}))
        callback = self.subs.get(response_topic)
        if callback:
            callback(self, None, message)


api.mqtt.AWSIoTMQTTClient = FakeClient


