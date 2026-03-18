import time
import json
from dataclasses import dataclass, asdict

from PyQt6 import uic
from PyQt6.QtWidgets import QDialog, QLayout

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
    running: bool = False
    settings_dialog: QDialog | None = None


STATE = State()


def serialise_settings(settings: Settings) -> bytes:
    return json.dumps(asdict(settings)).encode()


def deserialise_settings(settings: bytes) -> Settings:
    return Settings(**json.loads(settings.decode()))


def save_current_settings() -> None:
    assert STATE.settings is not None
    syl.save_settings(serialise_settings(STATE.settings))


def close_settings_dialog() -> None:
    dialog = STATE.settings_dialog
    if dialog is not None:
        dialog.close()


def fit_dialog_to_contents(dialog: QDialog) -> None:
    layout = dialog.layout()
    if layout is not None:
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    dialog.adjustSize()


def submit_info_pulse(value: int, ts_us: int):
    block = syl.IntSignalBlock()
    block.timestamps = [ts_us]
    block.data = [[value]]
    out.submit(block)


# # ####################################################################################
# # Syntalos interface
# # ####################################################################################


def prepare() -> bool:
    """This function is called before a run is started.
    You can use it for (slow) initializations."""
    close_settings_dialog()
    if STATE.settings is None:
        syl.println("Settings not set, aborting prepare()")
        return False

    # NB setting up the pins of the Firmata device here does not work bc
    # the firmata module is not yet ready
    return True


def start():
    """This function is called immediately when a run is started.
    This function should complete extremely quickly."""
    # Don't do anything here, let run() do the work, we have plenty of time there
    pass


def run():
    """This function is called once the experiment run has started."""
    if STATE.settings is None:
        syl.println("Settings not set, aborting run()")
        return

    STATE.running = True
    try:
        is_output = True
        ctl.firmata_register_digital_pin(STATE.settings.pin_start, "START_PULSE_PIN", is_output)
        ctl.firmata_register_digital_pin(STATE.settings.pin_stop, "STOP_PULSE_PIN", is_output)

        # Ensure in the beggining the LEDs are not blinking
        ctl.firmata_submit_digital_pulse("STOP_PULSE_PIN", STATE.settings.pulse_duration_msec)

        t0 = time.time()
        started = False

        value = 0

        # wait for new data to arrive and communicate with Syntalos
        while syl.is_running():
            syl.wait(100)  # ms

            if not started and (time.time() - t0 > STATE.settings.start_delay_sec):
                started = True
                ts_us = syl.time_since_start_usec()
                submit_info_pulse(value, ts_us - 1)
                value = 1
                submit_info_pulse(value, ts_us)

                ctl.firmata_submit_digital_pulse(
                    "START_PULSE_PIN", STATE.settings.pulse_duration_msec
                )
            else:
                ts_us = syl.time_since_start_usec()
                submit_info_pulse(value, ts_us)
    except Exception as exc:
        msg = f"Run failed: {exc.__class__.__name__}({exc})"
        syl.println(msg)
    STATE.running = False


def stop():
    """This function is called once a run is stopped."""
    if STATE.settings is None:
        syl.println("Settings not set, aborting stop()")
        return


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
    if STATE.running or syl.is_running():
        syl.println("Cannot show settings while running")
        return

    if not settings:
        if STATE.settings is None:
            STATE.settings = Settings()
    else:
        STATE.settings = deserialise_settings(settings)

    dialog = STATE.settings_dialog
    if dialog is not None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return

    assert STATE.settings is not None

    dialog = uic.loadUi(UI_FILE_PATH)
    STATE.settings_dialog = dialog
    fit_dialog_to_contents(dialog)

    dialog.startPinSpinBox.setValue(STATE.settings.pin_start)
    dialog.stopPinSpinBox.setValue(STATE.settings.pin_stop)
    dialog.pulseDurationSpinBox.setValue(STATE.settings.pulse_duration_msec)
    dialog.startDelaySpinBox.setValue(STATE.settings.start_delay_sec)

    def persist_settings():
        assert STATE.settings is not None
        STATE.settings.pin_start = dialog.startPinSpinBox.value()
        STATE.settings.pin_stop = dialog.stopPinSpinBox.value()
        STATE.settings.pulse_duration_msec = dialog.pulseDurationSpinBox.value()
        STATE.settings.start_delay_sec = dialog.startDelaySpinBox.value()
        save_current_settings()

    def cleanup_dialog():
        STATE.settings_dialog = None

    dialog.startPinSpinBox.valueChanged.connect(persist_settings)
    dialog.stopPinSpinBox.valueChanged.connect(persist_settings)
    dialog.pulseDurationSpinBox.valueChanged.connect(persist_settings)
    dialog.startDelaySpinBox.valueChanged.connect(persist_settings)
    dialog.finished.connect(cleanup_dialog)

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


# Register the settings callback
syl.call_on_show_settings(show_settings)
