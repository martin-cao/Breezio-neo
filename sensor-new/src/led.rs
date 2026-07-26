//! On-board WS2812 RGB LED (GPIO48) driven over the RMT peripheral.
//!
//! Adapted from the WS2812/RMT pulse-timing approach in the TapTransit-gateway
//! reference project. We only ever show one pixel, so the API is just `set`.

use esp_idf_hal::gpio::OutputPin;
use esp_idf_hal::peripheral::Peripheral;
use esp_idf_hal::rmt::{
    config::TransmitConfig, FixedLengthSignal, PinState, Pulse, RmtChannel, TxRmtDriver,
};
use esp_idf_hal::sys::EspError;
use smart_leds::RGB8;

/// Dim the LED so it isn't blinding on a desk (~30%).
const BRIGHTNESS_SCALE: u16 = 77;

pub const OFF: RGB8 = RGB8 { r: 0, g: 0, b: 0 };
pub const GREEN: RGB8 = RGB8 { r: 0, g: 255, b: 0 };
pub const RED: RGB8 = RGB8 { r: 255, g: 0, b: 0 };
pub const AMBER: RGB8 = RGB8 {
    r: 255,
    g: 120,
    b: 0,
};

pub struct StatusLed<'d> {
    tx: TxRmtDriver<'d>,
}

impl<'d> StatusLed<'d> {
    pub fn new<C, P, Ch, Pin>(channel: C, pin: P) -> Result<Self, EspError>
    where
        C: Peripheral<P = Ch> + 'd,
        P: Peripheral<P = Pin> + 'd,
        Ch: RmtChannel,
        Pin: OutputPin,
    {
        let config = TransmitConfig::new().clock_divider(1);
        let tx = TxRmtDriver::new(channel, pin, &config)?;
        Ok(Self { tx })
    }

    /// Show a solid color.
    pub fn set(&mut self, color: RGB8) -> Result<(), EspError> {
        let color = scale(color);
        let grb: u32 = ((color.g as u32) << 16) | ((color.r as u32) << 8) | color.b as u32;

        let ticks_hz = self.tx.counter_clock()?;
        let (t0h, t0l, t1h, t1l) = (
            Pulse::new_with_duration(
                ticks_hz,
                PinState::High,
                &std::time::Duration::from_nanos(350),
            )?,
            Pulse::new_with_duration(
                ticks_hz,
                PinState::Low,
                &std::time::Duration::from_nanos(800),
            )?,
            Pulse::new_with_duration(
                ticks_hz,
                PinState::High,
                &std::time::Duration::from_nanos(700),
            )?,
            Pulse::new_with_duration(
                ticks_hz,
                PinState::Low,
                &std::time::Duration::from_nanos(600),
            )?,
        );

        let mut signal = FixedLengthSignal::<24>::new();
        for i in 0..24 {
            let bit = (grb & (1 << (23 - i))) != 0;
            let (hi, lo) = if bit { (t1h, t1l) } else { (t0h, t0l) };
            signal.set(i, &(hi, lo))?;
        }
        self.tx.start_blocking(&signal)
    }
}

fn scale(color: RGB8) -> RGB8 {
    let apply = |v: u8| ((v as u16 * BRIGHTNESS_SCALE) / 255) as u8;
    RGB8 {
        r: apply(color.r),
        g: apply(color.g),
        b: apply(color.b),
    }
}
