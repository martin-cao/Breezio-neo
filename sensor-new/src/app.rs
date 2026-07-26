//! Shared node state — the single source of truth the loop reads and the display,
//! LED and telemetry all render from.

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum WifiStatus {
    Disabled,
    Connecting,
    Connected { ssid: &'static str, ip: String },
    Failed,
}

/// The simulated "window" node's full runtime state.
pub struct AppState {
    pub seq: u32,
    pub temp_c: Option<f32>,
    pub hum_pct: Option<f32>,
    pub light_pct: u8,
    pub aq_mv: u16,
    /// Simulated window position (no real motor): `true` = open.
    pub window_open: bool,
    /// Air-quality alarm currently sounding.
    pub alarm: bool,
    /// User silenced the alarm; it re-arms once air quality recovers.
    pub muted: bool,
    /// Wi-Fi STA state shown on the OLED when the `wifi` feature is enabled.
    pub wifi_status: WifiStatus,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            seq: 0,
            temp_c: None,
            hum_pct: None,
            light_pct: 0,
            aq_mv: 0,
            window_open: false,
            alarm: false,
            muted: false,
            wifi_status: WifiStatus::Disabled,
        }
    }

    pub fn window_str(&self) -> &'static str {
        if self.window_open {
            "OPEN"
        } else {
            "CLOSED"
        }
    }

    pub fn wifi_status_line(&self) -> String {
        match &self.wifi_status {
            WifiStatus::Disabled => "WiFi:OFF".into(),
            WifiStatus::Connecting => "WiFi:CONNECTING".into(),
            WifiStatus::Connected { ssid, ip } => format!("WiFi:{ssid} {ip}"),
            WifiStatus::Failed => "WiFi:FAILED".into(),
        }
    }
}
