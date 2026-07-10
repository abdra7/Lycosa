// UT-FE-01: mDNS scanner socket handling.
//
// Covers the two Windows-specific failure modes of MDnsClient (Tickets #103,
// #106): SO_REUSEPORT not existing on Windows (bind fails with WSAENOPROTOOPT,
// errno 10042) and virtual adapters rejecting multicast group membership
// (errno 10042/10045 from joinMulticast/setRawOption).

import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lycosa_dashboard/features/nodes/discovery.dart';

/// Delegate whose multicast-related calls throw a configurable error; only the
/// members the tests touch are implemented, the rest fall through to
/// [noSuchMethod].
class FakeRawDatagramSocket implements RawDatagramSocket {
  Object? multicastError;
  Object? rawOptionError;
  int joinCalls = 0;
  int leaveCalls = 0;
  int setRawOptionCalls = 0;
  bool closed = false;
  final List<List<int>> sentBuffers = [];

  @override
  void joinMulticast(InternetAddress group, [NetworkInterface? interface]) {
    joinCalls++;
    if (multicastError != null) throw multicastError!;
  }

  @override
  void leaveMulticast(InternetAddress group, [NetworkInterface? interface]) {
    leaveCalls++;
    if (multicastError != null) throw multicastError!;
  }

  @override
  void setRawOption(RawSocketOption option) {
    setRawOptionCalls++;
    if (rawOptionError != null) throw rawOptionError!;
  }

  @override
  void close() => closed = true;

  @override
  int send(List<int> buffer, InternetAddress address, int port) {
    sentBuffers.add(buffer);
    return buffer.length;
  }

  @override
  Datagram? receive() => null;

  @override
  StreamSubscription<RawSocketEvent> listen(
    void Function(RawSocketEvent event)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => const Stream<RawSocketEvent>.empty().listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

const wsaenoprotoopt = 10042;
const wsaeprotonosupport = 10045;

void main() {
  group('SafeRawDatagramSocket multicast error handling', () {
    late FakeRawDatagramSocket inner;
    late SafeRawDatagramSocket socket;
    final group224 = InternetAddress('224.0.0.251');

    setUp(() {
      inner = FakeRawDatagramSocket();
      socket = SafeRawDatagramSocket(inner);
    });

    test('joinMulticast swallows SocketException with errno 10042', () {
      inner.multicastError = const SocketException(
        'join failed',
        osError: OSError('protocol option not supported', wsaenoprotoopt),
      );
      socket.joinMulticast(group224);
      expect(inner.joinCalls, 1);
    });

    test('joinMulticast swallows bare OSError 10042 and 10045', () {
      inner.multicastError = const OSError('nope', wsaenoprotoopt);
      socket.joinMulticast(group224);
      inner.multicastError = const OSError('nope', wsaeprotonosupport);
      socket.joinMulticast(group224);
      expect(inner.joinCalls, 2);
    });

    test('joinMulticast swallows errors that only mention the code in text',
        () {
      inner.multicastError = Exception('setsockopt failed (OS Error 10042)');
      socket.joinMulticast(group224);
      expect(inner.joinCalls, 1);
    });

    test('joinMulticast rethrows unrelated errors', () {
      inner.multicastError = const OSError('permission denied', 10013);
      expect(() => socket.joinMulticast(group224), throwsA(isA<OSError>()));
    });

    test('leaveMulticast swallows 10042 and rethrows unrelated errors', () {
      inner.multicastError = const OSError('nope', wsaenoprotoopt);
      socket.leaveMulticast(group224);
      expect(inner.leaveCalls, 1);

      inner.multicastError = const SocketException(
        'down',
        osError: OSError('network unreachable', 10051),
      );
      expect(
        () => socket.leaveMulticast(group224),
        throwsA(isA<SocketException>()),
      );
    });

    test('setRawOption swallows 10042 (IP_MULTICAST_IF on virtual adapters)',
        () {
      final option = RawSocketOption.fromInt(
        RawSocketOption.levelIPv4,
        RawSocketOption.IPv4MulticastInterface,
        0,
      );
      inner.rawOptionError = const OSError('nope', wsaenoprotoopt);
      socket.setRawOption(option);
      expect(inner.setRawOptionCalls, 1);

      inner.rawOptionError = const OSError('invalid argument', 10022);
      expect(() => socket.setRawOption(option), throwsA(isA<OSError>()));
    });

    test('delegates send, receive, and close to the wrapped socket', () {
      expect(socket.send([1, 2, 3], group224, 5353), 3);
      expect(inner.sentBuffers.single, [1, 2, 3]);
      expect(socket.receive(), isNull);
      socket.close();
      expect(inner.closed, isTrue);
    });
  });

  group('bindSafeSocket (real sockets)', () {
    test('binds with reusePort requested and wraps in SafeRawDatagramSocket',
        () async {
      // MDnsClient.start always passes reusePort: true; on Windows the raw
      // bind would fail with errno 10042, so this succeeding everywhere is
      // the point of the factory.
      final socket = await bindSafeSocket(
        InternetAddress.loopbackIPv4,
        0,
        reuseAddress: true,
        reusePort: true,
      );
      addTearDown(socket.close);
      expect(socket, isA<SafeRawDatagramSocket>());
      expect(socket.port, greaterThan(0));
    });

    test('binds UDP 5353 the way MDnsClient does (10042 regression)',
        () async {
      // The exact production call: 0.0.0.0:5353, reuseAddress + reusePort.
      // reuseAddress lets us share 5353 with any OS mDNS responder already
      // listening there.
      final socket = await bindSafeSocket(
        InternetAddress.anyIPv4,
        5353,
        reuseAddress: true,
        reusePort: true,
      );
      addTearDown(socket.close);
      expect(socket.port, 5353);
    });

    test(
      'canary: raw reusePort bind on Windows no longer throws 10042',
      () async {
        // Historical context: reusePort on Windows used to fail the bind with
        // WSAENOPROTOOPT (errno 10042), which is why bindSafeSocket drops the
        // flag. As of Dart 3.12 the SDK ignores the flag itself and only
        // prints "Dart Socket ERROR: `reusePort` not supported for Windows"
        // to stderr — so the flag-drop's remaining value is silencing that
        // per-socket noise. If this canary ever starts failing, dart:io has
        // changed behavior again and bindSafeSocket should be revisited.
        final socket = await RawDatagramSocket.bind(
          InternetAddress.loopbackIPv4,
          0,
          reuseAddress: true,
          reusePort: true,
        );
        addTearDown(socket.close);
        expect(socket.port, greaterThan(0));
      },
      skip: Platform.isWindows ? false : 'Windows-specific OS behavior',
    );
  });
}
