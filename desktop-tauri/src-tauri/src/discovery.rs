//! LAN discovery of Lycosa agents over mDNS/DNS-SD (ADR-018). Agents
//! advertise `_lycosa-agent._tcp.local.`; the scan runs here, in the desktop
//! app on the operator's LAN, and only when the operator presses Scan.
//!
//! Windows (Ticket #106): virtual adapters (VPN, Docker, WSL) reject
//! multicast membership, and SO_REUSEPORT does not exist. `mdns-sd` binds
//! per interface and skips one it cannot join, so a bad adapter costs that
//! adapter only, never the scan; socket errors stay inside the daemon thread
//! instead of crashing the app. Scans are IPv4-only, as before.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use mdns_sd::{IfKind, ServiceDaemon, ServiceEvent};
use serde::Serialize;

pub const SERVICE_TYPE: &str = "_lycosa-agent._tcp.local.";

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveredAgent {
    /// TXT `name`, else the instance label.
    pub name: String,
    pub address: String,
    pub port: u16,
    pub version: Option<String>,
}

/// The instance label of `gpu-box._lycosa-agent._tcp.local.`.
pub fn instance_label(fullname: &str) -> String {
    fullname
        .strip_suffix(SERVICE_TYPE)
        .map(|s| s.trim_end_matches('.'))
        .unwrap_or_else(|| fullname.split('.').next().unwrap_or(fullname))
        .to_string()
}

pub fn scan_blocking(window: Duration) -> Result<Vec<DiscoveredAgent>, String> {
    let daemon = ServiceDaemon::new().map_err(|e| e.to_string())?;
    let _ = daemon.disable_interface(IfKind::IPv6);
    let receiver = daemon.browse(SERVICE_TYPE).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + window;
    let mut found: BTreeMap<String, DiscoveredAgent> = BTreeMap::new();
    while let Some(remaining) = deadline.checked_duration_since(Instant::now()) {
        match receiver.recv_timeout(remaining) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                let label = instance_label(info.get_fullname());
                let name = info
                    .get_property_val_str("name")
                    .filter(|n| !n.is_empty())
                    .map(str::to_string)
                    .unwrap_or(label);
                let version = info.get_property_val_str("version").map(str::to_string);
                let mut addresses: Vec<String> = info
                    .get_addresses_v4()
                    .into_iter()
                    .map(|ip| ip.to_string())
                    .collect();
                addresses.sort();
                if let Some(address) = addresses.into_iter().next() {
                    found.insert(
                        info.get_fullname().to_string(),
                        DiscoveredAgent {
                            name,
                            address,
                            port: info.get_port(),
                            version,
                        },
                    );
                }
            }
            Ok(_) => {}
            Err(_) => break,
        }
    }
    let _ = daemon.stop_browse(SERVICE_TYPE);
    let _ = daemon.shutdown();
    let mut agents: Vec<DiscoveredAgent> = found.into_values().collect();
    agents.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(agents)
}

pub async fn scan(window: Duration) -> Result<Vec<DiscoveredAgent>, String> {
    tokio::task::spawn_blocking(move || scan_blocking(window))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn instance_labels() {
        assert_eq!(
            instance_label("gpu-box._lycosa-agent._tcp.local."),
            "gpu-box"
        );
        assert_eq!(
            instance_label("odd.name._lycosa-agent._tcp.local."),
            "odd.name"
        );
        assert_eq!(instance_label("other._x._tcp.local."), "other");
    }
}
