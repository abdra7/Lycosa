import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'session.dart';

/// One event from the controller's /api/v1/events stream.
class LycosaEvent {
  LycosaEvent({required this.type, required this.ts, required this.data});

  final String type; // node.* | task.* | workflow.* | alert.created
  final DateTime ts;
  final Map<String, dynamic> data;

  bool get isAlert => type == 'alert.created';
  bool get isNodeEvent => type.startsWith('node.');

  factory LycosaEvent.fromJson(Map<String, dynamic> json) => LycosaEvent(
    type: json['type'] as String,
    ts: DateTime.parse(json['ts'] as String),
    data: json['data'] as Map<String, dynamic>? ?? const {},
  );

  String get summary => switch (type) {
    'node.connected' => 'Node ${data['name']} connected',
    'node.disconnected' => 'Node ${data['name']} disconnected',
    'node.metrics.updated' => 'Metrics from ${data['name']}',
    'task.started' => 'Task started (${data['type']})',
    'task.finished' => 'Task ${data['status']}',
    'workflow.started' => 'Workflow ${data['name']} started',
    'workflow.step.completed' => 'Step ${data['step']} completed',
    'workflow.paused' => 'Workflow paused at ${data['step']}',
    'workflow.finished' => 'Workflow ${data['status']}',
    'alert.created' => data['message'] as String? ?? 'Alert',
    _ => type,
  };
}

/// Seam so tests can inject a fake message stream instead of a socket.
typedef WsConnector = Stream<dynamic> Function(Uri uri);

final wsConnectorProvider = Provider<WsConnector>(
  (ref) =>
      (uri) => WebSocketChannel.connect(uri).stream,
);

/// Reconnect delay; tests override to null to disable reconnecting.
final wsReconnectDelayProvider = Provider<Duration?>(
  (ref) => const Duration(seconds: 5),
);

/// Live event stream for the active profile, with auto-reconnect.
final eventsProvider = StreamProvider<LycosaEvent>((ref) async* {
  final session = ref.watch(sessionProvider).value;
  final profile = session?.activeProfile;
  if (profile?.token == null) return;

  final wsUri = Uri.parse(
    '${profile!.baseUrl.replaceFirst('http', 'ws')}/api/v1/events'
    '?token=${Uri.encodeQueryComponent(profile.token!)}',
  );
  final connect = ref.watch(wsConnectorProvider);
  final reconnectDelay = ref.watch(wsReconnectDelayProvider);

  while (true) {
    try {
      await for (final message in connect(wsUri)) {
        yield LycosaEvent.fromJson(
          jsonDecode(message as String) as Map<String, dynamic>,
        );
      }
    } catch (_) {
      // fall through to reconnect
    }
    if (reconnectDelay == null) return;
    await Future<void>.delayed(reconnectDelay);
  }
});
