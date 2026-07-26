# Smart-IoT "Smart Window" — Full Hardware Wiring Guide

> Written for someone with **zero** analog/digital electronics background.
> If you follow this top-to-bottom you cannot damage anything. Read the
> **Golden Rules** once, then just do the steps.

---

## 0. What we are building

One ESP32-S3 board pretends to be a smart window node. It reads:

- **Temperature + Humidity** (DHT11)
- **Luminance / brightness** (photoresistor light sensor)
- **Air quality** — ammonia, benzene, ethanol, smoke, CO₂, etc. (MQ-135)

and it acts with:

- **Buzzer** — alarm when air quality / values cross a threshold
- **OLED screen** — shows readings + window state (OPEN / CLOSED)
- **On-board RGB LED (GPIO48)** — green = window open, red = window closed

The "window motor" is *simulated*: the user sends an open/close command, and we
show the result on the OLED + LED. No servo is wired (you didn't list one).

Even though the software will treat these as if they were separate isolated
sensor nodes, **physically they all connect to this one ESP32-S3 board.**

---

## 1. Your parts checklist

| # | Part | Connector printed on it | How it talks to the MCU |
|---|------|--------------------------|--------------------------|
| A | ESP32-S3-DevKitC-1 (Goouuu, N16R8) | — | the brain |
| B | DHT11 temp/humidity | `DATA  VCC  GND` | 1 digital wire |
| C | Light sensor (photoresistor) | `I/O  VCC  GND` | 1 **analog** wire |
| D | MQ-135 air quality | `AO  DO  VCC  GND` | 1 **analog** wire (AO) |
| E | OLED 0.96" (SSD1306) | `VDD/VCC  GND  SCK/SCL  SDA` | 2 wires (I²C bus) |
| F | Buzzer (active, high-level) | `I/O  VCC  GND` | 1 digital wire |

Plus: **breadboard**, **male-female / male-male jumper wires**, **multimeter**,
soldering iron (only needed if any module came without its pin header soldered —
see §3).

---

## 2. The Golden Rules (read once)

1. **Everything powers from the 3.3V rail, not 5V.**
   The ESP32-S3 pins are **3.3V logic**. If you power a sensor from 5V, its
   signal/data output can also become ~5V and that can **permanently damage**
   the ESP32 pin. Powering every module from **3.3V** keeps all signals ≤3.3V —
   totally safe. (This is the single most important rule.)

2. **Never plug or unplug wires while the USB cable is connected.**
   Always: wire it up → double-check → *then* plug in USB.

3. **Signal wires go straight to a GPIO. Power/ground go to the rails.**
   You'll build two "rails" on the breadboard (a `+` 3.3V line and a `−` GND
   line); every module's VCC/GND taps those rails. Only the signal pin is unique.

4. **Match the label, not the color.** Wire colors mean nothing — follow the
   printed labels (VCC, GND, DATA, etc.).

5. **MQ-135 gets hot and needs warm-up.** The metal can warms up (normal). It
   needs a few minutes (ideally longer) of power before readings settle. Don't
   touch the hot ring.

---

## 3. Do the modules need soldering?

Look at each module's 3/4-pin header:

- If the **metal pins are already sticking out** → nothing to solder, just plug
  jumper wires on. (Most of these kit modules ship pre-soldered.)
- If you see only **empty holes** → you must solder the little pin-header strip
  into those holes so you have something to connect to.

You do **not** need to solder anything on the breadboard or ESP32 — those use
plug-in jumper wires. So soldering is only *maybe* needed to attach headers to a
bare module. If all your modules already have pins, skip the iron entirely.

---

## 4. Understand the ESP32-S3 board layout

Hold the board with the **two USB-C ports pointing toward you (at the bottom)**,
antenna/shield at the top. There are two rows of pins:

- **Left row, top → bottom:** `GND, 3V3, RST, IO4, IO5, IO6, IO7, IO15, IO16,
  IO17, IO18, IO8, IO19, IO20, IO3, IO46, IO9, IO10, IO11, IO12, 3V3`
- **Right row, top → bottom:** `GND, IO1, IO2, TXD(43), RXD(44), IO42, IO41,
  IO40, IO39, IO38, IO37, IO36, IO35, IO0, IO45, IO48, IO47, IO21, IO14, IO13,
  5V`

Pins we will use (all easy to reach):

Everything lives on the **left header**, so the board can sit at the edge of a
narrow breadboard with only its left row exposed:

- `3V3` — top-left (2nd pin down). Power source for everything.
- `GND` — top-left (1st pin). Ground.
- `IO4`, `IO5`, `IO6`, `IO7` — upper-left → DHT11, buzzer, and I²C.
- `IO8`, `IO9` — lower-left → the two **analog** sensors.
- `IO48` — on-board RGB LED, **already wired on the PCB** (no jumper needed).

> **Why IO8 / IO9 for analog?** On ESP32-S3 only the **ADC1** pins (GPIO1–GPIO10)
> can read analog voltages *while Wi-Fi is on*. IO8 and IO9 are ADC1 channels that
> sit on the **left** header (unlike IO1/IO2 which are on the right), so we can keep
> the whole build on one side. Don't move the analog sensors to non-ADC1 pins.

---

## 5. The pin map (the single source of truth)

