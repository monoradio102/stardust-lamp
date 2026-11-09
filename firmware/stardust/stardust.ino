// 星屑灯 v1 (GM_hosikuzu) firmware for the ATtiny1614.
// Version: FW_VERSION below; what changed in each version is in CHANGELOG.md next to this file.
// Circuit and board: hardware/README.md in https://github.com/monoradio102/stardust-lamp
//
// Arduino IDE + megaTinyCore 2.6:
//   Board "ATtiny3224/1624/1614/1604/824/814/804/424/414/404/214/204", Chip ATtiny1614,
//   Clock 20 MHz internal, millis()/micros() Timer: TCA0  <- TCD0 must stay free for the HV drive,
//   Programmer "SerialUPDI - 230400 baud". Slide SW1 to PROG to upload, back to RUN for the serial monitor.
//
// Serial (RUN side of SW1): 115200 baud, newline. The CH340's RX and TX share one wire with PB2,
// so USART0 runs half duplex (open drain + loopback).
//   hv <V>              high voltage target, 0 = off (starts at 0 until saved with "save")
//   mode cosmic|rad|digital
//   win <us>            coincidence window for the MCU's own count (digital mode, log column C)
//   dead <us>           per-tube dead time for the MCU's count: a pulse closer than this to the last counted
//                       pulse of the same tube is dropped (0 = count every edge). The HV recharge after a
//                       discharge can make a tube input fire again 20-90 us later; without this, one muon was
//                       counted 2-4 times in about 7 % of coincidences (long run 2026-11-07..09)
//   log <s>             log interval
//   kick <ms>           charge the glow capacitor from the MCU (test the LED)
//   flash <ms>          rad mode: each hit lights the lamp this long, then the glow is emptied at once
//                       (0 = the old soft way: every hit adds a little charge and FADE lets it go)
//   sweep <from> <to> <step> <s>   plateau scan: counts per tube at each voltage
//   hvon <counts>       HV drive: 0 = steady 31 kHz with the on-time regulated (the old way); otherwise
//                       pulse drive: fixed on-time of <counts> x 50 ns, far fewer pulses, so far less switching
//                       loss. The pulses are spread out evenly (see hvBurst); the log "on" column is then the
//                       pulses sent as a share of the steady 31 kHz rate, in 0.1 %
//   ev on|off           print "e,A" / "e,B" for every hit and "e,C" for every coincidence, as they happen
//                       (for live displays; not saved)
//   dt on|off           print "dt,<us>" for every A-B pair closer than DT_MAX_US (tB - tA; not saved)
//   status | save | version
//
// Modes (the hardware PNP stack lights the lamp on every A+B overlap in all modes):
//   cosmic   MCU only counts; OUT pulses on each coincidence
//   rad      every single hit flashes the lamp briefly, like the click of a Geiger counter (see flash)
//   digital  MCU decides coincidences with its own window and kicks a full glow
// Log columns: t_s, hv_V, on (TCD counts of 50 ns), A, B (hits in the interval), C (coincidences
// within win), D (delayed coincidences: B 1 ms after A or the reverse = an estimate of chance overlaps),
// vbat_V, mode

#include <EEPROM.h>

// Bump on every change that is flashed: MAJOR when the log or command format breaks old tools,
// MINOR for new commands or behavior, PATCH for fixes. Record it in CHANGELOG.md.
#define FW_VERSION "1.6.0"
#include <avr/sleep.h>

// ---- pins ----
const uint8_t PIN_TUBE_A = PIN_PA1;  // low while tube A conducts
const uint8_t PIN_TUBE_B = PIN_PA2;
const uint8_t PIN_BTN = PIN_PA3;     // mode button to GND
const uint8_t PIN_HV_DRV = PIN_PA4;  // TCD0 WOA -> BSP125 gate (100k pull-down)
const uint8_t PIN_VBAT = PIN_PA6;    // VIN / 4.03
const uint8_t PIN_HV_FB = PIN_PA7;   // HV / 364.6
const uint8_t PIN_KICK = PIN_PB0;    // 1k into the glow capacitor; input when idle
const uint8_t PIN_OUT = PIN_PB1;     // Q9 gate: HIGH pulls the OUT pad low

