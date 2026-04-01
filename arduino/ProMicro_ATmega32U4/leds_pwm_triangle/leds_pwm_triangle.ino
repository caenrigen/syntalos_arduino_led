// Coded for SparkFun Pro Micro 5V, ATmega32U4

#include <Bounce2.h>
#include <avr/io.h>
#include <avr/interrupt.h>

const uint8_t BUTTON_PIN = 2;
const uint8_t START_PULSE_PIN = 3;
const uint8_t STOP_PULSE_PIN = 7;
Bounce2::Button button;

// ---- Pattern: 120ms up + 120ms down + 760ms off = 1000ms total ----
static constexpr uint16_t RAMP_UP_MS   = 120;
static constexpr uint16_t RAMP_DOWN_MS = 120;
static constexpr uint16_t RAMP_MS      = RAMP_UP_MS + RAMP_DOWN_MS; // 240
static constexpr uint16_t OFF_MS       = 760;
static constexpr uint16_t PATTERN_MS   = RAMP_MS + OFF_MS;          // 1000
static constexpr uint16_t RAMP_HALF    = RAMP_UP_MS;                // 120

// Timer3 carrier PWM: TOP=255 -> 62.5kHz at 16 MHz, prescaler /1
static constexpr uint16_t CARRIER_TOP  = 255;
static constexpr uint8_t  T3_CS_MASK   = (_BV(CS32) | _BV(CS31) | _BV(CS30));

volatile uint16_t phase = 0;
uint8_t dutyLUT[PATTERN_MS]; // 1000 bytes SRAM

volatile bool desiredOn    = true;   // toggled by button
volatile bool running      = false;  // Timer1 ISR running
volatile bool disableArmed = false;  // stop only when output reaches 0
volatile bool startPulseLatched = false;
volatile bool stopPulseLatched = false;

void onStartPulseRise()
{
  startPulseLatched = true;
}

void onStopPulseRise()
{
  stopPulseLatched = true;
}

static inline void build_pattern_lut()
{
  // 0..239: triangle (120 up, 120 down)
  for (uint16_t i = 0; i < RAMP_MS; i++) {
    uint16_t x = (i < RAMP_HALF) ? i : (RAMP_MS - 1 - i); // 0..119..0
    dutyLUT[i] = (uint8_t)((uint32_t)x * 255u / (RAMP_HALF - 1));   // /119
  }
  // 240..999: OFF (0)
  for (uint16_t i = RAMP_MS; i < PATTERN_MS; i++) {
    dutyLUT[i] = 0;
  }
}

static inline void timer3_init_carrier()
{
  // D5 is PC6 = OC3A on Pro Micro
  DDRC |= _BV(6); // PC6 output

  // Stop Timer3 while configuring
  TCCR3A = 0;
  TCCR3B = 0;
  TCNT3  = 0;

  // Fast PWM, TOP = ICR3 (Mode 14: WGM33:0 = 1110)
  ICR3  = CARRIER_TOP;
  OCR3A = 0;

  // Set WGM bits; leave OC3A disconnected and clock stopped for now
  TCCR3A = _BV(WGM31);
  TCCR3B = _BV(WGM33) | _BV(WGM32);
}

static inline void timer1_init_1kHz()
{
  // Timer1 CTC @ 1 kHz:
  // 16MHz / (64 * (1 + 249)) = 1000 Hz
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1  = 0;

  OCR1A  = 249;
  TCCR1B = _BV(WGM12); // CTC; clock bits set when starting
  TIMSK1 = 0;          // interrupt enable set when starting
}

static inline void hard_off_pc6()
{
  // Disconnect OC3A from pin and force LOW via PORTC
  TCCR3A &= ~(_BV(COM3A1) | _BV(COM3A0));
  PORTC  &= ~_BV(6);
  OCR3A   = 0;

  // Stop Timer3 clock (prevents any internal PWM edge cases too)
  TCCR3B &= ~T3_CS_MASK;
}

static inline void stop_and_hold_at_zero()
{
  running = false;
  disableArmed = false;

  // Stop Timer1 interrupt + clock
  TIMSK1 &= ~_BV(OCIE1A);
  TCCR1B &= ~(_BV(CS12) | _BV(CS11) | _BV(CS10));

  // Ensure output is truly off
  hard_off_pc6();
}

static inline void start_from_zero()
{
  uint8_t s = SREG; cli();

  disableArmed = false;
  running = true;

  phase = 0;

  // Start in a guaranteed OFF state at phase 0
  hard_off_pc6();

  // Start Timer1: prescaler /64 and enable interrupt
  TCNT1 = 0;
  TIMSK1 |= _BV(OCIE1A);
  TCCR1B = (TCCR1B & ~(_BV(CS12) | _BV(CS11) | _BV(CS10))) | _BV(CS11) | _BV(CS10);

  SREG = s;
}

static inline void setDesired(bool on)
{
  uint8_t s = SREG; cli();
  desiredOn = on;

  if (on) {
    disableArmed = false;
    if (!running) {
      SREG = s;
      start_from_zero();
      return;
    }
  } else {
    if (running) {
      disableArmed = true;

      // If we're currently at 0 (OFF window), stop immediately and hold at 0.
      if (dutyLUT[phase] == 0) {
        stop_and_hold_at_zero();
      }
    }
  }

  SREG = s;
}

// 1 ms tick: update duty cycle; hard-off during OFF window (and duty==0),
// stop only when output is at 0 if disableArmed.
ISR(TIMER1_COMPA_vect)
{
  uint8_t d = dutyLUT[phase];

  if (d == 0) {
    // Guarantee *no* residual pulses: disconnect OC3A, force pin LOW, stop Timer3 clock
    hard_off_pc6();

    if (disableArmed) {
      stop_and_hold_at_zero();
      return;
    }
  } else {
    // Ensure Timer3 is running at /1 and starts cleanly if it was stopped
    if ((TCCR3B & T3_CS_MASK) == 0) {
      TCNT3 = 0; // align PWM carrier start
    }
    TCCR3B = (TCCR3B & ~T3_CS_MASK) | _BV(CS30); // prescaler /1

    // Connect OC3A (non-inverting) and set duty
    TCCR3A |= _BV(COM3A1); // COM3A0 stays 0
    OCR3A = d;
  }

  phase++;
  if (phase >= PATTERN_MS) phase = 0;
}

void setup()
{
  RXLED0;
  TXLED0;

  button.attach(BUTTON_PIN, INPUT_PULLUP);
  button.interval(15);
  button.setPressedState(LOW);

  // Remote control inputs from a second controller board.
  // Both D3 and D7 support external interrupts on the Pro Micro.
  pinMode(START_PULSE_PIN, INPUT);
  pinMode(STOP_PULSE_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(START_PULSE_PIN), onStartPulseRise, RISING);
  attachInterrupt(digitalPinToInterrupt(STOP_PULSE_PIN), onStopPulseRise, RISING);

  build_pattern_lut();
  timer3_init_carrier();
  timer1_init_1kHz();

  setDesired(true); // start enabled
}

void loop()
{
  button.update();
  if (button.pressed()) {
    setDesired(!desiredOn); // toggle
  }

  bool startReq = false;
  bool stopReq = false;
  uint8_t s = SREG; cli();
  startReq = startPulseLatched;
  stopReq = stopPulseLatched;
  startPulseLatched = false;
  stopPulseLatched = false;
  SREG = s;

  if (startReq && !stopReq) {
    setDesired(true);
  } else if (stopReq && !startReq) {
    setDesired(false);
  }
}
