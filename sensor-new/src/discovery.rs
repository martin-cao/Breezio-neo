//! Gateway discovery over DNS-SD (feature = "wifi"): finds the Wi-Fi gateway's
//! host/port by browsing `_breezio-gateway._tcp.local` instead of relying on a
//! compile-time IP. `net_link.rs` falls back to `BREEZIO_GATEWAY_HOST` /
//! `BREEZIO_GATEWAY_PORT` when discovery fails or times out, and re-runs
//! discovery on every reconnect so a gateway IP change (DHCP renewal, moved
//! host machine, ...) is picked up without reflashing.

use std::time::Duration;

use esp_idf_svc::mdns::{EspMdns, Interface, Protocol, QueryResult};

const SERVICE_TYPE: &str = "_breezio-gateway";
const PROTO: &str = "_tcp";
const QUERY_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_RESULTS: usize = 4;

/// A gateway found via mDNS: enough to open the TCP link.
pub struct DiscoveredGateway {
    pub host: String,
    pub port: u16,
}

/// Browse for `_breezio-gateway._tcp.local` and return the first result with
/// a resolved address. Returns `None` on any failure (mDNS not initialized,
/// no responders within the timeout, ...) so the caller can fall back to the
/// compile-time host/port.
pub fn discover_gateway() -> Option<DiscoveredGateway> {
    let mdns = match EspMdns::take() {
        Ok(mdns) => mdns,
        Err(e) => {
            log::warn!("mDNS init failed ({e:?}); using compile-time gateway address");
            return None;
        }
    };

    let empty = QueryResult {
        instance_name: None,
        hostname: None,
        port: 0,
        txt: Vec::new(),
        addr: Vec::new(),
        interface: Interface::STA,
        ip_protocol: Protocol::V4,
    };
    let mut results = vec![empty; MAX_RESULTS];
    let found = match mdns.query_ptr(
        SERVICE_TYPE,
        PROTO,
        QUERY_TIMEOUT,
        MAX_RESULTS,
        &mut results,
    ) {
        Ok(count) => count,
        Err(e) => {
            log::warn!("mDNS query for {SERVICE_TYPE}.{PROTO}.local failed ({e:?}); using compile-time gateway address");
            return None;
        }
    };

    results[..found].iter().find_map(|result| {
        let addr = result.addr.first()?;
        let gateway = DiscoveredGateway {
            host: addr.to_string(),
            port: result.port,
        };
        log::info!(
            "discovered gateway {:?} at {}:{}",
            result.instance_name.as_deref().unwrap_or("?"),
            gateway.host,
            gateway.port,
        );
        Some(gateway)
    })
}