// ---- high voltage ----
const float HV_DIV = (8 * 10.0e6 + 220e3) / 220e3;  // R20..R27 + R28 over R28
const float HV_MAX_SET = 450;     // highest target accepted (J321 plateau tops out around 440 V)
const float HV_TRIP = 470;        // above this the drive stops at once (the TVS clamps near 500 V)
const uint16_t HV_PERIOD = 640;   // 20 MHz / 640 = 31.25 kHz, above hearing
const uint16_t HV_ON_MAX = 300;   // 15 us on-time cap
const uint32_t HV_STUCK_MS = 3000;  // on-time at the cap this long without reaching half the target -> fault

// ---- glow and output pulses ----
const uint16_t KICK_RAD_US = 300;     // one hit in rad mode with flash 0
const uint16_t DRAIN_US = 10000;      // KICK held low this long empties the glow capacitor (1k x 2.2uF = 2.2 ms)
const uint16_t KICK_COINC_US = 4000;  // one coincidence in digital mode (about a full charge)
const uint16_t OUT_PULSE_US = 1000;
const uint32_t DELAY_US = 1000;       // offset for the delayed (chance) coincidence count
const uint32_t DT_MAX_US = 5000;      // widest pair reported by "dt on"

enum Mode : uint8_t { COSMIC, RAD, DIGITAL };
const char *const MODE_NAMES[] = {"cosmic", "rad", "digital"};

struct Settings {
  uint16_t magic;
  uint16_t hvTarget;  // V
  uint8_t mode;
  uint16_t winUs;
  uint16_t logS;
  uint8_t flashMs;  // rad mode flash length, 0 = soft
  uint16_t burstOn;  // HV burst on-time in 50 ns counts, 0 = steady drive
  uint16_t deadUs;   // per-tube dead time for the MCU's count
};
const uint16_t MAGIC = 0x5D04;
const uint16_t MAGIC_V3 = 0x5D03;  // same layout without deadUs
const uint16_t MAGIC_V2 = 0x5D02;  // same layout without burstOn and deadUs
// dead 200: the repeat pulses all fell within 100 us of the first (none in excess from 100 us to 5 ms),
// so twice that; a tube near 20 CPM loses less than 1 real hit in 10,000 to it
Settings cfg = {MAGIC, 0, COSMIC, 100, 10, 30, 300, 200};

// ---- event counting (interrupts) ----
volatile uint32_t lastA = 0, lastB = 0;
volatile uint16_t hitsA = 0, hitsB = 0, coinc = 0, delayed = 0;
volatile uint8_t pendingKickA = 0, pendingKickB = 0, pendingCoinc = 0;
volatile bool seenA = false, seenB = false;

// pair times for "dt on": filled in the interrupts, printed from the loop
bool dtOn = false;
volatile int16_t dtBuf[16];
volatile uint8_t dtHead = 0, dtTail = 0;
static inline void pushDt(int16_t v) {
  uint8_t n = (dtHead + 1) & 15;
  if (n != dtTail) { dtBuf[dtHead] = v; dtHead = n; }
}

// single hits and coincidences for "ev on", same scheme
bool evOn = false;
volatile char evBuf[32];
volatile uint8_t evHead = 0, evTail = 0;
static inline void pushEv(char c) {
  if (!evOn) return;
  uint8_t n = (evHead + 1) & 31;
  if (n != evTail) { evBuf[evHead] = c; evHead = n; }
}

static inline bool within(uint32_t d, uint32_t lo, uint32_t hi) { return d >= lo && d <= hi; }

void onTubeA() {
  uint32_t t = micros();
  if (seenA && t - lastA < cfg.deadUs) return;  // a repeat of the same discharge (lastA stays at the first)
  hitsA++;
  pendingKickA++;
  pushEv('A');
  if (seenB) {
    uint32_t d = t - lastB;  // B came first
    if (d <= cfg.winUs) { coinc++; pendingCoinc++; pushEv('C'); }
    if (within(d, DELAY_US - cfg.winUs, DELAY_US + cfg.winUs)) delayed++;
    if (dtOn && d <= DT_MAX_US) pushDt(-(int16_t)d);
  }
  lastA = t;
  seenA = true;
}

