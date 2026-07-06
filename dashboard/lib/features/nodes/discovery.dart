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

Future<List<DiscoveredAgent>> scanForAgents() async {
  const lookupTimeout = Duration(seconds: 3);
  final client = MDnsClient();
  final found = <String, DiscoveredAgent>{};
  try {
    await client.start();
    await for (final ptr in client.lookup<PtrResourceRecord>(
        ResourceRecordQuery.serverPointer(lycosaServiceType),
        timeout: lookupTimeout)) {
      final instance = ptr.domainName; // e.g. gpu-box._lycosa-agent._tcp.local
      final label = instance.split('.').first;

      String name = label;
      String? version;
      await for (final txt in client.lookup<TxtResourceRecord>(
          ResourceRecordQuery.text(instance),
          timeout: lookupTimeout)) {
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
          timeout: lookupTimeout)) {
        await for (final a in client.lookup<IPAddressResourceRecord>(
            ResourceRecordQuery.addressIPv4(srv.target),
            timeout: lookupTimeout)) {
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
  return found.values.toList()
    ..sort((a, b) => a.name.compareTo(b.name));
}
