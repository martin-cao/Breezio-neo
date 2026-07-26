//! Hardware bring-up self-test. Flash this FIRST to confirm each peripheral and
//! its wiring, before running the real firmware:
//!
//!     cargo run --release --bin selftest
//!
//! Watch the CH340 serial monitor. Each component announces itself with a log line
//! plus physical feedback (LED colors, buzzer beeps, OLED text). Then the sensors
//! stream once a second so you can watch values react in real time — cover the
//! photoresistor and the brightness drops; breathe on the MQ-135 and its mV rises.
//!
//! It uses the raw peripheral drivers directly (only the DHT11 bit-bang, the LED
//! and the buzzer helpers are shared), so a PASS here means the *hardware* is good,
//! independent of the main firmware's logic.

#[path = "../buzzer.rs"]
mod buzzer;
#[path = "../dht11.rs"]
mod dht11;
#[path = "../led.rs"]
mod led;
#[path = "../sensors.rs"]
mod sensors;

use std::thread;
use std::time::Duration;

use embedded_graphics::{
    mono_font::{ascii::FONT_6X10, MonoTextStyle},
    pixelcolor::BinaryColor,
    prelude::*,
    text::{Baseline, Text},
};
use esp_idf_hal::adc::attenuation::DB_11;
use esp_idf_hal::adc::oneshot::config::AdcChannelConfig;
use esp_idf_hal::adc::oneshot::{AdcChannelDriver, AdcDriver};
use esp_idf_hal::gpio::{IOPin, OutputPin};
use esp_idf_hal::i2c::{I2cConfig, I2cDriver};
use esp_idf_hal::peripherals::Peripherals;
use esp_idf_hal::units::Hertz;
use smart_leds::RGB8;
use ssd1306::mode::BufferedGraphicsMode;
use ssd1306::prelude::*;
use ssd1306::size::DisplaySize128x64;
use ssd1306::{I2CDisplayInterface, Ssd1306};

use buzzer::Buzzer;
use dht11::Dht11;
use led::{StatusLed, AMBER, GREEN, OFF, RED};
use sensors::{aq_band, light_percent};

const BLUE: RGB8 = RGB8 { r: 0, g: 0, b: 255 };

type Oled<'d> = Ssd1306<
    I2CInterface<I2cDriver<'d>>,
    DisplaySize128x64,
    BufferedGraphicsMode<DisplaySize128x64>,
>;

fn draw_lines(disp: &mut Oled, lines: &[&str]) {
    let style = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);
    let _ = disp.clear(BinaryColor::Off);
    for (i, text) in lines.iter().enumerate() {
        let _ = Text::with_baseline(text, Point::new(0, (i as i32) * 14), style, Baseline::Top)
            .draw(disp);
    }
    let _ = disp.flush();
}

