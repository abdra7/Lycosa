import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/events.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';

MockClient meOnlyController() => MockClient((request) async {
  if (request.url.path == '/api/v1/me') {
    return http.Response(
      jsonEncode({
        'type': 'user',
        'id': 'u1',
        'role': 'admin',
        'email': 'op@lycosa.local',
      }),
      200,
    );
  }
  return http.Response('{}', 404);
});

void main() {
  test('event model parses and summarizes', () {
    final event = LycosaEvent.fromJson({
      'type': 'alert.created',
      'ts': '2026-07-05T10:00:00+00:00',
      'data': {'severity': 'warning', 'message': "Node 'box' went offline"},
    });
    expect(event.isAlert, isTrue);
    expect(event.summary, contains('went offline'));

    final node = LycosaEvent.fromJson({
      'type': 'node.connected',
      'ts': '2026-07-05T10:00:00+00:00',
      'data': {'name': 'gpu-box'},
    });
    expect(node.isNodeEvent, isTrue);
    expect(node.summary, 'Node gpu-box connected');
  });

  test(
    'eventsProvider streams events from the connector with the token',
    () async {
      final store = InMemoryProfileStore()
        ..profiles = [
          ControllerProfile(
            id: 'p1',
            name: 'lab',
            baseUrl: 'http://c:8000',
            token: 'tok-abc',
          ),
        ]
        ..activeId = 'p1';

      Uri? connectedUri;
      final messages = StreamController<dynamic>();
      final container = ProviderContainer(
        overrides: [
          profileStoreProvider.overrideWithValue(store),
          apiClientFactoryProvider.overrideWithValue(
            (baseUrl, {token}) => ApiClient(
              baseUrl: baseUrl,
              token: token,
              httpClient: meOnlyController(),
            ),
          ),
          wsConnectorProvider.overrideWithValue((uri) {
            connectedUri = uri;
            return messages.stream;
          }),
          wsReconnectDelayProvider.overrideWithValue(null), // no reconnect loop
        ],
      );
      addTearDown(container.dispose);

      final received = <LycosaEvent>[];
      final sub = container.listen(eventsProvider, (_, next) {
        if (next.hasValue) received.add(next.value!);
      });
      addTearDown(sub.close);

      // wait for session restore, then the WS "connection"
      await container.read(sessionProvider.future);
      await Future<void>.delayed(Duration.zero);

      messages.add(
        jsonEncode({
          'type': 'node.disconnected',
          'ts': '2026-07-05T10:00:00+00:00',
          'data': {'name': 'gpu-box'},
        }),
      );
      await Future<void>.delayed(Duration.zero);

      expect(
        connectedUri.toString(),
        'ws://c:8000/api/v1/events?token=tok-abc',
      );
      expect(received, hasLength(1));
      expect(received.single.type, 'node.disconnected');

      await messages.close();
    },
  );
}
