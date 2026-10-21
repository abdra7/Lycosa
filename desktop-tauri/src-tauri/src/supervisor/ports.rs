//! Port selection for the standalone services.

use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};

/// True when nothing listens on `host:port` (checked by binding it).
pub fn is_free(host: Ipv4Addr, port: u16) -> bool {
    port != 0 && TcpListener::bind(SocketAddrV4::new(host, port)).is_ok()
}

/// `preferred` when it is free on loopback and not already taken by another
/// service in this start; otherwise a fresh port from the OS.
pub fn pick(preferred: Option<u16>, taken: &[u16]) -> std::io::Result<u16> {
    if let Some(port) = preferred {
        if !taken.contains(&port) && is_free(Ipv4Addr::LOCALHOST, port) {
            return Ok(port);
        }
    }
    for _ in 0..20 {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))?;
        let port = listener.local_addr()?.port();
        if !taken.contains(&port) {
            return Ok(port);
        }
    }
    Err(std::io::Error::other("no free local port"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_a_free_preferred_port() {
        let port = pick(None, &[]).unwrap();
        assert_eq!(pick(Some(port), &[]).unwrap(), port);
    }

    #[test]
    fn replaces_a_busy_preferred_port() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let busy = listener.local_addr().unwrap().port();
        assert!(!is_free(Ipv4Addr::LOCALHOST, busy));
        let chosen = pick(Some(busy), &[]).unwrap();
        assert_ne!(chosen, busy);
    }

    #[test]
    fn never_hands_out_a_port_twice() {
        let first = pick(None, &[]).unwrap();
        let second = pick(Some(first), &[first]).unwrap();
        assert_ne!(first, second);
    }
}
