//! Breezio "smart window" node — ESP32-S3 firmware.
//!
//! One board reads temperature/humidity (DHT11), brightness (photoresistor) and
//! air quality (MQ-135); shows a status screen (OLED); alarms on bad air (buzzer +
//! red RGB LED); and pretends to be a window the host can open/close (RGB LED +
//! OLED). The primary deployment carries `$BRZ2` telemetry and commands over
//! Wi-Fi/TCP; UART0 exposes the same protocol as a local diagnostic and fallback
//! link. See `docs/WIRING.md` and `docs/PROTOCOL.md`.

mod app;
mod buzzer;
mod dht11;
#[cfg(feature = "wifi")]
mod discovery;
mod display;
mod led;
mod link;
#[cfg(feature = "wifi")]
mod net_link;
mod proto;
mod sensors;
#[cfg(feature = "wifi")]
mod wifi;

use std::time::{Duration, Instant};

use esp_idf_hal::adc::attenuation::DB_11;
use esp_idf_hal::adc::oneshot::config::AdcChannelConfig;
use esp_idf_hal::adc::oneshot::{AdcChannelDriver, AdcDriver};
use esp_idf_hal::delay::FreeRtos;
use esp_idf_hal::gpio::{IOPin, OutputPin};
use esp_idf_hal::i2c::{I2cConfig, I2cDriver};
use esp_idf_hal::peripherals::Peripherals;
use esp_idf_hal::units::Hertz;

use app::AppState;
#[cfg(feature = "wifi")]
use app::WifiStatus;
use buzzer::Buzzer;
use dht11::Dht11;
use display::StatusScreen;
use led::{StatusLed, AMBER, GREEN, OFF, RED};
use link::UartLink;
use proto::{encode_ack, encode_hello, Action, Command, Device, Telemetry};
use sensors::{light_percent, AQ_ALARM_MV, AQ_CLEAR_MV};

const FW_VERSION: &str = "1.0.0";
const NODE_ID: &str = "win-01";
// 1s is the DHT11's minimum reliable read spacing (dht11.rs); this is the
// fastest the sensor->gateway->backend chain can report.
const SAMPLE_INTERVAL: Duration = Duration::from_secs(1);
const LOOP_TICK_MS: u32 = 50;

fn main() -> anyhow::Result<()> {
    // Board bring-up: apply runtime patches and route `log` to the console.
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();
    log::info!("Breezio sensor-new {FW_VERSION} starting");

    let peripherals = Peripherals::take()?;
    let pins = peripherals.pins;

    // --- Analog sensors on ADC1 (Wi-Fi-safe channels). ---
    let adc = AdcDriver::new(peripherals.adc1)?;
    // Only set attenuation; leave the rest at defaults so this compiles across
    // esp-idf-hal 0.45.x patch versions (the `calibration` field's type changed).
    // `adc.read()` returns millivolts.
    let adc_cfg = AdcChannelConfig {
        attenuation: DB_11, // ~0..3.3 V range
        ..Default::default()
    };
    let mut light_ch = AdcChannelDriver::new(&adc, pins.gpio8, &adc_cfg)?; // photoresistor AO
    let mut gas_ch = AdcChannelDriver::new(&adc, pins.gpio9, &adc_cfg)?; // MQ-135 AO

    // --- DHT11 on GPIO4 (module carries its own pull-up). ---
    let mut dht = match Dht11::new(pins.gpio4.downgrade()) {
        Ok(d) => Some(d),
        Err(e) => {
            log::warn!("DHT11 init failed ({e:?}); temp/humidity will report NA");
            None
        }
    };

    // --- Buzzer on GPIO5 (active, high-level trigger). ---
    let mut buzzer = Buzzer::new(pins.gpio5.downgrade_output())?;

    // --- OLED (SSD1306) on I2C0: SDA=GPIO6, SCL=GPIO7. ---
    let i2c = I2cDriver::new(
        peripherals.i2c0,
        pins.gpio6,
        pins.gpio7,
        &I2cConfig::new().baudrate(Hertz(400_000)),
    )?;
    let mut screen = match StatusScreen::new(i2c) {
        Ok(s) => Some(s),
        Err(()) => {
            log::warn!("OLED init failed; continuing headless");
            None
        }
    };

    // --- On-board WS2812 RGB LED on GPIO48 (window-state indicator). ---
    let mut led = StatusLed::new(peripherals.rmt.channel0, pins.gpio48)?;
    let _ = led.set(AMBER); // window starts CLOSED

    let mut state = AppState::new();

    // --- Host link on UART0: TX=GPIO43, RX=GPIO44 (CH340 / left USB-C). ---
    let mut link = UartLink::new(peripherals.uart0, pins.gpio43, pins.gpio44)?;
    let hello_line = encode_hello(FW_VERSION, NODE_ID);
    link.write_line(&hello_line);

    // --- Wi-Fi/TCP transport (feature = "wifi"): same $BRZ2 lines over LAN.
    // The node joins the Wi-Fi itself (DHCP; primary SSID, then the 2.4 GHz
    // fallback), then actively connects to the gateway TCP listener.
    #[cfg(feature = "wifi")]
    let _ = {
        state.wifi_status = WifiStatus::Connecting;
        render_status_screen(&mut screen, &state);
    };
    #[cfg(feature = "wifi")]
    let _wifi = match wifi::connect(peripherals.modem) {
        Ok(connection) => {
            state.wifi_status = WifiStatus::Connected {
                ssid: connection.ssid,
                ip: connection.ip.clone(),
            };
            render_status_screen(&mut screen, &state);
            Some(connection.driver)
        }
        Err(e) => {
            log::warn!("Wi-Fi disabled after connection failure: {e:?}");
            state.wifi_status = WifiStatus::Failed;
            render_status_screen(&mut screen, &state);
            None
        }
    };
    #[cfg(feature = "wifi")]
    let mut net = if _wifi.is_some() {
        Some(net_link::NetLink::connect(
            net_link::gateway_host(),
            net_link::gateway_port(),
            &hello_line,
        ))
    } else {
        None
    };
    let mut last_sample = Instant::now() - SAMPLE_INTERVAL; // sample immediately
    let mut blink_tick: u32 = 0;
    let mut blink_on = false;

    loop {
        // 1) Service any inbound commands promptly (UART and, if enabled, TCP).
        // ACKs are broadcast on both transports so either host sees them.
        loop {
            let cmd = link.poll_command();
            #[cfg(feature = "wifi")]
            let cmd = cmd.or_else(|| net.as_mut().and_then(|net| net.poll_command()));
            let Some(cmd) = cmd else { break };
            let ack = handle_command(cmd, &mut state, &mut buzzer, &mut led);
            link.write_line(&ack);
            #[cfg(feature = "wifi")]
            if let Some(net) = net.as_mut() {
                net.write_line(&ack);
            }
            if let Some(s) = screen.as_mut() {
                s.render(&state);
            }
        }

        // 2) Sample sensors + report on a fixed cadence.
        if last_sample.elapsed() >= SAMPLE_INTERVAL {
            last_sample = Instant::now();
            state.seq = state.seq.wrapping_add(1);

            match dht.as_mut().map(|d| d.read()) {
                Some(Ok(r)) => {
                    state.temp_c = Some(r.temperature_c);
                    state.hum_pct = Some(r.humidity_pct);
                }
                Some(Err(e)) => {
                    log::warn!("DHT11 read failed: {e:?}");
                    state.temp_c = None;
                    state.hum_pct = None;
                }
                None => {}
            }

            state.light_pct = light_percent(adc.read(&mut light_ch).unwrap_or(0));
            state.aq_mv = adc.read(&mut gas_ch).unwrap_or(0);

            evaluate_alarm(&mut state);
            update_indicators(&state, &mut buzzer, &mut led);
            if let Some(s) = screen.as_mut() {
                s.render(&state);
            }

            let telemetry_line = Telemetry {
                seq: state.seq,
                temp_c: state.temp_c,
                hum_pct: state.hum_pct,
                light_pct: state.light_pct,
                aq_mv: state.aq_mv,
                window_open: state.window_open,
                alarm: state.alarm,
            }
            .encode();
            link.write_line(&telemetry_line);
            #[cfg(feature = "wifi")]
            if let Some(net) = net.as_mut() {
                net.write_line(&telemetry_line);
            }
        }

        // 3) Blink the LED red while the air-quality alarm is active.
        if state.alarm {
            blink_tick += 1;
            if blink_tick % 10 == 0 {
                blink_on = !blink_on;
                let _ = led.set(if blink_on { RED } else { OFF });
            }
        }

        FreeRtos::delay_ms(LOOP_TICK_MS);
    }
}