fn main() -> anyhow::Result<()> {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();
    log::info!("========================================");
    log::info!("   Breezio sensor-new  SELF-TEST");
    log::info!("========================================");

    let p = Peripherals::take()?;
    let pins = p.pins;

    // --- [1/6] RGB LED on GPIO48 ---
    log::info!("[1/6] RGB LED (GPIO48): cycling red -> green -> blue -> amber -> off");
    let mut led = match StatusLed::new(p.rmt.channel0, pins.gpio48) {
        Ok(mut led) => {
            for (name, color) in [
                ("red", RED),
                ("green", GREEN),
                ("blue", BLUE),
                ("amber", AMBER),
            ] {
                log::info!("      LED -> {name}");
                let _ = led.set(color);
                thread::sleep(Duration::from_millis(600));
            }
            let _ = led.set(OFF);
            log::info!("      LED PASS (did you see 4 colors?)");
            Some(led)
        }
        Err(e) => {
            log::error!("      LED FAIL: {e:?}");
            None
        }
    };

    // --- [2/6] Buzzer on GPIO5 ---
    log::info!("[2/6] Buzzer (GPIO5): two short beeps");
    match Buzzer::new(pins.gpio5.downgrade_output()) {
        Ok(mut bz) => {
            for _ in 0..2 {
                let _ = bz.beep(120);
                thread::sleep(Duration::from_millis(180));
            }
            log::info!("      Buzzer PASS (did you hear 2 beeps?)");
        }
        Err(e) => log::error!("      Buzzer FAIL: {e:?}"),
    }

    // --- [3/6] OLED (SSD1306) on I2C0: SDA=GPIO6, SCL=GPIO7 ---
    log::info!("[3/6] OLED (GPIO6=SDA, GPIO7=SCL): scanning I2C bus then drawing");
    let mut i2c = I2cDriver::new(
        p.i2c0,
        pins.gpio6,
        pins.gpio7,
        // 100 kHz is more tolerant of weak pull-ups / longer breadboard wires.
        &I2cConfig::new().baudrate(Hertz(100_000)),
    )?;

    // Probe every address; a device that ACKs a 1-byte write is present.
    let mut found: Vec<u8> = Vec::new();
    for addr in 0x03u8..0x78 {
        if i2c.write(addr, &[0x00], 50).is_ok() {
            found.push(addr);
        }
    }
    if found.is_empty() {
        log::error!("      I2C scan: NO devices found — check OLED VDD/GND and that SDA=GPIO6, SCL=GPIO7 (not swapped)");
    } else {
        let list: Vec<String> = found.iter().map(|a| format!("0x{a:02X}")).collect();
        log::info!("      I2C scan: found {}", list.join(", "));
    }

    let interface = I2CDisplayInterface::new(i2c);
    let mut disp: Option<Oled> = {
        let mut d = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0)
            .into_buffered_graphics_mode();
        match d.init() {
            Ok(()) => {
                draw_lines(&mut d, &["BREEZIO", "SELF-TEST", "OLED OK"]);
                log::info!("      OLED PASS (screen should show text)");
                Some(d)
            }
            Err(e) => {
                log::error!("      OLED FAIL: {e:?} (check wiring / addr 0x3C)");
                None
            }
        }
    };

    // --- [4/6][5/6][6/6] Sensors: stream every second ---
    log::info!("[4/6] DHT11 (GPIO4), [5/6] light (GPIO8), [6/6] MQ-135 (GPIO9)");
    log::info!("      streaming every 1 s — cover the light sensor / breathe on the MQ-135 to see it react");

    let adc = AdcDriver::new(p.adc1)?;
    let adc_cfg = AdcChannelConfig {
        attenuation: DB_11,
        ..Default::default()
    };
    let mut light_ch = AdcChannelDriver::new(&adc, pins.gpio8, &adc_cfg)?;
    let mut gas_ch = AdcChannelDriver::new(&adc, pins.gpio9, &adc_cfg)?;
    let mut dht = match Dht11::new(pins.gpio4.downgrade()) {
        Ok(d) => Some(d),
        Err(e) => {
            log::error!("      DHT11 init FAIL: {e:?}");
            None
        }
    };

    loop {
        let (temp, hum) = match dht.as_mut().map(|d| d.read()) {
            Some(Ok(r)) => (Some(r.temperature_c), Some(r.humidity_pct)),
            Some(Err(e)) => {
                log::warn!("      DHT11 read failed: {e:?}");
                (None, None)
            }
            None => (None, None),
        };

        let light_mv = adc.read(&mut light_ch).unwrap_or(0);
        let gas_mv = adc.read(&mut gas_ch).unwrap_or(0);
        let lp = light_percent(light_mv);

        let temp_s = temp
            .map(|v| format!("{:.1}C", v))
            .unwrap_or_else(|| "--".into());
        let hum_s = hum
            .map(|v| format!("{:.1}%", v))
            .unwrap_or_else(|| "--".into());
        log::info!(
            "DHT T={temp_s} H={hum_s} | light={light_mv}mV ({lp}%) | MQ135={gas_mv}mV ({})",
            aq_band(gas_mv)
        );

        if let Some(d) = disp.as_mut() {
            draw_lines(
                d,
                &[
                    "SELF-TEST",
                    &format!("T:{temp_s} H:{hum_s}"),
                    &format!("Light:{light_mv}mV {lp}%"),
                    &format!("MQ:{gas_mv}mV {}", aq_band(gas_mv)),
                ],
            );
        }

        // Reflect brightness on the LED so the light sensor has visible feedback too.
        if let Some(l) = led.as_mut() {
            let _ = l.set(if lp > 50 { GREEN } else { BLUE });
        }

        thread::sleep(Duration::from_secs(1));
    }
}