void onTubeB() {
  uint32_t t = micros();
  if (seenB && t - lastB < cfg.deadUs) return;
  hitsB++;
  pendingKickB++;
  pushEv('B');
  if (seenA) {
    uint32_t d = t - lastA;
    if (d <= cfg.winUs) { coinc++; pendingCoinc++; pushEv('C'); }
    if (within(d, DELAY_US - cfg.winUs, DELAY_US + cfg.winUs)) delayed++;
    if (dtOn && d <= DT_MAX_US) pushDt((int16_t)d);
  }
  lastB = t;
  seenB = true;
}

// ---- HV drive: TCD0 one-ramp, WOA high from 0 to CMPACLR, period CMPBCLR ----
// FAULTCTRL (which hands the pin to TCD0) and CTRLB are enable-protected: they only take while TCD0 is
// stopped, so they are written once in hvSetup(). Off = TCD0 stopped, and the pin then sits low.
float onCounts = 0;
uint32_t allMs = 0, runSince = 0;  // pulse drive bookkeeping
float runEq = 0;                   // pulses sent, in ms at the steady 31 kHz rate
uint32_t stuckSince = 0;
const char *fault = nullptr;
bool tcdRunning = false;
const uint16_t PULSE_FAST = HV_PERIOD / 4;  // period in 200 ns counts at the steady 31 kHz rate
const uint16_t PULSE_SLOW = 4095;           // the longest TCD0 period: 819 us, 1.2 kHz
const float PULSE_DUTY = 0.7;               // share of 1 ms steps the drive should be on
const float PULSE_DUTY_K = 1.0 / 64;        // the share is averaged over about 64 ms
const float PULSE_RATE_K = 0.002;           // period change per ms per unit of share error (seconds to settle)
float pulsePeriod = PULSE_FAST;             // starts at full rate, so the HV comes up as fast as before
float pulseDuty = PULSE_DUTY;

void hvSetup() {
  digitalWrite(PIN_HV_DRV, LOW);
  pinMode(PIN_HV_DRV, OUTPUT);
  TCD0.CTRLA = 0;  // stop (the core may have started it for PWM)
  TCD0.CTRLB = TCD_WGMODE_ONERAMP_gc;
  TCD0.CMPASET = 0;
  TCD0.CMPACLR = 0;
  TCD0.CMPBSET = 0;
  TCD0.CMPBCLR = HV_PERIOD - 1;
  _PROTECTED_WRITE(TCD0.FAULTCTRL, TCD_CMPAEN_bm);  // WOA drives PA4; its value is low while TCD0 is stopped
  tcdRunning = false;
}

void hvApply(uint16_t counts) {
  if (counts == 0) {
    if (tcdRunning) {
      TCD0.CTRLA = TCD_CLKSEL_20MHZ_gc | TCD_CNTPRES_DIV1_gc;  // stop
      tcdRunning = false;
    }
    return;
  }
  TCD0.CMPACLR = counts;
  if (!tcdRunning) {
    TCD0.CMPBCLR = HV_PERIOD - 1;  // the pulse drive may have left a longer period
    while (!(TCD0.STATUS & TCD_ENRDY_bm)) {}
    TCD0.CTRLA = TCD_CLKSEL_20MHZ_gc | TCD_CNTPRES_DIV1_gc | TCD_ENABLE_bm;
    tcdRunning = true;
  } else {
    TCD0.CTRLE = TCD_SYNCEOC_bm;  // take the new value at the end of this cycle
  }
}

void hvStop(const char *why) {
  onCounts = 0;
  runSince = 0;
  pulsePeriod = PULSE_FAST;  // the next start comes up at full rate
  pulseDuty = PULSE_DUTY;
  hvApply(0);
  if (why) {
    fault = why;
    cfg.hvTarget = 0;
    Serial.print(F("! HV stopped: "));
    Serial.println(why);
  }
}

float readHV() {
  uint32_t sum = 0;
  for (uint8_t i = 0; i < 8; i++) sum += analogRead(PIN_HV_FB);
  return sum / 8.0 / 1023.0 * 1.5 * HV_DIV;
}

float readVbat() {
  analogReference(INTERNAL2V5);
  analogRead(PIN_VBAT);  // let the reference settle
  float v = analogRead(PIN_VBAT) / 1023.0 * 2.5 * (1.0e6 + 330e3) / 330e3;
  analogReference(INTERNAL1V5);
  analogRead(PIN_HV_FB);
  return v;
}

float hvNow = 0;