| Module | Module pin | Wire to ESP32-S3 | Notes |
|--------|-----------|-------------------|-------|
| **Power rail** | — | `3V3`  → breadboard **+** rail | one jumper |
| **Ground rail** | — | `GND`  → breadboard **−** rail | one jumper |
| DHT11 | `VCC` | **+** rail | |
| DHT11 | `GND` | **−** rail | |
| DHT11 | `DATA` | **IO4** | pull-up is on the module already |
| Light sensor | `VCC` | **+** rail | |
| Light sensor | `GND` | **−** rail | |
| Light sensor | `I/O` (analog out) | **IO8** | ADC1 — brightness |
| MQ-135 | `VCC` | **+** rail | |
| MQ-135 | `GND` | **−** rail | |
| MQ-135 | `AO` (analog out) | **IO9** | ADC1 — gas level |
| MQ-135 | `DO` | *leave empty* | not used (we read analog) |
| OLED | `VDD` / `VCC` | **+** rail | |
| OLED | `GND` | **−** rail | |
| OLED | `SDA` | **IO6** | I²C data |
| OLED | `SCK` / `SCL` | **IO7** | I²C clock (same pin, two names) |
| Buzzer | `VCC` | **+** rail | |
| Buzzer | `GND` | **−** rail | |
| Buzzer | `I/O` | **IO5** | HIGH = beep |
| RGB LED | (on board) | **IO48** | no wiring — window state |

That's **9 signal/power jumpers to the ESP32** + a bunch of short rail taps.

---

## 6. Step-by-step assembly

### Step 1 — Build the power rails
A breadboard has two long lines down each side, usually marked red `+` and blue
`−`. Pick the pair on one side.

1. Jumper from ESP32 **`3V3`** → breadboard **`+`** rail.
2. Jumper from ESP32 **`GND`** → breadboard **`−`** rail.

Now the whole `+` rail is 3.3V and the whole `−` rail is ground.

### Step 2 — Seat the modules
Push each module into the breadboard (or just leave them loose and use
male-female jumpers). For every module:

- its **VCC/VDD** pin → a jumper to the **`+`** rail
- its **GND** pin → a jumper to the **`−`** rail

Do this for all 5 modules (DHT11, light, MQ-135, OLED, buzzer).

### Step 3 — Wire the signal pins
One jumper each, straight to the GPIO:

- DHT11 `DATA`  → **IO4**
- Light `I/O`   → **IO8**
- MQ-135 `AO`   → **IO9**
- OLED `SDA`    → **IO6**
- OLED `SCK`    → **IO7**
- Buzzer `I/O`  → **IO5**

Leave MQ-135 `DO` unconnected.

### Step 4 — Verify BEFORE power (multimeter, see §7)
### Step 5 — Plug in USB (the UART/left USB-C port for programming)

---

## 7. Verify with your multimeter (do this before every power-on)

Set the meter to **continuity / resistance (Ω)** mode with USB **unplugged**:

1. **No short on power:** probe the **`+`** rail and the **`−`** rail. It should
   **NOT** beep (no continuity). If it beeps, you have a short — fix before
   powering.
2. **Each VCC reaches the rail:** probe a module's VCC pin and the `+` rail →
   should beep (connected).
3. **Each GND reaches the rail:** probe a module's GND pin and the `−` rail →
   should beep.

Then plug USB in and switch the meter to **DC Volts**:

4. Probe `+` rail (red) to `−` rail (black) → should read **~3.3V**.
5. If any module's VCC does not read ~3.3V, its rail jumper is loose.

If all four checks pass, wiring is safe and correct.

---

## 8. Power notes / FAQ

- **Which USB port?** Use the **left USB-C** (the UART / CH340 one) for flashing
  and normal use. It powers the board and carries the serial console.
- **Is 3.3V enough current?** Yes. Everything here draws well under the board
  regulator's limit when powered from USB. The MQ-135 heater is the biggest
  draw and is fine at 3.3V.
- **"But MQ-135 is usually 5V!"** True — 5V makes its heater run hotter and
  readings a bit more standard. But at 5V its `AO` can output up to 5V and
  **that would damage IO9**. For this course/demo, **3.3V is the safe, correct
  choice** and works fine. (Advanced: if you ever need 5V, you must add a
  resistor voltage divider on `AO` before IO9 — not required here.)
- **Buzzer too quiet?** It's louder on 5V. You *may* move only the buzzer's VCC
  to the `5V` pin (its `I/O` is just a logic input, so a 3.3V HIGH still triggers
  it safely). Optional. If unsure, leave it on 3.3V.
- **DHT11 needs a pull-up resistor?** Your module already has it on-board (the
  `R3` part). Don't add one.

---

## 9. Quick sanity map (cheat sheet)

```
ESP32-S3                       Modules (all VCC->+3V3 rail, all GND->- rail)
  3V3  ->  (+) rail
  GND  ->  (-) rail
  IO4  <----------------------- DHT11         DATA  (temp/humidity)
  IO5  ------------------------> Buzzer       I/O   (alarm, HIGH=beep)
  IO6  <---------------------->  OLED         SDA   (I2C data)
  IO7  ------------------------> OLED         SCK/SCL (I2C clock)
  IO8  <----------------------- Light sensor  I/O   (analog brightness)
  IO9  <----------------------- MQ-135        AO    (analog gas)
  IO48 (on-board RGB LED) = window state: green=open / amber=closed / blink red=alarm
```

All eight jumpers land on the **left** header, so the ESP32 can hug one edge of a
narrow breadboard.

Once this is wired and the multimeter checks pass, the firmware in
`sensor-new/` will drive exactly these pins.