/// Arm/disarm the air-quality alarm with hysteresis and mute handling.
fn evaluate_alarm(state: &mut AppState) {
    if state.aq_mv >= AQ_ALARM_MV {
        if !state.muted {
            state.alarm = true;
        }
    } else if state.aq_mv < AQ_CLEAR_MV {
        // Air recovered: clear the alarm and re-arm a previously muted one.
        state.alarm = false;
        state.muted = false;
    }
}

/// Reflect current state on the LED and buzzer.
/// LED priority: alarm (red) > window open (green) > window closed (amber).
fn update_indicators(state: &AppState, buzzer: &mut Buzzer, led: &mut StatusLed) {
    let color = if state.alarm {
        RED
    } else if state.window_open {
        GREEN
    } else {
        AMBER
    };
    let _ = led.set(color);
    let _ = if state.alarm {
        buzzer.on()
    } else {
        buzzer.off()
    };
}

#[cfg(feature = "wifi")]
fn render_status_screen(screen: &mut Option<StatusScreen<'_>>, state: &AppState) {
    if let Some(s) = screen.as_mut() {
        s.render(state);
    }
}

/// Apply a host command and return the matching ACK line for the caller to
/// send on every active transport.
fn handle_command(
    cmd: Command,
    state: &mut AppState,
    buzzer: &mut Buzzer,
    led: &mut StatusLed,
) -> String {
    log::info!("command: {:?} {:?} (id={})", cmd.device, cmd.action, cmd.id);
    let (ok, resulting_state): (bool, &str) = match (cmd.device, cmd.action) {
        (Device::Window, Action::Open) => {
            state.window_open = true;
            (true, "OPEN")
        }
        (Device::Window, Action::Close) => {
            state.window_open = false;
            (true, "CLOSED")
        }
        (Device::Window, Action::Toggle) => {
            state.window_open = !state.window_open;
            (true, state.window_str())
        }
        (Device::Buzzer, Action::Mute) => {
            state.muted = true;
            state.alarm = false;
            (true, "MUTED")
        }
        (Device::Buzzer, Action::Test) => {
            let _ = buzzer.beep(120);
            (true, "TEST")
        }
        // Actions not valid for the device (e.g. WINDOW,MUTE) never decode into a
        // Command, so this arm is unreachable in practice — reply ERR defensively.
        _ => (false, "INVALID"),
    };

    update_indicators(state, buzzer, led);
    encode_ack(cmd.id, cmd.device, ok, resulting_state)
}
