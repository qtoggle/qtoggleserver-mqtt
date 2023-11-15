class MqttException(Exception):
    pass


class ClientNotConnected(MqttException):
    def __init__(self) -> None:
        super().__init__('MQTT client not connected')
