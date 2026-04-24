import json
from pathlib import Path
import sys
import time
from dataclasses import asdict, dataclass

import numpy as np

import syntalos_mlink as syl
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QDialog, QLayout


@dataclass
class Settings:
    pin_start: int = 3
    pin_stop: int = 7
    # Does not matter, Arduino is programmed to react to the rising edge.
    pulse_duration_msec: int = 1
    # Wait a few seconds so that all video feeds and device signals are stable.
    start_delay_sec: float = 10.0


@dataclass
class State:
    settings: Settings | None = None
    running: bool = False
    settings_dialog: QDialog | None = None
    t0_ns: int | None = None
    pins_initialized: bool = False
    pulse_sent: bool = False


def clear_state() -> None:
    STATE.running = False
    STATE.t0_ns = None
    STATE.pins_initialized = False
    STATE.pulse_sent = False


STATE = State()
App: QApplication | None = None
MLink: syl.SyntalosLink | None = None
ctl: syl.OutputPort | None = None
out: syl.OutputPort | None = None

UI_FILE_PATH = Path(__file__).resolve().with_name("settings.ui")


def serialise_settings(settings: Settings) -> bytes:
    return json.dumps(asdict(settings)).encode()


def deserialise_settings(settings: bytes) -> Settings:
    return Settings(**json.loads(settings.decode()))  # pyright: ignore[reportAny]


def close_settings_dialog() -> None:
    dialog = STATE.settings_dialog
    if dialog is not None:
        _ = dialog.close()


def fit_dialog_to_contents(dialog: QDialog) -> None:
    layout = dialog.layout()
    if layout is not None:
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    dialog.adjustSize()


def submit_info_pulse(value: int, ts_ns_py: int, ts_us_syl: int) -> None:
    assert out is not None
    block = syl.IntSignalBlock()
    block.timestamps = np.array([ts_us_syl])
    block.data = np.array([[value, ts_ns_py // 1000]])
    out.submit(block)


def get_timestamps() -> tuple[int, int]:
    ts_ns_py = time.perf_counter_ns()
    ts_us_syl = int(syl.time_since_start_usec())
    return ts_ns_py, ts_us_syl


# # ####################################################################################
# # Syntalos interface
# # ####################################################################################


def register_ports(mlink: syl.SyntalosLink) -> None:
    global ctl, out

    out = mlink.register_output_port("start_pulse", "Start pulse", syl.DataType.IntSignalBlock)
    ctl = mlink.register_output_port("firmatactl", "Firmata control", syl.DataType.FirmataControl)


def prepare() -> bool:
    clear_state()
    close_settings_dialog()
    if STATE.settings is None:
        print("Settings not set, aborting prepare()")
        return False

    assert out is not None
    out.set_metadata_value("signal_names", ["START_PULSE", "TIMESTAMP_PY"])
    out.set_metadata_value("time_unit", "microseconds")
    out.set_metadata_value("data_unit", ["a.u.", "microseconds"])

    # Setting up Firmata pins here does not work because the Firmata module is not ready yet.
    return True


def start() -> None:
    STATE.running = True
    STATE.t0_ns = time.perf_counter_ns()


def event_loop_tick() -> None:
    if App is not None:
        App.processEvents()

    if not STATE.running:
        return

    assert ctl is not None
    assert STATE.settings is not None

    try:
        if not STATE.pins_initialized:
            is_output = True
            _ = ctl.firmata_register_digital_pin(
                STATE.settings.pin_start, "START_PULSE_PIN", is_output
            )
            _ = ctl.firmata_register_digital_pin(
                STATE.settings.pin_stop, "STOP_PULSE_PIN", is_output
            )

            # Ensure in the beginning the LEDs are not blinking.
            _ = ctl.firmata_submit_digital_pulse(
                "STOP_PULSE_PIN", STATE.settings.pulse_duration_msec
            )
            STATE.pins_initialized = True

        if STATE.pulse_sent:
            return

        assert STATE.t0_ns is not None, "t0_ns not set"
        delay_ns = STATE.settings.start_delay_sec * 1e9
        if time.perf_counter_ns() - STATE.t0_ns <= delay_ns:
            return

        STATE.pulse_sent = True

        # syl.time_since_start_usec() takes a bit longer to execute (on the order of ~1 ms).
        # Subsequent calls are faster. Do one dummy call first.
        _ = get_timestamps()
        ts_ns_py, ts_us_syl = get_timestamps()
        # Submit the START pulse asap after querying the timestamp.
        _ = ctl.firmata_submit_digital_pulse("START_PULSE_PIN", STATE.settings.pulse_duration_msec)

        # We might be able to use this as our global reference.
        submit_info_pulse(1, ts_ns_py=ts_ns_py, ts_us_syl=ts_us_syl)
    except Exception:
        STATE.running = False
        raise


def stop() -> None:
    STATE.running = False


def load_settings(settings: bytes, _base_dir: Path) -> bool:
    if not settings:
        if STATE.settings is None:
            STATE.settings = Settings()
        return True

    try:
        STATE.settings = deserialise_settings(settings)
    except Exception:
        STATE.settings = Settings()
        raise

    return True


def save_settings(_base_dir: Path) -> bytes:
    if STATE.settings is None:
        STATE.settings = Settings()
    settings = serialise_settings(STATE.settings)
    return settings


# # ####################################################################################
# # Settings UI
# # ####################################################################################


def show_settings() -> None:
    # Showing the settings UI while running prevents the module event loop from advancing.
    # Keep it simple: no settings UI while running.
    if STATE.running or (MLink is not None and MLink.is_running):
        print("Cannot show settings while running")
        return

    if STATE.settings is None:
        STATE.settings = Settings()

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


def main() -> int:
    global App, MLink

    App = QApplication.instance()
    if App is None:
        App = QApplication(sys.argv)
    App.setQuitOnLastWindowClosed(False)

    MLink = syl.init_link(rename_process=True)
    register_ports(MLink)

    MLink.on_prepare = prepare
    MLink.on_start = start
    MLink.on_stop = stop
    MLink.on_show_settings = show_settings
    MLink.on_save_settings = save_settings
    MLink.on_load_settings = load_settings

    MLink.await_data_forever(event_loop_tick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
