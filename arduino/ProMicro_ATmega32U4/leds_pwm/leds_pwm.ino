// Coded for SparkFun Pro Micro 5V, ATmega32U4

#include <Bounce2.h>
#include <avr/io.h>

const uint8_t BUTTON_PIN = 2;
Bounce2::Button button; // debouncer
volatile bool blinking = true;

static inline void pwm_enable(bool on) {
  if (on) {
    // Connect OC3A to pin (non-inverting PWM)
    TCCR3A |= _BV(COM3A1); // COM3A0 stays 0

    // Start Timer3 clock: prescaler /256 (CS32=1)
    TCCR3B = (TCCR3B & ~(_BV(CS32) | _BV(CS31) | _BV(CS30))) | _BV(CS32);
  } else {
    // Stop Timer3 clock
    TCCR3B &= ~(_BV(CS32) | _BV(CS31) | _BV(CS30));

    // Disconnect OC3A from pin
    TCCR3A &= ~(_BV(COM3A1) | _BV(COM3A0));

    // Force pin low while disabled
    PORTC &= ~_BV(6); // PC6 low
  }
}

void setup() {
  // Disable the built-in TX/RX red LEDs present on the Pro Micro board,
  // we don't need them.
  RXLED0;
  TXLED0;

  // For a 4-pin tactile switch button.
  // Button with internal pull-up: pressed = LOW
  button.attach(BUTTON_PIN, INPUT_PULLUP);
  button.interval(15); // 10–20 ms is typical
  button.setPressedState(LOW);

  // PWM for precise LED blinking

  // D5 is PC6 = OC3A on Pro Micro
  DDRC |= _BV(6); // PC6 output

  // Stop Timer3 while configuring
  TCCR3A = 0;
  TCCR3B = 0;
  TCNT3 = 0;

  // Fast PWM, TOP = ICR3 (WGM = 14)
  // Prescaler /256 => tick = 16 MHz / 256 = 62500 Hz (16 µs)
  // Period 1.000 s => TOP+1 = 62500 => ICR3 = 62499
  ICR3 = 62499; // 1.000 s
  // High time 0.240 s => 0.240 * 62500 = 15000 ticks
  // In Fast PWM non-inverting, HIGH ticks = OCR3A + 1 => OCR3A = 14999
  OCR3A = 14999; // 0.240 s high

  // Set WGM bits for mode 14
  TCCR3A |= _BV(WGM31);
  TCCR3B |= _BV(WGM33) | _BV(WGM32);

  // Start enabled
  pwm_enable(true);
}

void loop() {
  button.update();

  // "pressed()" fires once per debounced press
  if (button.pressed()) {
    blinking = !blinking;
    pwm_enable(blinking);
  }
}
