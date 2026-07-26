//! UART0 link to the host (GPIO43 TX / GPIO44 RX → CH340 → left USB-C).
//!
//! Carries only the `$BRZ2` protocol: we write telemetry/hello/ack lines out and
//! assemble inbound bytes into command lines. Log output goes to the separate USB
//! Serial/JTAG console (see `sdkconfig.defaults`), so this stream stays clean.

use std::collections::VecDeque;

use esp_idf_hal::delay::NON_BLOCK;
use esp_idf_hal::gpio::{InputPin, OutputPin};
use esp_idf_hal::peripheral::Peripheral;
use esp_idf_hal::sys::EspError;
use esp_idf_hal::uart::{config::Config, Uart, UartDriver};
use esp_idf_hal::units::Hertz;

use crate::proto::{parse_command, Command};

const RX_LINE_MAX: usize = 256;

pub struct UartLink<'d> {
    uart: UartDriver<'d>,
    rx_buf: Vec<u8>,
    read_chunk: [u8; 64],
    pending: VecDeque<Command>,
}

impl<'d> UartLink<'d> {
    pub fn new(
        uart: impl Peripheral<P = impl Uart> + 'd,
        tx: impl Peripheral<P = impl OutputPin> + 'd,
        rx: impl Peripheral<P = impl InputPin> + 'd,
    ) -> Result<Self, EspError> {
        let config = Config::new().baudrate(Hertz(115_200));
        let uart = UartDriver::new(
            uart,
            tx,
            rx,
            Option::<esp_idf_hal::gpio::AnyIOPin>::None,
            Option::<esp_idf_hal::gpio::AnyIOPin>::None,
            &config,
        )?;
        Ok(Self {
            uart,
            rx_buf: Vec::with_capacity(RX_LINE_MAX),
            read_chunk: [0u8; 64],
            pending: VecDeque::new(),
        })
    }

    /// Send one already-framed protocol line. Best-effort; write errors are dropped.
    pub fn write_line(&self, line: &str) {
        let _ = self.uart.write(line.as_bytes());
    }

    /// Non-blocking: pull any newly-arrived command. Call in a `while let` to drain.
    pub fn poll_command(&mut self) -> Option<Command> {
        if let Some(cmd) = self.pending.pop_front() {
            return Some(cmd);
        }
        if let Ok(n) = self.uart.read(&mut self.read_chunk, NON_BLOCK) {
            for i in 0..n {
                let b = self.read_chunk[i];
                match b {
                    b'\n' => {
                        let line = String::from_utf8_lossy(&self.rx_buf).into_owned();
                        self.rx_buf.clear();
                        if let Some(cmd) = parse_command(&line) {
                            self.pending.push_back(cmd);
                        }
                    }
                    b'\r' => {}
                    _ => {
                        if self.rx_buf.len() >= RX_LINE_MAX {
                            self.rx_buf.clear(); // runaway line without a newline; resync
                        }
                        self.rx_buf.push(b);
                    }
                }
            }
        }
        self.pending.pop_front()
    }
}
