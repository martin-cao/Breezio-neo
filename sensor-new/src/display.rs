//! 0.96" SSD1306 OLED (128x64, I2C) status screen.
//!
//! Best-effort: if the panel is missing or the bus glitches, drawing calls are
//! swallowed so the node keeps sensing and reporting regardless.

use embedded_graphics::{
    mono_font::{ascii::FONT_6X10, MonoTextStyle},
    pixelcolor::BinaryColor,
    prelude::*,
    text::{Baseline, Text},
};
use esp_idf_hal::i2c::I2cDriver;
use ssd1306::mode::BufferedGraphicsMode;
use ssd1306::prelude::*;
use ssd1306::size::DisplaySize128x64;
use ssd1306::{I2CDisplayInterface, Ssd1306};

use crate::app::AppState;

type Oled<'d> = Ssd1306<
    I2CInterface<I2cDriver<'d>>,
    DisplaySize128x64,
    BufferedGraphicsMode<DisplaySize128x64>,
>;

pub struct StatusScreen<'d> {
    display: Oled<'d>,
}

impl<'d> StatusScreen<'d> {
    /// Initialize the panel at its default I2C address (0x3C). Returns `Err(())`
    /// if init fails so the caller can log and carry on without a screen.
    pub fn new(i2c: I2cDriver<'d>) -> Result<Self, ()> {
        let interface = I2CDisplayInterface::new(i2c);
        let mut display = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0)
            .into_buffered_graphics_mode();
        display.init().map_err(|_| ())?;
        Ok(Self { display })
    }

    /// Redraw the whole screen from current state.
    pub fn render(&mut self, state: &AppState) {
        let style = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);
        let temp = state
            .temp_c
            .map(|v| format!("{:.1}C", v))
            .unwrap_or_else(|| "--.-C".into());
        let hum = state
            .hum_pct
            .map(|v| format!("{:.1}%", v))
            .unwrap_or_else(|| "--.-%".into());

        let line2 = format!("T:{}  H:{}", temp, hum);
        let line3 = format!("Light:{:>3} AQ:{}mV", state.light_pct, state.aq_mv);
        let line4 = format!(
            "Win:{}{}",
            state.window_str(),
            if state.alarm { "  !ALARM" } else { "" }
        );
        let wifi = state.wifi_status_line();

        let _ = self.display.clear(BinaryColor::Off);
        for (i, text) in ["Breezio Window", &line2, &line3, &line4, &wifi]
            .iter()
            .enumerate()
        {
            let y = (i as i32) * 13;
            let _ = Text::with_baseline(text, Point::new(0, y), style, Baseline::Top)
                .draw(&mut self.display);
        }
        let _ = self.display.flush();
    }
}
