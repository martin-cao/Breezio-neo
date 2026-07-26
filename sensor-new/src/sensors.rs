//! Analog-sensor conversions and alarm thresholds.
//!
//! The ADC drivers themselves live in `main` (their lifetimes borrow the shared
//! `AdcDriver`), so this module only holds the pure math and the tunable limits —
//! the numbers you'd tweak while calibrating on the bench.

/// ESP32-S3 ADC full-scale at 11 dB attenuation (~0..3.3 V) in millivolts.
pub const ADC_FULLSCALE_MV: u16 = 3300;

/// Set `true` if brighter light makes the photoresistor module output a *lower*
/// voltage (depends on which leg of its divider the LDR sits on). Flip this one
/// constant if the brightness reads backwards on your board.
pub const LIGHT_INVERTED: bool = false;

/// MQ-135 air-quality alarm thresholds, in raw millivolts on the `AO` pin.
/// Higher voltage = more gas. `ALARM` arms the buzzer; `CLEAR` (with hysteresis)
/// disarms it and re-arms a muted alarm. Calibrate against clean-air baseline.
pub const AQ_ALARM_MV: u16 = 2000;
pub const AQ_CLEAR_MV: u16 = 1600;

/// Map a photoresistor voltage (mV) to a 0..100 raw brightness index.
///
/// This is **not calibrated lux** — it's a linear rescale of the raw ADC voltage.
/// Turning it into real lux would need a per-board calibration measurement against
/// a reference light meter, which nobody has done here.
pub fn light_percent(mv: u16) -> u8 {
    let mv = mv.min(ADC_FULLSCALE_MV);
    let pct = (mv as u32 * 100 / ADC_FULLSCALE_MV as u32) as u8;
    if LIGHT_INVERTED {
        100 - pct
    } else {
        pct
    }
}

/// Coarse air-quality band (used by the selftest debug screen and the gateway's
/// JSON summary), since the MQ-135 is uncalibrated. The main status screen shows
/// the raw millivolt reading instead — see `display.rs`.
pub fn aq_band(mv: u16) -> &'static str {
    if mv >= AQ_ALARM_MV {
        "POOR"
    } else if mv >= AQ_CLEAR_MV {
        "FAIR"
    } else {
        "GOOD"
    }
}
