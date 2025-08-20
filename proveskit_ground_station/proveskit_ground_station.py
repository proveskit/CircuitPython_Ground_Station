import json
import time

import supervisor
from pysquared.cdh import CommandDataHandler
from pysquared.config.config import Config
from pysquared.hardware.radio.packetizer.packet_manager import PacketManager
from pysquared.logger import Logger

from .binary_encoder import BinaryDecoder, BinaryEncoder


class GroundStation:
    def __init__(
        self,
        logger: Logger,
        config: Config,
        packet_manager: PacketManager,
        cdh: CommandDataHandler,
        use_binary_encoding: bool = True,
    ):
        self._log = logger
        self._log.colorized = True
        self._config = config
        self._packet_manager = packet_manager
        self._cdh = cdh
        self._use_binary_encoding = use_binary_encoding
        self._key_map = {}

    def listen(self):
        try:
            while True:
                if supervisor.runtime.serial_bytes_available:
                    typed = input().strip()
                    if typed:
                        self.handle_input(typed)

                b = self._packet_manager.listen(1)
                if b is not None:
                    decoded_response = self._decode_response(b)
                    self._log.info(
                        message="Received response", response=decoded_response
                    )

        except KeyboardInterrupt:
            self._log.debug("Keyboard interrupt received, exiting listen mode.")

    def send_receive(self):
        try:
            encoding_mode = "Binary" if self._use_binary_encoding else "JSON"
            cmd_selection = input(
                f"""
            ===============================
            | Select command to send      |
            | 1: Reset                    |
            | 2: Change radio modulation  |
            | 3: Send joke                |
            | 4: Toggle encoding mode     |
            | Current: {encoding_mode}            |
            ===============================
            """
            )

            self.handle_input(cmd_selection)

        except KeyboardInterrupt:
            self._log.debug("Keyboard interrupt received, exiting send mode.")

    def handle_input(self, cmd_selection):
        if cmd_selection == "4":
            self._use_binary_encoding = not self._use_binary_encoding
            encoding_mode = "Binary" if self._use_binary_encoding else "JSON"
            self._log.info(f"Encoding mode switched to: {encoding_mode}")
            return
        
        if cmd_selection not in ["1", "2", "3"]:
            self._log.warning("Invalid command selection. Please try again.")
            return

        message: dict[str, object] = {
            "name": self._config.cubesat_name,
            "password": self._config.super_secret_code,
        }

        if cmd_selection == "1":
            message["command"] = self._cdh.command_reset
        elif cmd_selection == "2":
            message["command"] = self._cdh.command_change_radio_modulation
            modulation = input("Enter new radio modulation [FSK | LoRa]: ")
            message["args"] = [modulation]
        elif cmd_selection == "3":
            message["command"] = self._cdh.command_send_joke

        while True:
            # Turn on the radio so that it captures any received packets to buffer
            self._packet_manager.listen(1)

            # Send the message
            self._log.info(
                "Sending command",
                cmd=message["command"],
                args=message.get("args", []),
            )
            encoded_message = self._encode_message(message)
            self._packet_manager.send(encoded_message)

            # Listen for ACK response
            b = self._packet_manager.listen(1)
            if b is None:
                self._log.info("No response received, retrying...")
                continue

            if b != b"ACK":
                decoded_response = self._decode_response(b)
                self._log.info(
                    "No ACK response received, retrying...",
                    response=decoded_response,
                )
                continue

            self._log.info("Received ACK")

            # Now listen for the actual response
            b = self._packet_manager.listen(1)
            if b is None:
                self._log.info("No response received, retrying...")
                continue

            decoded_response = self._decode_response(b)
            self._log.info("Received response", response=decoded_response)
            break

    def run(self):
        while True:
            print(
                """
            =============================
            |                           |
            | WELCOME!                  |
            | PROVESKIT Ground Station  |
            |                           |
            =============================
            | Please Select Your Mode   |
            | 'A': Listen               |
            | 'B': Send                 |
            =============================
            """
            )

            device_selection = input().lower()

            if device_selection not in ["a", "b"]:
                self._log.warning("Invalid Selection. Please try again.")
                continue

            if device_selection == "a":
                self.listen()
            elif device_selection == "b":
                self.send_receive()

            time.sleep(1)

    def _encode_message(self, message: dict) -> bytes:
        """Encode a message using either binary or JSON format.
        
        Args:
            message: Dictionary containing the message data
            
        Returns:
            Encoded message bytes
        """
        if self._use_binary_encoding:
            encoder = BinaryEncoder()
            
            for key, value in message.items():
                if isinstance(value, str):
                    encoder.add_string(key, value)
                elif isinstance(value, int):
                    # Automatically select optimal integer size
                    if -128 <= value <= 127:
                        encoder.add_int(key, value, size=1)
                    elif -32768 <= value <= 32767:
                        encoder.add_int(key, value, size=2)
                    else:
                        encoder.add_int(key, value, size=4)
                elif isinstance(value, float):
                    encoder.add_float(key, value)
                elif isinstance(value, list):
                    # Handle command arguments as individual indexed fields
                    for i, arg in enumerate(value):
                        arg_key = f"{key}_{i}"
                        if isinstance(arg, str):
                            encoder.add_string(arg_key, arg)
                        elif isinstance(arg, int):
                            encoder.add_int(arg_key, arg)
                        elif isinstance(arg, float):
                            encoder.add_float(arg_key, arg)
                        else:
                            encoder.add_string(arg_key, str(arg))
                else:
                    # Convert other types to string
                    encoder.add_string(key, str(value))
            
            # Store key map for decoding responses
            self._key_map.update(encoder.get_key_map())
            
            encoded_data = encoder.to_bytes()
            self._log.debug(
                "Binary encoding",
                original_size=len(json.dumps(message, separators=(",", ":"))),
                binary_size=len(encoded_data),
                compression_ratio=len(json.dumps(message, separators=(",", ":"))) / len(encoded_data) if encoded_data else 1
            )
            return encoded_data
        else:
            # Fallback to JSON encoding
            return json.dumps(message).encode("utf-8")

    def _decode_response(self, data: bytes) -> str:
        """Decode a response using either binary or JSON format.
        
        Args:
            data: Raw response bytes
            
        Returns:
            Decoded response as string for logging
        """
        if self._use_binary_encoding:
            try:
                # First try to decode as binary
                decoder = BinaryDecoder(data, self._key_map)
                decoded_data = decoder.get_all()
                
                if decoded_data:
                    # Successfully decoded binary data
                    return json.dumps(decoded_data, separators=(",", ":"))
                else:
                    # Empty binary data, might be plain text (like ACK)
                    return data.decode("utf-8", errors="replace")
            except Exception as e:
                # If binary decoding fails, try as plain text
                self._log.debug(f"Binary decode failed: {e}, falling back to text")
                return data.decode("utf-8", errors="replace")
        else:
            # Standard text decoding
            return data.decode("utf-8", errors="replace")

    def get_encoding_stats(self, message: dict) -> dict:
        """Get statistics comparing binary vs JSON encoding for a message.
        
        Args:
            message: The message to analyze
            
        Returns:
            Dictionary with encoding statistics
        """
        # JSON size
        json_data = json.dumps(message, separators=(",", ":")).encode("utf-8")
        json_size = len(json_data)
        
        # Binary size
        encoder = BinaryEncoder()
        for key, value in message.items():
            if isinstance(value, str):
                encoder.add_string(key, value)
            elif isinstance(value, int):
                if -128 <= value <= 127:
                    encoder.add_int(key, value, size=1)
                elif -32768 <= value <= 32767:
                    encoder.add_int(key, value, size=2)
                else:
                    encoder.add_int(key, value, size=4)
            elif isinstance(value, float):
                encoder.add_float(key, value)
            elif isinstance(value, list):
                for i, arg in enumerate(value):
                    arg_key = f"{key}_{i}"
                    if isinstance(arg, str):
                        encoder.add_string(arg_key, arg)
                    elif isinstance(arg, int):
                        encoder.add_int(arg_key, arg)
                    elif isinstance(arg, float):
                        encoder.add_float(arg_key, arg)
                    else:
                        encoder.add_string(arg_key, str(arg))
            else:
                encoder.add_string(key, str(value))
        
        binary_data = encoder.to_bytes()
        binary_size = len(binary_data)
        
        return {
            "json_size": json_size,
            "binary_size": binary_size,
            "compression_ratio": json_size / binary_size if binary_size > 0 else 1,
            "bytes_saved": json_size - binary_size,
            "size_reduction_percent": ((json_size - binary_size) / json_size * 100) if json_size > 0 else 0
        }
