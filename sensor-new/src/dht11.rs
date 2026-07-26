//! Minimal DHT11 single-wire driver (bit-banged).
//!
//! The DHT11 speaks a 40-bit one-wire protocol: the host pulls the line low for
//! ~18 ms, releases it, and the sensor answers with 40 bits where a bit's value is
//! encoded in the *length* of its high pulse (~26 µs = 0, ~70 µs = 1). We time the
//! pulses with `esp_timer_get_time()` (1 µs resolution) rather than disabling
//! interrupts, and simply retry on the occasional corrupted read.
//!
//! The chip is only rated for 0..50 C / 20..90% RH; there is no calibrated
//! negative-temperature mode, so readings outside a generous envelope around that
//! range are rejected as a bad frame rather than reported (see `Dht11Error::Implausible`).

use esp_idf_hal::delay::Ets;
use esp_idf_hal::gpio::{AnyIOPin, InputOutput, PinDriver};
use esp_idf_hal::sys::esp_timer_get_time;

/// A decoded DHT11 sample.
#[derive(Clone, Copy, Debug)]
pub struct Reading {
    pub temperature_c: f32,
    pub humidity_pct: f32,
}

#[derive(Debug)]
pub enum Dht11Error {
    /// The line never reached an expected level in time.
    Timeout,
    /// The 8-bit sum check over the four data bytes did not match.
    Checksum,
    /// A GPIO operation failed.
    Gpio,
    /// Checksum passed but the decoded value falls outside anything the DHT11
    /// could plausibly report (spec range is 0..50 C, 20..90% RH); almost always
    /// a bit-timing misread near the edge of that range rather than real data.
    Implausible,
}

pub struct Dht11<'d> {
    pin: PinDriver<'d, AnyIOPin, InputOutput>,
}

impl<'d> Dht11<'d> {
    /// Take ownership of the data pin as an open-drain in/out line.
    pub fn new(pin: AnyIOPin) -> Result<Self, Dht11Error> {
        let pin = PinDriver::input_output_od(pin).map_err(|_| Dht11Error::Gpio)?;
        let mut me = Self { pin };
        // Idle high (released) between reads.
        me.pin.set_high().map_err(|_| Dht11Error::Gpio)?;
        Ets::delay_ms(1);
        Ok(me)
    }

    /// Perform one read, retrying a few times before giving up.
    pub fn read(&mut self) -> Result<Reading, Dht11Error> {
        let mut last = Dht11Error::Timeout;
        for _ in 0..3 {
            match self.read_once() {
                Ok(reading) => return Ok(reading),
                Err(err) => {
                    last = err;
                    // DHT11 needs ~1 s between reads; keep it modest for retries.
                    Ets::delay_ms(60);
                }
            }
        }
        Err(last)
    }

    fn read_once(&mut self) -> Result<Reading, Dht11Error> {
        // --- Start signal: hold low >=18 ms, then release. ---
        self.pin.set_low().map_err(|_| Dht11Error::Gpio)?;
        Ets::delay_ms(20);
        self.pin.set_high().map_err(|_| Dht11Error::Gpio)?;
        Ets::delay_us(30);

        // --- Sensor response: ~80 µs low, ~80 µs high. ---
        self.wait_level(false, 100)?;
        self.wait_level(true, 100)?;

        // --- 40 data bits. Each begins with ~50 µs low, then a high pulse whose
        // length is the bit value. ---
        let mut bytes = [0u8; 5];
        for i in 0..40 {
            self.wait_level(false, 100)?; // trailing low of the previous bit / start of this one
            self.wait_level(true, 100)?; // rising edge -> high pulse begins
            let high_us = self.wait_level(false, 100)?; // measure the high pulse
            let bit = u8::from(high_us > 45); // >45 µs -> '1'
            bytes[i / 8] = (bytes[i / 8] << 1) | bit;
        }

        let sum = bytes[0]
            .wrapping_add(bytes[1])
            .wrapping_add(bytes[2])
            .wrapping_add(bytes[3]);
        if sum != bytes[4] {
            return Err(Dht11Error::Checksum);
        }

        // DHT11 carries integers in bytes[0]/[2]; the fractional bytes are usually 0.
        let humidity = bytes[0] as f32 + bytes[1] as f32 * 0.1;

        // The DHT11 is only rated 0..50 C and has no official negative-temperature
        // mode, but the commonly-cloned chip signals "negative" via the top bit of
        // the decimal byte, with the magnitude one less than the integer byte (e.g.
        // -1 C is sent as integer=0, sign bit set) — the same decode the reference
        // Adafruit DHT driver uses. Below zero is out of spec either way, so this is
        // best-effort recovery, not a calibrated reading.
        let temperature = if bytes[3] & 0x80 != 0 {
            -1.0 - bytes[2] as f32
        } else {
            bytes[2] as f32 + (bytes[3] & 0x0F) as f32 * 0.1
        };

        // Defense in depth: reject anything outside a generous envelope around the
        // sensor's rated range instead of silently reporting a bit-misread value
        // (this is what previously showed as a bogus "51 C, then counting down").
        if !(-30.0..=60.0).contains(&temperature) || !(0.0..=100.0).contains(&humidity) {
            return Err(Dht11Error::Implausible);
        }

        Ok(Reading {
            temperature_c: temperature,
            humidity_pct: humidity,
        })
    }

    /// Busy-wait until the line reaches `level`, returning the elapsed microseconds,
    /// or `Timeout` if `timeout_us` passes first.
    fn wait_level(&self, level: bool, timeout_us: i64) -> Result<i64, Dht11Error> {
        let start = now_us();
        loop {
            let elapsed = now_us() - start;
            if elapsed > timeout_us {
                return Err(Dht11Error::Timeout);
            }
            if self.pin.is_high() == level {
                return Ok(elapsed);
            }
        }
    }
}

fn now_us() -> i64 {
    // Safe: reads a free-running monotonic timer with no side effects.
    unsafe { esp_timer_get_time() }
}
