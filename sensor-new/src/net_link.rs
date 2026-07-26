//! Wi-Fi TCP link (feature = "wifi"): the same `$BRZ2` lines as UART0, sent over
//! a sensor-initiated TCP connection to the gateway. Command lines arrive on the
//! same connection.
//!
//! Mirrors `link::UartLink`'s API (`write_line` / `poll_command`) so `main.rs`
//! can treat both transports identically. The link reconnects in the background
//! from the main loop; telemetry is best-effort like UART logging.

use std::collections::VecDeque;
use std::io::{ErrorKind, Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

use crate::discovery;
use crate::proto::{parse_command, Command};

/// Default `$BRZ2`-over-TCP gateway listen port.
pub const PORT: u16 = 5568;

const RX_LINE_MAX: usize = 256;
const RECONNECT_DELAY: Duration = Duration::from_secs(3);

pub struct NetLink {
    fallback_host: String,
    fallback_port: u16,
    hello_line: String,
    stream: Option<TcpStream>,
    rx_buf: Vec<u8>,
    read_chunk: [u8; 128],
    pending: VecDeque<Command>,
    next_attempt: Instant,
}

impl NetLink {
    /// `fallback_host`/`fallback_port` are used when mDNS discovery of
    /// `_breezio-gateway._tcp.local` fails or times out.
    pub fn connect(fallback_host: &str, fallback_port: u16, hello_line: &str) -> Self {
        let mut link = Self {
            fallback_host: fallback_host.to_string(),
            fallback_port,
            hello_line: hello_line.to_string(),
            stream: None,
            rx_buf: Vec::with_capacity(RX_LINE_MAX),
            read_chunk: [0u8; 128],
            pending: VecDeque::new(),
            next_attempt: Instant::now(),
        };
        link.ensure_connected();
        link
    }

    /// Send one framed protocol line to the gateway.
    /// Best-effort like the UART: failed writes drop the socket and retry later.
    pub fn write_line(&mut self, line: &str) {
        if self.ensure_connected().is_none() {
            return;
        }
        let keep = match self.stream.as_mut().unwrap().write_all(line.as_bytes()) {
            Ok(()) => true,
            Err(e) if e.kind() == ErrorKind::WouldBlock => true,
            Err(e) => {
                log::warn!("gateway TCP write failed: {e:?}");
                false
            }
        };
        if !keep {
            self.drop_stream();
        }
    }

    /// Non-blocking: keep the gateway connection alive and pull any command.
    pub fn poll_command(&mut self) -> Option<Command> {
        if let Some(cmd) = self.pending.pop_front() {
            return Some(cmd);
        }
        if self.ensure_connected().is_none() {
            return None;
        }
        loop {
            match self.stream.as_mut().unwrap().read(&mut self.read_chunk) {
                Ok(0) => {
                    self.drop_stream();
                    return None;
                }
                Ok(n) => {
                    for &b in &self.read_chunk[..n] {
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
                                    self.rx_buf.clear(); // runaway line; resync
                                }
                                self.rx_buf.push(b);
                            }
                        }
                    }
                }
                Err(e) if e.kind() == ErrorKind::WouldBlock => return self.pending.pop_front(),
                Err(e) => {
                    log::warn!("gateway TCP read failed: {e:?}");
                    self.drop_stream();
                    return self.pending.pop_front();
                }
            }
        }
    }

    fn ensure_connected(&mut self) -> Option<()> {
        if self.stream.is_some() {
            return Some(());
        }
        if Instant::now() < self.next_attempt {
            return None;
        }
        let (host, port) = match discovery::discover_gateway() {
            Some(gateway) => (gateway.host, gateway.port),
            None => (self.fallback_host.clone(), self.fallback_port),
        };
        match TcpStream::connect((host.as_str(), port)) {
            Ok(stream) => {
                if let Err(e) = stream.set_nonblocking(true) {
                    log::warn!("gateway TCP nonblocking setup failed: {e:?}");
                    self.next_attempt = Instant::now() + RECONNECT_DELAY;
                    return None;
                }
                log::info!("connected to gateway at {host}:{port}");
                self.stream = Some(stream);
                let hello_result = self
                    .stream
                    .as_mut()
                    .map(|stream| stream.write_all(self.hello_line.as_bytes()));
                if let Some(Err(e)) = hello_result {
                    log::warn!("gateway TCP hello failed: {e:?}");
                    self.drop_stream();
                    return None;
                }
                Some(())
            }
            Err(e) => {
                log::warn!("cannot connect to gateway at {host}:{port} ({e:?}); retrying");
                self.next_attempt = Instant::now() + RECONNECT_DELAY;
                None
            }
        }
    }

    fn drop_stream(&mut self) {
        self.stream = None;
        self.rx_buf.clear();
        self.next_attempt = Instant::now() + RECONNECT_DELAY;
    }
}

/// Fallback gateway host, used only when mDNS discovery of
/// `_breezio-gateway._tcp.local` (see `discovery.rs`) fails or times out.
pub fn gateway_host() -> &'static str {
    option_env!("BREEZIO_GATEWAY_HOST").unwrap_or("192.0.2.100")
}

/// Fallback gateway port, paired with `gateway_host()`.
pub fn gateway_port() -> u16 {
    option_env!("BREEZIO_GATEWAY_PORT")
        .and_then(|value| value.parse().ok())
        .unwrap_or(PORT)
}
