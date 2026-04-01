// Coded for Arduino DUE AT91SAM3X8E

#include <Bounce2.h>

const uint8_t BUTTON_PIN = 2;
const uint8_t START_PULSE_PIN = 3;
const uint8_t STOP_PULSE_PIN = 7;
const uint8_t PWM_PIN = 6; // D6 = PWML7
Bounce2::Button button;

// ---- Pattern: 120ms up + 120ms down + 760ms off = 1000ms total ----
static constexpr uint16_t RAMP_UP_MS = 120;
static constexpr uint16_t RAMP_DOWN_MS = 120;
static constexpr uint16_t RAMP_MS = RAMP_UP_MS + RAMP_DOWN_MS; // 240
static constexpr uint16_t OFF_MS = 760;
static constexpr uint16_t PATTERN_MS = RAMP_MS + OFF_MS; // 1000
static constexpr uint16_t RAMP_HALF = RAMP_UP_MS; // 120

// PWM carrier: target ~62.5kHz at 8-bit period.
static constexpr uint16_t PWM_PERIOD = 255;
static constexpr uint32_t PWM_CARRIER_HZ = 62500;

// 1 kHz tick for the triangle pattern.
static constexpr uint32_t TICK_HZ = 1000;
static constexpr uint32_t TC_RC = VARIANT_MCK / 2 / TICK_HZ;

volatile uint16_t phase = 0;
uint8_t dutyLUT[PATTERN_MS]; // 1000 bytes SRAM

volatile bool desiredOn = true; // toggled by button
volatile bool running = false; // TC0 ISR running
volatile bool disableArmed = false; // stop only when output reaches 0
volatile bool startPulseLatched = false;
volatile bool stopPulseLatched = false;

static uint32_t pwmChannel = 0;

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
    dutyLUT[i] = (uint8_t)((uint32_t)x * 255u / (RAMP_HALF - 1)); // /119
  }
  // 240..999: OFF (0)
  for (uint16_t i = RAMP_MS; i < PATTERN_MS; i++) {
    dutyLUT[i] = 0;
  }
}

static inline void pwm_init()
{
  const PinDescription &pin = g_APinDescription[PWM_PIN];
  pwmChannel = pin.ulPWMChannel;

  pmc_enable_periph_clk(PWM_INTERFACE_ID);
  PWMC_ConfigureClocks(PWM_CARRIER_HZ * PWM_PERIOD, 0, VARIANT_MCK);

  PIO_Configure(pin.pPort, pin.ulPinType, pin.ulPin, pin.ulPinConfiguration);
  PWMC_ConfigureChannel(PWM_INTERFACE, pwmChannel, PWM_CMR_CPRE_CLKA, 0, 0);
  PWMC_SetPeriod(PWM_INTERFACE, pwmChannel, PWM_PERIOD);
  PWMC_SetDutyCycle(PWM_INTERFACE, pwmChannel, 0);
  PWMC_EnableChannel(PWM_INTERFACE, pwmChannel);
}

static inline void pwm_set_duty(uint8_t duty)
{
  PWMC_SetDutyCycle(PWM_INTERFACE, pwmChannel, duty);
}

static inline void tc_init_1kHz()
{
  pmc_enable_periph_clk(ID_TC0);
  TC_Configure(TC0, 0,
               TC_CMR_TCCLKS_TIMER_CLOCK1 |
               TC_CMR_WAVE |
               TC_CMR_WAVSEL_UP_RC);
  TC_SetRC(TC0, 0, TC_RC);
  TC0->TC_CHANNEL[0].TC_IDR = 0xFFFFFFFF;
  NVIC_DisableIRQ(TC0_IRQn);
}

static inline void tc_start_1kHz()
{
  TC0->TC_CHANNEL[0].TC_IDR = 0xFFFFFFFF;
  TC0->TC_CHANNEL[0].TC_IER = TC_IER_CPCS;
  TC_GetStatus(TC0, 0);
  NVIC_ClearPendingIRQ(TC0_IRQn);
  TC_Start(TC0, 0);
  NVIC_EnableIRQ(TC0_IRQn);
}

static inline void tc_stop_1kHz()
{
  NVIC_DisableIRQ(TC0_IRQn);
  TC0->TC_CHANNEL[0].TC_IDR = TC_IDR_CPCS;
  TC_Stop(TC0, 0);
  TC_GetStatus(TC0, 0);
}

static inline void stop_and_hold_at_zero()
{
  running = false;
  disableArmed = false;
  tc_stop_1kHz();
  pwm_set_duty(0);
}

static inline void start_from_zero()
{
  disableArmed = false;
  running = true;
  phase = 0;
  pwm_set_duty(0);
  tc_start_1kHz();
}

static inline void setDesired(bool on)
{
  noInterrupts();
  desiredOn = on;

  if (on) {
    disableArmed = false;
    if (!running) {
      start_from_zero();
    }
  } else {
    if (running) {
      disableArmed = true;
      if (dutyLUT[phase] == 0) {
        stop_and_hold_at_zero();
      }
    }
  }
  interrupts();
}

extern "C" void TC0_Handler(void)
{
  TC_GetStatus(TC0, 0);

  uint8_t duty = dutyLUT[phase];
  if (duty == 0) {
    pwm_set_duty(0);
    if (disableArmed) {
      stop_and_hold_at_zero();
      return;
    }
  } else {
    pwm_set_duty(duty);
  }

  phase++;
  if (phase >= PATTERN_MS) phase = 0;
}

void setup()
{
  button.attach(BUTTON_PIN, INPUT_PULLUP);
  button.interval(15);
  button.setPressedState(LOW);

  // Remote control inputs from a second controller board.
  // Expect short HIGH pulses on each pin:
  // - START_PULSE_PIN: start pattern generation
  // - STOP_PULSE_PIN: stop pattern generation (at next zero output point)
  pinMode(START_PULSE_PIN, INPUT);
  pinMode(STOP_PULSE_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(START_PULSE_PIN), onStartPulseRise, RISING);
  attachInterrupt(digitalPinToInterrupt(STOP_PULSE_PIN), onStopPulseRise, RISING);

  build_pattern_lut();
  pwm_init();
  tc_init_1kHz();

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
  noInterrupts();
  startReq = startPulseLatched;
  stopReq = stopPulseLatched;
  startPulseLatched = false;
  stopPulseLatched = false;
  interrupts();

  // Keep remote commands unambiguous: ignore simultaneous start+stop edges.
  if (startReq && !stopReq) {
    setDesired(true);
  } else if (stopReq && !startReq) {
    setDesired(false);
  }
}
