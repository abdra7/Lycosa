// The localhost trap that cost real setup time: the Add-node command must not
// hand a "localhost" URL to an agent running on a different machine. These
// cover the pure logic that rewrites it to the controller's LAN IP and picks
// the right interface among virtual adapters.

import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/features/nodes/add_node_dialog.dart';

void main() {
  group('isLoopbackHost', () {
    test('flags localhost and 127.0.0.1', () {
      expect(isLoopbackHost('http://localhost:8000'), isTrue);
      expect(isLoopbackHost('http://127.0.0.1:8000/'), isTrue);
    });

    test('does not flag real hosts or IPs', () {
      expect(isLoopbackHost('http://192.168.0.120:8000'), isFalse);
      expect(isLoopbackHost('http://controller.lan:8000'), isFalse);
    });
  });

  group('controllerUrlForOtherDevices', () {
    test('swaps a localhost host for the detected LAN IP', () {
      expect(
        controllerUrlForOtherDevices('http://localhost:8000', '192.168.0.120'),
        'http://192.168.0.120:8000',
      );
      expect(
        controllerUrlForOtherDevices('http://127.0.0.1:8000/', '192.168.0.120'),
        'http://192.168.0.120:8000/',
      );
    });

    test('leaves an already-routable URL untouched', () {
      expect(
        controllerUrlForOtherDevices('http://192.168.0.5:8000', '192.168.0.120'),
        'http://192.168.0.5:8000',
      );
    });

    test('leaves localhost as-is when no LAN IP was detected', () {
      expect(
        controllerUrlForOtherDevices('http://localhost:8000', null),
        'http://localhost:8000',
      );
    });
  });

  group('bestLanIpv4', () {
    test('picks the real Wi-Fi/LAN address over virtual adapters', () {
      // exactly the controller machine's interface list from the field report
      final picked = bestLanIpv4([
        '172.30.160.1', // WSL / Hyper-V
        '192.168.56.1', // VirtualBox host-only
        '169.254.79.211', // link-local
        '192.168.0.120', // Wi-Fi  <-- the one we want
      ]);
      expect(picked, '192.168.0.120');
    });

    test('prefers 192.168.x over 10.x', () {
      expect(bestLanIpv4(['10.0.0.5', '192.168.1.7']), '192.168.1.7');
    });

    test('falls back to 10.x when no 192.168 exists', () {
      expect(bestLanIpv4(['172.17.0.1', '10.1.2.3']), '10.1.2.3');
    });

    test('returns null when only virtual/link-local addresses exist', () {
      expect(
        bestLanIpv4(['172.20.0.1', '192.168.56.1', '169.254.1.1']),
        isNull,
      );
    });

    test('does not treat 172.32.x (outside the private range) as Docker', () {
      expect(bestLanIpv4(['172.32.5.5']), '172.32.5.5');
    });
  });
}
