import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:multicast_dns/multicast_dns.dart';

/// LAN discovery of Lycosa agents over mDNS/DNS-SD (Ticket #103).
///
/// Agents advertise `_lycosa-agent._tcp.local.`; this scan runs in the
/// dashboard because it is a native app on the operator's LAN — the
/// controller sits in a bridge-network Docker container that multicast
/// never reaches.

const lycosaServiceType = '_lycosa-agent._tcp.local';

/// One agent announcing itself on the LAN.
class DiscoveredAgent {
  const DiscoveredAgent({
    required this.name,
    required this.address,
    required this.port,
    this.version,
  });

  final String name; // node name (TXT record, falls back to instance label)
  final String address; // IPv4 of the exec API
  final int port; // exec API port
  final String? version;
}

/// Seam for tests and future transports: production scans mDNS.
typedef LanScan = Future<List<DiscoveredAgent>> Function();

final lanScanProvider = Provider<LanScan>((ref) => scanForAgents);

/// A [RawDatagramSocket] delegate whose multicast join/leave failures are
/// swallowed instead of thrown (Ticket #106).
///
/// On Windows, virtual adapters (VPN tunnels, Docker/WSL switches) reject
/// multicast group membership: the underlying setsockopt fails with
/// WSAENOPROTOOPT (errno 10042) or WSAEPROTONOSUPPORT (errno 10045), which
/// [MDnsClient.start] would otherwise surface as a crash of the whole scan.
/// Skipping the adapter is correct — agents are still found via the ones
/// that joined successfully.
class SafeRawDatagramSocket extends StreamView<RawSocketEvent>
    implements RawDatagramSocket {
  SafeRawDatagramSocket(this._socket) : super(_socket);

  final RawDatagramSocket _socket;

  /// dart:io is inconsistent about the thrown type: bind failures surface as
  /// [SocketException], but joinMulticast/setRawOption failures come out as a
  /// bare [OSError] — so match on the error code wherever it lives.
  static bool _isIgnorableMulticastError(Object e) {
    final code = switch (e) {
      SocketException(:final osError) => osError?.errorCode,
      OSError(:final errorCode) => errorCode,
      _ => null,
    };
    if (code == 10042 || code == 10045) return true;
    final text = e.toString();
    return text.contains('10042') || text.contains('10045');
  }

  @override
  void joinMulticast(InternetAddress group, [NetworkInterface? interface]) {
    try {
      _socket.joinMulticast(group, interface);
    } catch (e) {
      if (!_isIgnorableMulticastError(e)) rethrow;
    }
  }

  @override
  void leaveMulticast(InternetAddress group, [NetworkInterface? interface]) {
    try {
      _socket.leaveMulticast(group, interface);
    } catch (e) {
      if (!_isIgnorableMulticastError(e)) rethrow;
    }
  }

  @override
  InternetAddress get address => _socket.address;

  @override
  int get port => _socket.port;

  @override
  bool get broadcastEnabled => _socket.broadcastEnabled;

  @override
  set broadcastEnabled(bool value) => _socket.broadcastEnabled = value;

  @override
  bool get multicastLoopback => _socket.multicastLoopback;

  @override
  set multicastLoopback(bool value) => _socket.multicastLoopback = value;

  @override
  int get multicastHops => _socket.multicastHops;

  @override
  set multicastHops(int value) => _socket.multicastHops = value;

  @override
  // ignore: deprecated_member_use
  NetworkInterface? get multicastInterface => _socket.multicastInterface;

  @override
  // ignore: deprecated_member_use
  set multicastInterface(NetworkInterface? value) =>
      // ignore: deprecated_member_use
      _socket.multicastInterface = value;

  @override
  bool get readEventsEnabled => _socket.readEventsEnabled;

  @override
  set readEventsEnabled(bool value) => _socket.readEventsEnabled = value;

  @override
  bool get writeEventsEnabled => _socket.writeEventsEnabled;

  @override
  set writeEventsEnabled(bool value) => _socket.writeEventsEnabled = value;

  @override
  void close() => _socket.close();

  @override
  Datagram? receive() => _socket.receive();

  @override
  int send(List<int> buffer, InternetAddress address, int port) =>
      _socket.send(buffer, address, port);

  @override
  Uint8List getRawOption(RawSocketOption option) =>
      _socket.getRawOption(option);

  @override
  void setRawOption(RawSocketOption option) {
    try {
      _socket.setRawOption(option);
    } catch (e) {
      // MDnsClient.start sets IP_MULTICAST_IF per adapter; virtual adapters
      // reject it the same way they reject group membership. Skip them.
      if (!_isIgnorableMulticastError(e)) rethrow;
    }
  }
}

