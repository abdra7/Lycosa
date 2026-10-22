//! This machine's LAN address, for the add-node commands: when the app is
//! connected to a controller over localhost it runs on the controller's
//! host, so other devices must be given the LAN IP instead.

/// The most likely real LAN IPv4: VirtualBox host-only (192.168.56.x),
/// Docker/WSL (172.16–31.x) and link-local (169.254.x) are skipped;
/// 192.168.x beats 10.x beats anything else.
pub fn best_lan_ipv4<'a>(addresses: impl IntoIterator<Item = &'a str>) -> Option<String> {
    let mut candidates: Vec<&str> = addresses
        .into_iter()
        .filter(|ip| {
            !ip.starts_with("169.254.") && !ip.starts_with("192.168.56.") && !docker_or_wsl(ip)
        })
        .collect();
    candidates.sort_by_key(|ip| rank(ip));
    candidates.first().map(|ip| ip.to_string())
}

fn docker_or_wsl(ip: &str) -> bool {
    let parts: Vec<&str> = ip.split('.').collect();
    if parts.len() != 4 || parts[0] != "172" {
        return false;
    }
    parts[1]
        .parse::<u8>()
        .is_ok_and(|second| (16..=31).contains(&second))
}

fn rank(ip: &str) -> u8 {
    if ip.starts_with("192.168.") {
        0
    } else if ip.starts_with("10.") {
        1
    } else {
        2
    }
}

pub fn detect_lan_ipv4() -> Option<String> {
    let interfaces = if_addrs::get_if_addrs().ok()?;
    let addresses: Vec<String> = interfaces
        .iter()
        .filter(|iface| !iface.is_loopback())
        .filter_map(|iface| match iface.ip() {
            std::net::IpAddr::V4(v4) => Some(v4.to_string()),
            std::net::IpAddr::V6(_) => None,
        })
        .collect();
    best_lan_ipv4(addresses.iter().map(String::as_str))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefers_home_lans_and_skips_virtual_adapters() {
        assert_eq!(
            best_lan_ipv4(["172.17.0.1", "10.0.0.7", "192.168.1.20", "169.254.3.3"]),
            Some("192.168.1.20".into())
        );
        assert_eq!(
            best_lan_ipv4(["192.168.56.1", "10.2.3.4"]),
            Some("10.2.3.4".into())
        );
        assert_eq!(best_lan_ipv4(["172.20.1.1", "169.254.1.1"]), None);
        assert_eq!(best_lan_ipv4(["172.32.0.5"]), Some("172.32.0.5".into()));
    }
}
