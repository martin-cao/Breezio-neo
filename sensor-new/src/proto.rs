//! `$BRZ2` line-protocol encoder/decoder. See `docs/PROTOCOL.md`.
//!
//! Everything is plain ASCII. Each line is `$<body>*<CS>\r\n` where `<CS>` is the
//! XOR of every byte of `<body>` (NMEA-style). We only ever *encode* telemetry /
//! hello / ack here, and only ever *decode* commands from the host.

use std::fmt::Write as _;

/// NMEA-style XOR checksum over the bytes between `$` and `*`.
pub fn checksum(body: &str) -> u8 {
    body.bytes().fold(0u8, |acc, b| acc ^ b)
}

/// Wrap a comma-joined `body` (e.g. `"BRZ2,T,1,..."`, no `$`/`*`) into a full line.
fn frame(body: &str) -> String {
    let mut line = String::with_capacity(body.len() + 10);
    // write! to a String is infallible.
    let _ = write!(line, "${}*{:02X}\r\n", body, checksum(body));
    line
}

/// Format an optional float field as one decimal, or `NA` when the sensor failed.
fn fmt_opt(value: Option<f32>) -> String {
    match value {
        Some(v) => format!("{:.1}", v),
        None => "NA".to_string(),
    }
}

/// A single telemetry sample to emit to the host.
pub struct Telemetry {
    pub seq: u32,
    pub temp_c: Option<f32>,
    pub hum_pct: Option<f32>,
    pub light_pct: u8,
    pub aq_mv: u16,
    pub window_open: bool,
    pub alarm: bool,
}

impl Telemetry {
    /// `$BRZ2,T,<seq>,<temp>,<hum>,<light>,<aq>,<win>,<alarm>*CS`
    pub fn encode(&self) -> String {
        let body = format!(
            "BRZ2,T,{},{},{},{},{},{},{}",
            self.seq,
            fmt_opt(self.temp_c),
            fmt_opt(self.hum_pct),
            self.light_pct,
            self.aq_mv,
            if self.window_open { "OPEN" } else { "CLOSED" },
            if self.alarm { 1 } else { 0 },
        );
        frame(&body)
    }
}

/// `$BRZ2,HELLO,<fw>,<node_id>*CS`
pub fn encode_hello(fw_version: &str, node_id: &str) -> String {
    frame(&format!("BRZ2,HELLO,{},{}", fw_version, node_id))
}

/// `$BRZ2,ACK,<id>,<device>,<result>,<state>*CS`
pub fn encode_ack(id: u32, device: Device, ok: bool, state: &str) -> String {
    frame(&format!(
        "BRZ2,ACK,{},{},{},{}",
        id,
        device.as_str(),
        if ok { "OK" } else { "ERR" },
        state,
    ))
}

/// Controllable devices exposed to the host.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Device {
    Window,
    Buzzer,
}

impl Device {
    pub fn as_str(self) -> &'static str {
        match self {
            Device::Window => "WINDOW",
            Device::Buzzer => "BUZZER",
        }
    }
}

/// Actions the host can request.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Action {
    Open,
    Close,
    Toggle,
    Mute,
    Test,
}

/// A decoded, checksum-verified host command.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Command {
    pub id: u32,
    pub device: Device,
    pub action: Action,
}

/// Parse one line for a valid `$BRZ2,CMD,...` command.
///
/// Tolerant by design: leading/trailing garbage is ignored (we anchor on the last
/// `$`), and anything with a bad checksum, unknown device/action, or malformed
/// shape returns `None` instead of panicking — a noisy byte can never crash us.
pub fn parse_command(line: &str) -> Option<Command> {
    let dollar = line.rfind('$')?;
    let after = &line[dollar + 1..];
    let star = after.find('*')?;
    let body = &after[..star];
    let cs_hex = after.get(star + 1..star + 3)?;

    let expected = u8::from_str_radix(cs_hex, 16).ok()?;
    if checksum(body) != expected {
        return None;
    }

    let mut fields = body.split(',');
    if fields.next()? != "BRZ2" {
        return None;
    }
    if fields.next()? != "CMD" {
        return None;
    }
    let id: u32 = fields.next()?.parse().ok()?;
    let device = match fields.next()? {
        "WINDOW" => Device::Window,
        "BUZZER" => Device::Buzzer,
        _ => return None,
    };
    let action = match (device, fields.next()?) {
        (Device::Window, "OPEN") => Action::Open,
        (Device::Window, "CLOSE") => Action::Close,
        (Device::Window, "TOGGLE") => Action::Toggle,
        (Device::Buzzer, "MUTE") => Action::Mute,
        (Device::Buzzer, "TEST") => Action::Test,
        _ => return None,
    };
    Some(Command { id, device, action })
}