/// Socket factory for [MDnsClient]: binds normally, then wraps the socket so
/// multicast joins on unsupported adapters degrade gracefully.
///
/// [MDnsClient.start] always requests `reusePort: true`, but SO_REUSEPORT
/// does not exist on Windows: the bind itself fails with WSAENOPROTOOPT
/// (errno 10042) before any of the [SafeRawDatagramSocket] protections apply,
/// killing the whole scan. Windows allows port sharing via SO_REUSEADDR
/// (which `reuseAddress` already sets), so dropping the flag there is safe.
@visibleForTesting
Future<RawDatagramSocket> bindSafeSocket(
  dynamic host,
  int port, {
  bool reuseAddress = true,
  bool reusePort = false,
  int ttl = 1,
}) async {
  final socket = await RawDatagramSocket.bind(
    host,
    port,
    reuseAddress: reuseAddress,
    reusePort: reusePort && !Platform.isWindows,
    ttl: ttl,
  );
  return SafeRawDatagramSocket(socket);
}

Future<List<DiscoveredAgent>> scanForAgents() async {
  const lookupTimeout = Duration(seconds: 3);
  final client = MDnsClient(rawDatagramSocketFactory: bindSafeSocket);
  final found = <String, DiscoveredAgent>{};
  try {
    // MDnsClient.start listens on the incoming socket with no error handler
    // of its own: per its docs, an omitted `onError` means socket-level
    // errors are "considered unhandled" and go straight to the Zone's
    // uncaught-error handler instead of this function's try/catch — which is
    // exactly what crashes the whole app on Windows, where a UDP socket can
    // asynchronously surface an ICMP host-unreachable as a later read error
    // well after start() has already returned. Swallow it here instead; the
    // ongoing lookup below is unaffected and keeps returning what it found.
    await client.start(onError: (Object _, StackTrace _) {});
    await for (final ptr in client.lookup<PtrResourceRecord>(
      ResourceRecordQuery.serverPointer(lycosaServiceType),
      timeout: lookupTimeout,
    )) {
      final instance = ptr.domainName; // e.g. gpu-box._lycosa-agent._tcp.local
      final label = instance.split('.').first;

      String name = label;
      String? version;
      await for (final txt in client.lookup<TxtResourceRecord>(
        ResourceRecordQuery.text(instance),
        timeout: lookupTimeout,
      )) {
        for (final line in txt.text.split('\n')) {
          final separator = line.indexOf('=');
          if (separator <= 0) continue;
          final key = line.substring(0, separator);
          final value = line.substring(separator + 1);
          if (key == 'name' && value.isNotEmpty) name = value;
          if (key == 'version') version = value;
        }
      }

      await for (final srv in client.lookup<SrvResourceRecord>(
        ResourceRecordQuery.service(instance),
        timeout: lookupTimeout,
      )) {
        await for (final a in client.lookup<IPAddressResourceRecord>(
          ResourceRecordQuery.addressIPv4(srv.target),
          timeout: lookupTimeout,
        )) {
          found[instance] = DiscoveredAgent(
            name: name,
            address: a.address.address,
            port: srv.port,
            version: version,
          );
        }
      }
    }
  } finally {
    client.stop();
  }
  return found.values.toList()..sort((a, b) => a.name.compareTo(b.name));
}