// Pulse drive for hvon > 0, with TCD0 counting at 5 MHz (DIV4) so the period can reach 819 us.
// The HV load is only the feedback divider (a few uA), while every switching cycle throws away the charge on
// the switch node, so the drive sends few pulses with a long fixed on-time.
// 1.3.0-1.5.0 ran the pulses at the full 31 kHz for a whole 1 ms whenever the HV was low: about one 1 ms burst
// every 14 ms, at an uneven rhythm around 70 Hz. Each burst kicked the HV up a little, and that reached the
// lamp as a visible flicker (and is the likely cause of the repeat pulses that dead time now drops).
// Now the on/off decision is still made every 1 ms, but while on, the pulses come at a rate that is tuned
// slowly so that the drive is on in about PULSE_DUTY of the steps: each on-step then sends only a few pulses,
// and the off-steps are short and frequent, so the HV ripple is small and far above what the eye follows.

void hvPulse(uint16_t counts) {  // counts in 50 ns, like hvon
  TCD0.CMPACLR = (counts + 2) / 4;
  TCD0.CMPBCLR = (uint16_t)pulsePeriod - 1;
  if (!tcdRunning) {
    while (!(TCD0.STATUS & TCD_ENRDY_bm)) {}
    TCD0.CTRLA = TCD_CLKSEL_20MHZ_gc | TCD_CNTPRES_DIV4_gc | TCD_ENABLE_bm;
    tcdRunning = true;
  } else {
    TCD0.CTRLE = TCD_SYNCEOC_bm;
  }
}

void hvBurst() {
  uint16_t raw = analogRead(PIN_HV_FB) + analogRead(PIN_HV_FB);
  float v = raw / 2.0 / 1023.0 * 1.5 * HV_DIV;
  hvNow += (v - hvNow) * 0.1;  // smoothed for the log and status
  if (v > HV_TRIP) { if (tcdRunning || cfg.hvTarget) hvStop("over voltage"); return; }  // report once
  allMs++;
  if (cfg.hvTarget == 0) { if (tcdRunning) hvStop(nullptr); runSince = 0; pulsePeriod = PULSE_FAST; return; }
  bool on = v < cfg.hvTarget;
  pulseDuty += ((on ? 1.0 : 0.0) - pulseDuty) * PULSE_DUTY_K;
  pulsePeriod *= 1 - PULSE_RATE_K * (pulseDuty - PULSE_DUTY);  // on too often: pulse faster
  pulsePeriod = constrain(pulsePeriod, PULSE_FAST, PULSE_SLOW);
  if (on) {
    hvPulse(min(cfg.burstOn, HV_ON_MAX));
    runEq += PULSE_FAST / pulsePeriod;
    if (!runSince) runSince = millis();
    if (v < cfg.hvTarget * 0.5 && millis() - runSince > HV_STUCK_MS) hvStop("no rise at full drive (feedback open or short?)");
  } else {
    hvApply(0);
    runSince = 0;
  }
}

// Integral control on the on-time, every 5 ms. The output only starts once the switch node rings up to
// about half the target, so the step is limited to a couple of counts and the loop stays gentle.
void hvControl() {
  hvNow = readHV();
  if (hvNow > HV_TRIP) { hvStop("over voltage"); return; }
  if (cfg.hvTarget == 0) { if (onCounts) hvStop(nullptr); return; }
  float err = cfg.hvTarget - hvNow;
  onCounts += constrain(err * 0.01, -2.0, 1.0);
  onCounts = constrain(onCounts, 0, HV_ON_MAX);
  hvApply((uint16_t)onCounts);
  if (onCounts >= HV_ON_MAX && hvNow < cfg.hvTarget * 0.5) {
    if (!stuckSince) stuckSince = millis();
    if (millis() - stuckSince > HV_STUCK_MS) hvStop("no rise at full drive (feedback open or short?)");
  } else {
    stuckSince = 0;
  }
}

// ---- glow kick and OUT pulses, timed from loop() so the interrupts never wait ----
uint32_t kickUntil = 0, drainUntil = 0, outUntil = 0;
bool kickOn = false, drainOn = false, drainAfter = false, outOn = false;

