//! Active buzzer on GPIO5 — high-level trigger (drive the pin HIGH to sound it).

use esp_idf_hal::delay::FreeRtos;
use esp_idf_hal::gpio::{AnyOutputPin, Output, PinDriver};
use esp_idf_hal::sys::EspError;

pub struct Buzzer<'d> {
    pin: PinDriver<'d, AnyOutputPin, Output>,
}

impl<'d> Buzzer<'d> {
    pub fn new(pin: AnyOutputPin) -> Result<Self, EspError> {
        let mut pin = PinDriver::output(pin)?;
        pin.set_low()?;
        Ok(Self { pin })
    }

    pub fn on(&mut self) -> Result<(), EspError> {
        self.pin.set_high()
    }

    pub fn off(&mut self) -> Result<(), EspError> {
        self.pin.set_low()
    }

    /// Short confirmation blip (used by the BUZZER,TEST command).
    pub fn beep(&mut self, ms: u32) -> Result<(), EspError> {
        self.on()?;
        FreeRtos::delay_ms(ms);
        self.off()
    }
}
