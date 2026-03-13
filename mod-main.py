import time
import json
from dataclasses import dataclass, asdict

import syntalos_mlink as syl

ctl = syl.get_output_port("firmatactl")

out = syl.get_output_port("start_pulse")
out.set_metadata_value("signal_names", ["START_PULSE"])
out.set_metadata_value("time_unit", "microseconds")
out.set_metadata_value("data_unit", ["a.u."])

# Path to the UI file (same directory as this script)
UI_FILE_PATH = "settings.ui"


@dataclass
class Settings:
    pin_start: int = 3
    pin_stop: int = 7
    # Does not matter, Arduino is programmed to react to the rising edge
    pulse_duration_msec: int = 1
    # Wait a few seconds so that all video feeds and device signals are stable
    start_delay_sec: float = 10.0


@dataclass
class State:
    settings: Settings | None = None


STATE = State()


def serialise_settings(settings: Settings) -> bytes:
    return json.dumps(asdict(settings)).encode()


def deserialise_settings(settings: bytes) -> Settings:
    return Settings(**json.loads(settings.decode()))


# # ####################################################################################
# # Syntalos interface
# # ####################################################################################


def prepare() -> bool:
    """This function is called before a run is started.
    You can use it for (slow) initializations."""
    if STATE.settings is None:
        syl.println("Settings not set, aborting prepare()")
        return False

    # NB setting up the pins of the Firmata device here does not work bc
    # the firmata module is not yet ready
    return True


def start():
    """This function is called immediately when a run is started.
    This function should complete extremely quickly."""
    assert STATE.settings is not None

    is_output = True
    ctl.firmata_register_digital_pin(STATE.settings.pin_start, "START_PULSE_PIN", is_output)
    ctl.firmata_register_digital_pin(STATE.settings.pin_stop, "STOP_PULSE_PIN", is_output)

    # Ensure in the beggining the LEDs are not blinking
    ctl.firmata_submit_digital_pulse("STOP_PULSE_PIN", STATE.settings.pulse_duration_msec)


def run():
    """This function is called once the experiment run has started."""
    assert STATE.settings is not None

    t0 = time.time()
    started = False
    # wait for new data to arrive and communicate with Syntalos
    while syl.is_running():
        syl.wait(1)  # ms

        if not started and (time.time() - t0 > STATE.settings.start_delay_sec):
            ctl.firmata_submit_digital_pulse("START_PULSE_PIN", STATE.settings.pulse_duration_msec)
            started = True


def stop():
    """This function is called once a run is stopped."""
    assert STATE.settings is not None
    ctl.firmata_submit_digital_pulse("STOP_PULSE_PIN", STATE.settings.pulse_duration_msec)


def set_settings(settings: bytes):
    if settings:
        try:
            STATE.settings = deserialise_settings(settings)
        except Exception as exc:
            msg = f"Failed to parse settings: {exc.__class__.__name__}({exc})"
            syl.println(msg)
            syl.raise_error(msg)
            STATE.settings = Settings()
    elif STATE.settings is None:
        STATE.settings = Settings()


# # ####################################################################################
# # Settings UI
# # ####################################################################################


def show_settings(settings: bytes):
    # Showing the settings UI while running prevents the run() loop from advancing.
    # Keep it simple: no settings UI while running.
    if syl.is_running():
        syl.println("Cannot show settings while running")
        return

    STATE.settings = deserialise_settings(settings)


# Register the settings callback
syl.call_on_show_settings(show_settings)