// Drives KICK high for us microseconds. With drain, KICK is then driven low for DRAIN_US to empty the
// glow capacitor, so the lamp goes dark right away instead of fading out.
void kick(uint32_t us, bool drain = false) {
  digitalWrite(PIN_KICK, HIGH);
  pinMode(PIN_KICK, OUTPUT);
  uint32_t until = micros() + us;
  if (!kickOn || (int32_t)(until - kickUntil) > 0) kickUntil = until;
  kickOn = true;
  drainOn = false;
  drainAfter = drain;
}

void outPulse() {
  digitalWrite(PIN_OUT, HIGH);
  outUntil = micros() + OUT_PULSE_US;
  outOn = true;
}

void serviceOutputs() {
  uint32_t now = micros();
  if (kickOn && (int32_t)(now - kickUntil) >= 0) {
    kickOn = false;
    if (drainAfter) {
      digitalWrite(PIN_KICK, LOW);  // empty the glow capacitor through the 1k
      drainUntil = now + DRAIN_US;
      drainOn = true;
    } else {
      pinMode(PIN_KICK, INPUT);  // let go: the glow capacitor discharges on its own
    }
  }
  if (drainOn && (int32_t)(now - drainUntil) >= 0) {
    pinMode(PIN_KICK, INPUT);
    drainOn = false;
  }
  if (outOn && (int32_t)(now - outUntil) >= 0) {
    digitalWrite(PIN_OUT, LOW);
    outOn = false;
  }
}

void handleEvents() {
  noInterrupts();
  uint8_t a = pendingKickA, b = pendingKickB, c = pendingCoinc;
  pendingKickA = pendingKickB = pendingCoinc = 0;
  interrupts();
  if (cfg.mode == RAD) {
    if (a + b) {
      if (cfg.flashMs) kick((uint32_t)cfg.flashMs * 1000, true);
      else kick((uint32_t)KICK_RAD_US * (a + b));
      outPulse();
    }
  } else if (c) {
    if (cfg.mode == DIGITAL) kick(KICK_COINC_US);
    outPulse();
  }
}

// ---- mode button ----
uint8_t btnHist = 0xFF;
// Shows the mode as 1-3 short flashes. Each flash charges the glow capacitor briefly, then KICK is driven
// low to empty it through the 1k at once, so the flashes stay apart however long FADE makes the glow.
// Blocking wait that keeps the HV regulated: in burst mode the timer runs at full on-time until hvBurst()
// stops it, so the drive must never be left alone for more than a few ms.
void waitMs(uint16_t ms) {
  uint32_t t0 = millis(), last = t0;
  while (millis() - t0 < ms) {
    if (millis() != last) {
      last = millis();
      if (cfg.burstOn) hvBurst();
    }
    __asm__ __volatile__("wdr");
  }
}

void blinkMode() {
  for (uint8_t i = 0; i <= cfg.mode; i++) {
    pinMode(PIN_KICK, OUTPUT);
    digitalWrite(PIN_KICK, HIGH);
    waitMs(150);
    digitalWrite(PIN_KICK, LOW);
    waitMs(300);
  }
  pinMode(PIN_KICK, INPUT);
  kickOn = drainOn = false;
}

void serviceButton() {
  static uint32_t last = 0;
  if (millis() - last < 10) return;
  last = millis();
  btnHist = (btnHist << 1) | digitalRead(PIN_BTN);
  if (btnHist == 0xF0) {  // high for 40 ms, then low for 40 ms: one clean press
    cfg.mode = (cfg.mode + 1) % 3;
    Settings saved;
    EEPROM.get(0, saved);
    if (saved.magic == MAGIC) EEPROM.put(offsetof(Settings, mode), cfg.mode);  // keep the mode across power-offs
    Serial.print(F("mode "));
    Serial.println(MODE_NAMES[cfg.mode]);
    blinkMode();
  }
}

// ---- serial commands ----
char line[40];
uint8_t lineLen = 0;

