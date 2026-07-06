import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';
import 'package:lycosa_dashboard/features/nodes/discovery.dart';
import 'package:lycosa_dashboard/features/nodes/node_detail_screen.dart';
import 'package:lycosa_dashboard/features/nodes/nodes_screen.dart';

import 'api_client_nodes_test.dart' show nodeJson;

/// Fake controller serving /me, node list/detail/patch, and key minting.
MockClient fakeController({
  required List<Map<String, dynamic>> nodes,
  String role = 'admin',
  List<http.Request>? captured,
}) {
  return MockClient((request) async {
    captured?.add(request);
    final path = request.url.path;
    if (path == '/api/v1/me') {
      return http.Response(
          jsonEncode({
            'type': 'user',
            'id': 'u1',
            'role': role,
            'email': 'op@lycosa.local'
          }),
          200);
    }
    if (path == '/api/v1/nodes' && request.method == 'GET') {
      return http.Response(jsonEncode(nodes), 200);
    }
    if (path.startsWith('/api/v1/nodes/') && request.method == 'GET') {
      return http.Response(jsonEncode(nodes.first), 200);
    }
    if (path.startsWith('/api/v1/nodes/') && request.method == 'PATCH') {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(
          jsonEncode({...nodes.first, 'role': body['role']}), 200);
    }
    if (path == '/api/v1/admin/api-keys' && request.method == 'POST') {
      return http.Response(
          jsonEncode({
            'id': 'k1',
            'name': 'new-box',
            'api_key': 'lyc_onetime_secret123',
          }),
          201);
    }
    return http.Response(
        jsonEncode({'error': {'code': 'not_found', 'message': 'nope'}}), 404);
  });
}

Widget appWith(MockClient controller,
    {required Widget home, LanScan? lanScan}) {
  final store = InMemoryProfileStore()
    ..profiles = [
      ControllerProfile(
          id: 'p1', name: 'lab', baseUrl: 'http://c:8000', token: 'tok')
    ]
    ..activeId = 'p1';
  return ProviderScope(
    overrides: [
      profileStoreProvider.overrideWithValue(store),
      apiClientFactoryProvider.overrideWithValue(
        (baseUrl, {token}) =>
            ApiClient(baseUrl: baseUrl, token: token, httpClient: controller),
      ),
      if (lanScan != null) lanScanProvider.overrideWithValue(lanScan),
    ],
    child: MaterialApp(home: home),
  );
}

Future<void> settle(WidgetTester tester) async {
  // let the session restore + first poll emit without waiting on the
  // 10s poll timer that pumpAndSettle would spin on
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  testWidgets('node list renders status, roles, and heartbeat age',
      (tester) async {
    final controller = fakeController(nodes: [nodeJson(role: 'hybrid')]);
    await tester.pumpWidget(
        appWith(controller, home: const Scaffold(body: NodesScreen())));
    await settle(tester);

    expect(find.text('gpu-box'), findsOneWidget);
    expect(find.text('online'), findsOneWidget);
    expect(find.text('hybrid'), findsOneWidget);
    expect(find.text('hybrid (85%)'), findsOneWidget);
    expect(find.text('Add node'), findsOneWidget); // admin sees the button
  });

  testWidgets('operator does not see the Add node button', (tester) async {
    final controller =
        fakeController(nodes: [nodeJson()], role: 'operator');
    await tester.pumpWidget(
        appWith(controller, home: const Scaffold(body: NodesScreen())));
    await settle(tester);

    expect(find.text('gpu-box'), findsOneWidget);
    expect(find.text('Add node'), findsNothing);
  });

  testWidgets('role override sends a PATCH from the detail screen',
      (tester) async {
    final captured = <http.Request>[];
    final controller =
        fakeController(nodes: [nodeJson()], captured: captured);
    await tester.pumpWidget(appWith(controller,
        home: const NodeDetailScreen(nodeId: 'n1')));
    await settle(tester);

    expect(find.text('Role'), findsOneWidget);
    expect(find.textContaining('hybrid (85% confidence)'), findsOneWidget);
    expect(find.textContaining('Capable GPU'), findsOneWidget);

    await tester.tap(find.text('Set role'));
    await settle(tester);
    await tester.tap(find.text('storage').last);
    await settle(tester);
    await tester.tap(find.text('Save'));
    await settle(tester);

    final patch = captured.where((r) => r.method == 'PATCH').single;
    expect(jsonDecode(patch.body), {'role': 'storage'});
  });

  testWidgets('LAN scan lists discovered agents and flags unregistered ones',
      (tester) async {
    final controller = fakeController(nodes: [nodeJson()]); // 'gpu-box'
    await tester.pumpWidget(appWith(
      controller,
      home: const Scaffold(body: NodesScreen()),
      lanScan: () async => const [
        DiscoveredAgent(
            name: 'gpu-box',
            address: '192.168.1.20',
            port: 8010,
            version: '0.1.0'),
        DiscoveredAgent(
            name: 'new-laptop', address: '192.168.1.30', port: 8010),
      ],
    ));
    await settle(tester);

    expect(find.text('Discovered on LAN'), findsOneWidget);
    await tester.tap(find.text('Scan'));
    await settle(tester);

    expect(find.text('new-laptop'), findsOneWidget);
    expect(find.textContaining('192.168.1.30:8010'), findsOneWidget);
    expect(find.textContaining('not registered'), findsOneWidget);
    // the registered node is recognized as such
    expect(find.text('registered'), findsOneWidget);
  });

  testWidgets('add-node dialog mints and reveals the one-time key',
      (tester) async {
    final controller = fakeController(nodes: [nodeJson()]);
    await tester.pumpWidget(
        appWith(controller, home: const Scaffold(body: NodesScreen())));
    await settle(tester);

    await tester.tap(find.text('Add node'));
    await settle(tester);
    await tester.enterText(
        find.widgetWithText(TextField, 'Key name'), 'new-box');
    await tester.tap(find.text('Create key'));
    await settle(tester);

    expect(find.text('lyc_onetime_secret123'), findsOneWidget);
    expect(find.textContaining('will not be shown again'), findsOneWidget);
    expect(find.textContaining('lycosa-agent run'), findsOneWidget);
  });
}
