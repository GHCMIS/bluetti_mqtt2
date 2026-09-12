import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from bluetti_mqtt.bluetooth import build_device
from bluetti_mqtt.bluetooth.client import BluetoothClient, ClientState
from bluetti_mqtt.bluetooth.exc import BadConnectionError
from bluetti_mqtt.core import ReadHoldingRegisters
from bluetti_mqtt.device_handler import DeviceHandler
from bluetti_mqtt.server_cli import CommandLineHandler
from bluetti_mqtt import logger_cli


ADDRESS = '00:11:22:33:44:55'


def ranges(commands):
    return [(cmd.starting_address, cmd.quantity) for cmd in commands]


class PackConfigurationTests(unittest.TestCase):
    def test_default_preserves_expansion_packs(self):
        device = build_device(ADDRESS, 'AC200L12345')
        self.assertEqual(device.pack_num_max, 3)
        self.assertEqual(ranges(device.pack_polling_commands), [(91, 37)])
        self.assertEqual(ranges(device.pack_logging_commands), [(91, 119)])
        self.assertNotIn((91, 31), ranges(device.polling_commands))

    def test_standalone_reads_internal_battery_in_main_loop(self):
        device = build_device(ADDRESS, 'AC200L12345', False)
        self.assertEqual(device.pack_num_max, 1)
        self.assertEqual(device.pack_polling_commands, [])
        self.assertEqual(device.pack_logging_commands, [])
        self.assertIn((91, 31), ranges(device.polling_commands))
        self.assertIn((91, 31), ranges(device.logging_commands))
        parsed = device.parse(91, bytes(62))
        self.assertIn('pack_battery_percent', parsed)
        self.assertEqual(len(parsed['cell_voltages']), 16)

    def test_other_models_are_unaffected(self):
        default = build_device(ADDRESS, 'AC30012345')
        standalone = build_device(ADDRESS, 'AC30012345', False)
        self.assertEqual(default.pack_num_max, standalone.pack_num_max)
        self.assertEqual(ranges(default.pack_logging_commands),
                         ranges(standalone.pack_logging_commands))

    def test_mqtt_cli_accepts_standalone_flag(self):
        for flags, expected in [([], False), (['--ac200l-standalone'], True)]:
            with patch('sys.argv', ['bluetti-mqtt', '--broker', 'localhost', *flags, ADDRESS]), \
                    patch.object(CommandLineHandler, 'start') as start:
                CommandLineHandler().execute()
                self.assertEqual(start.call_args[0][0].ac200l_standalone, expected)

    def test_logger_cli_forwards_standalone_flag(self):
        with patch('sys.argv', ['bluetti-logger', '--ac200l-standalone',
                                '--log', '/unused', ADDRESS]), \
                patch.object(logger_cli, 'log', new_callable=AsyncMock) as log:
            logger_cli.main()
            log.assert_awaited_once_with(ADDRESS, '/unused', ac200l_expansion_packs=False)


class AsyncConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_uses_setting_and_stops_pack_loop(self):
        with patch('bluetti_mqtt.device_handler.MultiDeviceManager') as manager:
            manager.return_value.get_name.return_value = 'AC200L12345'
            manager.return_value.is_ready.return_value = True
            handler = DeviceHandler([ADDRESS], 30, None, ac200l_expansion_packs=False)
            self.assertEqual(handler._get_device(ADDRESS).pack_num_max, 1)
            await asyncio.wait_for(handler._pack_poll(ADDRESS), timeout=1)
            manager.return_value.perform_nowait.assert_not_called()
            manager.return_value.perform.assert_not_called()

    async def test_write_eof_becomes_recoverable_connection_error(self):
        with patch('bluetti_mqtt.bluetooth.client.BleakClient') as backend:
            backend.return_value.write_gatt_char = AsyncMock(side_effect=EOFError())
            client = BluetoothClient(ADDRESS)
            future = await client.perform(ReadHoldingRegisters(91, 31))
            await client._perform_command()
            with self.assertRaises(BadConnectionError):
                await future
            self.assertEqual(client.state, ClientState.DISCONNECTING)


if __name__ == '__main__':
    unittest.main()
