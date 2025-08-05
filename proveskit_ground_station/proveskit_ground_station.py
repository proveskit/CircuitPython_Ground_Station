import json
import time

import supervisor
from pysquared.cdh import CommandDataHandler
from pysquared.config.config import Config
from pysquared.hardware.radio.packetizer.packet_manager import PacketManager
from pysquared.logger import Logger

UPDATE_LEADERBOARD = "update_leaderboard"
SEND_LEADERBOARD_MAIN = "send_leaderboard_main"
RETURN_LEADERBOARD_MAIN = "return_leaderboard_main"

class GroundStation:
    def __init__(
        self,
        logger: Logger,
        config: Config,
        packet_manager: PacketManager,
        cdh: CommandDataHandler,
    ):
        self._log = logger
        self._log.colorized = True
        self._config = config
        self._packet_manager = packet_manager
        self._cdh = cdh

    def listen(self):
        try:
            while True:
                if supervisor.runtime.serial_bytes_available:
                    typed = input().strip()
                    if typed:
                        self.handle_input(typed)

                b = self._packet_manager.listen(1)
                if b is not None:
                    self._log.info(
                        message="Received response", response=b.decode("utf-8")
                    )

        except KeyboardInterrupt:
            self._log.debug("Keyboard interrupt received, exiting listen mode.")

    def send_receive(self):
        try:
            cmd_selection = input(
                """
            ===============================
            | Select command to send      |
            | 1: Reset                    |
            | 2: Change radio modulation  |
            | 3: Send joke                |
            | 4: Ask for leaderboard      |
            | 5: Update leaderboard       |
            ===============================
            """
            )

            self.handle_input(cmd_selection)

        except KeyboardInterrupt:
            self._log.debug("Keyboard interrupt received, exiting send mode.")

    def handle_input(self, cmd_selection):
        if cmd_selection not in ["1", "2", "3", "4", "5"]:
            self._log.warning("Invalid command selection. Please try again.")
            return

        if cmd_selection == "4":
            self.ask_for_leaderboard()
            return
        elif cmd_selection == "5":
            name = input("Enter your name for the leaderboard: ")
            self.ask_to_update(name)
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
            self._packet_manager.send(json.dumps(message).encode("utf-8"))

            # Listen for ACK response
            b = self._packet_manager.listen(1)
            if b is None:
                self._log.info("No response received, retrying...")
                continue

            if b != b"ACK":
                self._log.info(
                    "No ACK response received, retrying...",
                    response=b.decode("utf-8"),
                )
                continue

            self._log.info("Received ACK")

            # Now listen for the actual response
            b = self._packet_manager.listen(1)
            if b is None:
                self._log.info("No response received, retrying...")
                continue

            self._log.info("Received response", response=b.decode("utf-8"))
            break

    
    def ask_for_leaderboard(main_callsign=None):
        if main_callsign is None:
            main_callsign = config.radio.main_sat_license
        self.logger.info(f"Requesting leaderboard from {main_callsign}...")
        message = {
            "current_time": time.monotonic(),
            "callsign": main_callsign,
            "command": SEND_LEADERBOARD_MAIN,
        }
        encoded_message = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if not packet_manager.send(encoded_message):
            self.logger.warning(f"Failed to send leaderboard request to {main_callsign}")
            return
        self.logger.info(f"Listening for response from {main_callsign} for 30 seconds.")
        command = ""
        start_time = time.monotonic()
        while command != RETURN_LEADERBOARD_MAIN and time.monotonic() < start_time + 30:
            self.logger.info(f"Listening... {time.monotonic()}")
            received_message = packet_manager.listen(1)
            if not received_message:
                continue
            try:
                decoded_message = json.loads(received_message.decode("utf-8"))
            except Exception as e:
                self.logger.warning(f"Failed to decode message: {e}")
                continue
            command = decoded_message.get("command")
            self.logger.info(f"Received: {received_message}")
            self.logger.info(f"Command: {command}")
        if command == RETURN_LEADERBOARD_MAIN:
            payload = decoded_message.get("leaderboard", {})
            print("LEADERBOARD:")
            if payload:
                sorted_leaderboard = sorted(payload.items(), key=lambda kv: (-kv[1], kv[0]))
                for i, (name, score) in enumerate(sorted_leaderboard, 1):
                    print(f"{i}. {name}: {score}")
            else:
                print("Leaderboard is empty.")
        else:
            self.logger.warning("Did not receive leaderboard response in time.")


    def ask_to_update(name):
        message = {
            "current_time": time.monotonic(),
            "command": UPDATE_LEADERBOARD,
            "callsign": "any",
            "name": name,
        }
        encoded_message = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if not packet_manager.send(encoded_message):
            self.logger.warning("Failed to send leaderboard request")
        else:  # TODO have cubes say who sent them :)
            self.logger.info("name sent! out in the world")

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

  