void printStatus() {
  Serial.print(F("# hv target ")); Serial.print(cfg.hvTarget);
  Serial.print(F(" V, now ")); Serial.print(hvNow, 1);
  if (cfg.burstOn) { Serial.print(F(" V, burst ")); Serial.print(cfg.burstOn); }
  else { Serial.print(F(" V, on ")); Serial.print((uint16_t)onCounts); }
  Serial.print(F(", mode ")); Serial.print(MODE_NAMES[cfg.mode]);
  Serial.print(F(", win ")); Serial.print(cfg.winUs);
  Serial.print(F(" us, dead ")); Serial.print(cfg.deadUs);
  Serial.print(F(" us, flash ")); Serial.print(cfg.flashMs);
  Serial.print(F(" ms"));
  Serial.print(F(", log ")); Serial.print(cfg.logS);
  Serial.print(F(" s, fault ")); Serial.print(fault ? fault : "none");
  Serial.println(F(", fw " FW_VERSION));
}

void sweep(int from, int to, int step, int secs);

void runCommand(char *s) {
  char *cmd = strtok(s, " ");
  if (!cmd) return;
  char *a1 = strtok(nullptr, " ");
  if (!strcmp(cmd, "hv") && a1) {
    float v = atof(a1);
    cfg.hvTarget = v <= 0 ? 0 : (uint16_t)min(v, HV_MAX_SET);
    fault = nullptr;
    if (!cfg.hvTarget) hvStop(nullptr);
  } else if (!strcmp(cmd, "mode") && a1) {
    for (uint8_t i = 0; i < 3; i++) if (!strcmp(a1, MODE_NAMES[i])) cfg.mode = i;
  } else if (!strcmp(cmd, "win") && a1) {
    cfg.winUs = constrain(atoi(a1), 5, 5000);
  } else if (!strcmp(cmd, "dead") && a1) {
    cfg.deadUs = constrain(atoi(a1), 0, 5000);
  } else if (!strcmp(cmd, "log") && a1) {
    cfg.logS = constrain(atoi(a1), 1, 3600);
  } else if (!strcmp(cmd, "flash") && a1) {
    cfg.flashMs = constrain(atoi(a1), 0, 200);
  } else if (!strcmp(cmd, "hvon") && a1) {
    hvStop(nullptr);
    cfg.burstOn = constrain(atoi(a1), 0, (int)HV_ON_MAX);
  } else if (!strcmp(cmd, "ev") && a1) {
    evOn = !strcmp(a1, "on");
    Serial.println(evOn ? F("# ev on") : F("# ev off"));
    return;
  } else if (!strcmp(cmd, "dt") && a1) {
    dtOn = !strcmp(a1, "on");
    Serial.println(dtOn ? F("# dt on") : F("# dt off"));
    return;
  } else if (!strcmp(cmd, "kick") && a1) {
    kick((uint32_t)constrain(atoi(a1), 1, 50) * 1000);
  } else if (!strcmp(cmd, "sweep")) {
    char *a2 = strtok(nullptr, " "), *a3 = strtok(nullptr, " "), *a4 = strtok(nullptr, " ");
    if (a1 && a2 && a3 && a4) sweep(atoi(a1), atoi(a2), atoi(a3), atoi(a4));
    return;
  } else if (!strcmp(cmd, "version")) {
    Serial.println(F("# fw " FW_VERSION ", built " __DATE__ " " __TIME__));
    return;
  } else if (!strcmp(cmd, "save")) {
    EEPROM.put(0, cfg);
    Serial.println(F("# saved"));
  } else if (strcmp(cmd, "status")) {
    return;  // anything else (including our own echoed log lines) is ignored
  }
  printStatus();
}

void serviceSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[lineLen] = 0;
      if (lineLen) runCommand(line);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}

// ---- main loop pieces ----
void tick() {
  static uint32_t lastCtl = 0;
  static uint8_t ctlN = 0;
  if (millis() != lastCtl) {
    lastCtl = millis();
    if (cfg.burstOn) hvBurst();
    else if (++ctlN >= 5) { ctlN = 0; hvControl(); }
  }
  handleEvents();
  while (evTail != evHead) {
    Serial.print(F("e,")); Serial.println(evBuf[evTail]);
    evTail = (evTail + 1) & 31;
  }
  while (dtTail != dtHead) {
    Serial.print(F("dt,")); Serial.println(dtBuf[dtTail]);
    dtTail = (dtTail + 1) & 15;
  }
  serviceOutputs();
  serviceButton();
  serviceSerial();
  __asm__ __volatile__("wdr");  // a hang resets the chip, and the HV gate is pulled low during reset
}

