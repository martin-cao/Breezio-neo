//! Wi-Fi STA bring-up with SSID fallback (feature = "wifi").
//!
//! The node joins the LAN itself and gets its address via DHCP. It does not need
//! a static IP because it actively connects to the gateway TCP listener
//! configured in `net_link.rs`.
//!
//! Join order: `PRIMARY_SSID` first, then `FALLBACK_SSID` if that association
//! fails or times out. The ESP32-S3 radio is 2.4 GHz-only, so when the primary
//! network is 5 GHz-only the first attempt simply never associates and we fall
//! back automatically, using the same code path as any other join failure.
//!
//! Credentials are compile-time overridable so real ones never need to live in
//! source: `BREEZIO_WIFI_SSID=... BREEZIO_WIFI_FALLBACK_SSID=...
//! BREEZIO_WIFI_PASS=... BREEZIO_GATEWAY_HOST=... cargo build --features wifi`.
//! The public defaults below are placeholders. Override all three values for a
//! real deployment.

use std::time::{Duration, Instant};

use esp_idf_hal::modem::Modem;
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};

pub const PRIMARY_SSID: &str = match option_env!("BREEZIO_WIFI_SSID") {
    Some(ssid) => ssid,
    None => "breezio-lab",
};
pub const FALLBACK_SSID: &str = match option_env!("BREEZIO_WIFI_FALLBACK_SSID") {
    Some(ssid) => ssid,
    None => "breezio-lab-2g",
};
pub const PASSWORD: &str = match option_env!("BREEZIO_WIFI_PASS") {
    Some(pass) => pass,
    None => "change-me",
};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);

pub struct WifiConnection {
    pub driver: BlockingWifi<EspWifi<'static>>,
    pub ssid: &'static str,
    pub ip: String,
}

/// Bring the STA up and block until one of the two SSIDs hands us a DHCP lease.
/// The returned `BlockingWifi` must stay alive for the link to persist.
pub fn connect(modem: Modem) -> anyhow::Result<WifiConnection> {
    let sysloop = EspSystemEventLoop::take()?;
    let nvs = EspDefaultNvsPartition::take()?;
    let mut wifi = BlockingWifi::wrap(EspWifi::new(modem, sysloop.clone(), Some(nvs))?, sysloop)?;

    for ssid in [PRIMARY_SSID, FALLBACK_SSID] {
        match try_join(&mut wifi, ssid) {
            Ok(()) => {
                let ip = wifi.wifi().sta_netif().get_ip_info()?;
                log::info!(
                    "Wi-Fi up on {ssid:?}, DHCP address {}; register this IP in the dashboard",
                    ip.ip
                );
                return Ok(WifiConnection {
                    driver: wifi,
                    ssid,
                    ip: ip.ip.to_string(),
                });
            }
            Err(e) => log::warn!("Wi-Fi join {ssid:?} failed ({e}); trying fallback"),
        }
    }
    anyhow::bail!("could not join {PRIMARY_SSID:?} or {FALLBACK_SSID:?}")
}

fn try_join(wifi: &mut BlockingWifi<EspWifi<'static>>, ssid: &str) -> anyhow::Result<()> {
    let config = Configuration::Client(ClientConfiguration {
        ssid: ssid
            .try_into()
            .map_err(|_| anyhow::anyhow!("SSID too long: {ssid:?}"))?,
        password: PASSWORD
            .try_into()
            .map_err(|_| anyhow::anyhow!("Wi-Fi password too long"))?,
        auth_method: AuthMethod::WPA2Personal,
        ..Default::default()
    });
    if wifi.is_started()? {
        let _ = wifi.stop();
    }
    wifi.set_configuration(&config)?;
    wifi.start()?;
    log::info!("joining Wi-Fi {ssid:?} ...");
    wifi.connect()?;

    // Bound the DHCP wait ourselves so a half-joined network can't hang boot.
    let deadline = Instant::now() + CONNECT_TIMEOUT;
    while !wifi.is_up()? {
        if Instant::now() >= deadline {
            let _ = wifi.disconnect();
            anyhow::bail!("timed out waiting for association + DHCP");
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok(())
}
