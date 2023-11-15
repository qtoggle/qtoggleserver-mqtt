import asyncio
import logging
import ssl

from typing import Optional

import aiomqtt

from jinja2 import Template

from qtoggleserver.core import events as core_events
from qtoggleserver.core.device import attrs as core_device_attrs
from qtoggleserver.lib.templatenotifications import TemplateNotificationsHandler
from qtoggleserver.utils import json as json_utils

from . import logger
from .exceptions import ClientNotConnected


class MqttEventHandler(TemplateNotificationsHandler):
    DEFAULT_PORT = 1883
    DEFAULT_RECONNECT_INTERVAL = 5  # seconds
    DEFAULT_TOPIC = '{{device_attrs.name}}'
    DEFAULT_QOS = 0

    DEFAULT_TEMPLATES = {
        'value-change': None,
        'port-update': None,
        'port-add': None,
        'port-remove': None,
        'device-update': None,
        'full-update': None,
        'slave-device-update': None,
        'slave-device-add': None,
        'slave-device-remove': None,
    }

    logger = logger

    def __init__(
        self,
        *,
        server: str,
        port: int = DEFAULT_PORT,
        use_tls: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        reconnect_interval: int = DEFAULT_RECONNECT_INTERVAL,
        topic: str = DEFAULT_TOPIC,
        json_context_fields: Optional[list[str]] = None,
        qos: int = DEFAULT_QOS,
        client_logging: bool = False,
        **kwargs,
    ) -> None:
        self.server: str = server
        self.port: int = port
        self.use_tls: bool = use_tls
        self.username: Optional[str] = username
        self.password: Optional[str] = password
        self.client_id: Optional[str] = client_id
        self.reconnect_interval: int = reconnect_interval
        self.topic: str = topic
        self.json_context_fields: Optional[set[str]] = set(json_context_fields) if json_context_fields else None
        self.qos: int = qos
        self.client_logging: bool = client_logging

        self._mqtt_client: Optional[aiomqtt.Client] = None
        self._client_task: Optional[asyncio.Task] = None

        super().__init__(**kwargs)

        self._topic_template: Template = self.make_template(self.topic)

        self.client_logger: logging.Logger = self.logger.getChild('client')
        if not self.client_logging:
            self.client_logger.setLevel(logging.CRITICAL)

        self._start_client_task()

    async def _client_loop(self) -> None:
        while True:
            try:
                if self.use_tls:
                    tls_context = ssl.create_default_context()
                else:
                    tls_context = None
                client_id = self.client_id or core_device_attrs.attr_get_name()
                async with aiomqtt.Client(
                    hostname=self.server,
                    port=self.port,
                    tls_context=tls_context,
                    username=self.username,
                    password=self.password,
                    client_id=client_id,
                    logger=self.client_logger,
                ) as client:
                    self._mqtt_client = client
                    async with client.messages() as messages:
                        async for _ in messages:
                            # We don't really expect any message since we don't subscribe to any topic
                            await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.debug('client task cancelled')
                self._mqtt_client = None
                break
            except Exception:
                self.error('MQTT client error; reconnecting in %s seconds', self.reconnect_interval, exc_info=True)
                self._mqtt_client = None
                await asyncio.sleep(self.reconnect_interval)

    def _start_client_task(self) -> None:
        self._client_task = asyncio.create_task(self._client_loop())

    async def _stop_client_task(self) -> None:
        self._client_task.cancel()
        await self._client_task
        self._client_task = None

    async def cleanup(self) -> None:
        if self._client_task:
            await self._stop_client_task()

    async def push_template_message(self, event: core_events.Event, context: dict) -> None:
        if not self._mqtt_client:
            raise ClientNotConnected()

        payload = await self.render(event.get_type(), context)
        if not payload:
            # If no template is specified, dump the context as JSON
            payload_context = self._prepare_payload_context(context)
            payload = json_utils.dumps(payload_context, extra_types=json_utils.EXTRA_TYPES_ISO)

        topic = await self._topic_template.render_async(context)
        await self._mqtt_client.publish(topic, payload, self.qos)

        self.debug('message published')

    def _prepare_payload_context(self, context: dict) -> dict:
        context = dict(context)

        if self.json_context_fields is not None:
            for name in list(context):
                if name not in self.json_context_fields:
                    context.pop(name)

        # Remove sensitive data
        attrs_dicts: list[dict] = []
        if 'device_attrs' in context:
            attrs_dicts.append(context['device_attrs'])
        for slave_attrs in context.get('slave_attrs', {}).values():
            attrs_dicts.append(slave_attrs)
        for attrs_dict in attrs_dicts:
            for attr in list(attrs_dict):
                if attr.count('password') or attr == 'wifi_key':
                    attrs_dict.pop(attr, None)

        # Remove references to objects
        context.pop('event', None)
        context.pop('port', None)
        context.pop('device', None)
        context.pop('slave', None)

        return context