void takeCounts(uint16_t &a, uint16_t &b, uint16_t &c, uint16_t &d) {
  noInterrupts();
  a = hitsA; b = hitsB; c = coinc; d = delayed;
  hitsA = hitsB = coinc = delayed = 0;
  interrupts();
}

void sweep(int from, int to, int step, int secs) {
  if (step <= 0 || secs <= 0) return;
  Serial.println(F("# sweep: V,A,B (counts in the window),cpmA,cpmB"));
  uint8_t savedMode = cfg.mode;
  for (int v = from; v <= to; v += step) {
    cfg.hvTarget = min(v, (int)HV_MAX_SET);
    uint32_t t = millis();
    while (millis() - t < 5000) tick();  // settle
    uint16_t a, b, c, d;
    takeCounts(a, b, c, d);
    t = millis();
    while (millis() - t < (uint32_t)secs * 1000) tick();
    takeCounts(a, b, c, d);
    Serial.print(v); Serial.print(','); Serial.print(a); Serial.print(','); Serial.print(b); Serial.print(',');
    Serial.print(a * 60.0 / secs, 1); Serial.print(','); Serial.println(b * 60.0 / secs, 1);
    if (fault) break;
  }
  cfg.mode = savedMode;
  Serial.println(F("# sweep done (hv left at the last step; send hv <V> to change)"));
}

void setup() {
  Settings saved;
  EEPROM.get(0, saved);
  if (saved.magic == MAGIC) cfg = saved;
  else if (saved.magic == MAGIC_V3 || saved.magic == MAGIC_V2) {  // older layouts: keep what they had, default the rest
    if (saved.magic == MAGIC_V2) saved.burstOn = cfg.burstOn;
    saved.deadUs = cfg.deadUs;
    saved.magic = MAGIC;
    cfg = saved;
  }  // a saved HV target starts the lamp on its own; first boot stays at 0

  pinMode(PIN_KICK, INPUT);
  pinMode(PIN_OUT, OUTPUT);
  digitalWrite(PIN_OUT, LOW);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_TUBE_A, INPUT);
  pinMode(PIN_TUBE_B, INPUT);
  hvSetup();
  analogReference(INTERNAL1V5);

  Serial.begin(115200, SERIAL_HALF_DUPLEX);
  PORTB.PIN2CTRL |= PORT_PULLUPEN_bm;

  attachInterrupt(digitalPinToInterrupt(PIN_TUBE_A), onTubeA, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_TUBE_B), onTubeB, FALLING);

  Serial.println(F("# stardust v1, fw " FW_VERSION));
  printStatus();
  Serial.println(F("# t_s,hv_V,on,A,B,C,D,vbat_V,mode"));
  _PROTECTED_WRITE(WDT.CTRLA, WDT_PERIOD_2KCLK_gc);  // watchdog, about 2 s
  set_sleep_mode(SLEEP_MODE_IDLE);  // CPU stops, timers / pins / USART keep running; any interrupt wakes it
  sleep_enable();
  blinkMode();
}

void loop() {
  static uint32_t lastLog = millis();
  tick();
  if (millis() - lastLog >= (uint32_t)cfg.logS * 1000) {
    lastLog += (uint32_t)cfg.logS * 1000;
    // after the interval is shortened we are many steps behind: start over from now instead of
    // printing a burst of catch-up lines that all carry the same time
    if (millis() - lastLog >= (uint32_t)cfg.logS * 1000) lastLog = millis();
    uint16_t a, b, c, d;
    takeCounts(a, b, c, d);
    Serial.print(millis() / 1000); Serial.print(',');
    Serial.print(hvNow, 1); Serial.print(',');
    if (cfg.burstOn) { Serial.print(allMs ? (uint16_t)(runEq * 1000 / allMs) : 0); runEq = 0; allMs = 0; }
    else Serial.print((uint16_t)onCounts);
    Serial.print(',');
    Serial.print(a); Serial.print(','); Serial.print(b); Serial.print(',');
    Serial.print(c); Serial.print(','); Serial.print(d); Serial.print(',');
    Serial.print(readVbat(), 2); Serial.print(',');
    Serial.println(MODE_NAMES[cfg.mode]);
  }
  // Nothing timed by polling micros() is running: sleep until the next interrupt (the millis tick wakes
  // us every ms, a tube hit or a serial byte sooner)
  if (!kickOn && !drainOn && !outOn) sleep_cpu();
}
