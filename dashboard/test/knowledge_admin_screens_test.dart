import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';
import 'package:lycosa_dashboard/features/admin/admin_screen.dart';
import 'package:lycosa_dashboard/features/knowledge/knowledge_screen.dart';

MockClient fakeController({
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
          'email': 'op@lycosa.local',
        }),
        200,
      );
    }
    if (path == '/api/v1/knowledge/collections' && request.method == 'GET') {
      return http.Response(
        jsonEncode([
          {
            'id': 'c1',
            'name': 'spider-facts',
            'description': 'arachnids',
            'embedding_backend': 'hashing',
            'embedding_dim': 384,
            'created_at': '2026-07-05T10:00:00Z',
          },
        ]),
        200,
      );
    }
    if (path == '/api/v1/knowledge/collections/c1/documents' &&
        request.method == 'GET') {
      return http.Response(
        jsonEncode([
          {
            'id': 'd1',
            'collection_id': 'c1',
            'filename': 'spiders.md',
            'content_type': 'text/markdown',
            'size_bytes': 2048,
            'status': 'embedded',
            'chunk_count': 3,
            'error': null,
            'created_at': '2026-07-05T10:00:00Z',
          },
        ]),
        200,
      );
    }
    if (path == '/api/v1/knowledge/retrieve') {
      return http.Response(
        jsonEncode({
          'chunks': [
            {
              'text': 'Wolf spiders hunt at night.',
              'source': 'spiders.md',
              'collection': 'spider-facts',
              'score': 0.91,
              'document_id': 'd1',
            },
          ],
          'context_text': '...',
          'latency_ms': 4.2,
        }),
        200,
      );
    }
    if (path == '/api/v1/admin/audit-logs') {
      return http.Response(
        jsonEncode([
          {
            'id': 'a1',
            'created_at': '2026-07-05T10:00:00Z',
            'actor_user_id': 'u1',
            'actor_api_key_id': null,
            'action': 'auth.login.success',
            'resource_type': 'session',
            'resource_id': 's1',
            'detail': null,
            'ip_address': '192.168.1.9',
          },
        ]),
        200,
      );
    }
    if (path == '/api/v1/admin/api-keys' && request.method == 'GET') {
      return http.Response(
        jsonEncode([
          {
            'id': 'k1',
            'name': 'garage-box',
            'key_prefix': 'abcd1234',
            'node_id': 'n1',
            'expires_at': null,
            'revoked_at': null,
            'last_used_at': '2026-07-05T10:00:00Z',
            'created_at': '2026-07-05T09:00:00Z',
          },
        ]),
        200,
      );
    }
    if (path.startsWith('/api/v1/admin/api-keys/') &&
        request.method == 'DELETE') {
      return http.Response('', 204);
    }
    return http.Response(
      jsonEncode({
        'error': {'code': 'not_found', 'message': 'nope'},
      }),
      404,
    );
  });
}

Map<String, dynamic> collectionJson(String id, String name) => {
  'id': id,
  'name': name,
  'description': null,
  'embedding_backend': 'hashing',
  'embedding_dim': 384,
  'created_at': '2026-07-05T10:00:00Z',
};

/// A controller whose collection list actually shrinks when a DELETE lands,
/// so refresh assertions exercise the real refetch instead of a static fake.
MockClient deletableController(
  List<Map<String, dynamic>> collections, {
  List<http.Request>? captured,
  int deleteStatus = 204,
  String deleteError = 'Qdrant delete failed — is the qdrant service running?',
}) {
  final deletePath = RegExp(r'^/api/v1/knowledge/collections/([^/]+)$');
  return MockClient((request) async {
    captured?.add(request);
    final path = request.url.path;
    if (path == '/api/v1/me') {
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
    if (path == '/api/v1/knowledge/collections' && request.method == 'GET') {
      return http.Response(jsonEncode(collections), 200);
    }
    final match = deletePath.firstMatch(path);
    if (match != null && request.method == 'DELETE') {
      if (deleteStatus != 204) {
        return http.Response(
          jsonEncode({
            'error': {'code': 'bad_gateway', 'message': deleteError},
          }),
          deleteStatus,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }
      collections.removeWhere((c) => c['id'] == match.group(1));
      return http.Response('', 204);
    }
    if (path.endsWith('/documents') && request.method == 'GET') {
      return http.Response(jsonEncode([]), 200);
    }
    return http.Response(
      jsonEncode({
        'error': {'code': 'not_found', 'message': 'nope'},
      }),
      404,
    );
  });
}

Widget appWith(MockClient controller, {required Widget home}) {
  final store = InMemoryProfileStore()
    ..profiles = [
      ControllerProfile(
        id: 'p1',
        name: 'lab',
        baseUrl: 'http://c:8000',
        token: 'tok',
      ),
    ]
    ..activeId = 'p1';
  return ProviderScope(
    overrides: [
      profileStoreProvider.overrideWithValue(store),
      apiClientFactoryProvider.overrideWithValue(
        (baseUrl, {token}) =>
            ApiClient(baseUrl: baseUrl, token: token, httpClient: controller),
      ),
    ],
    child: MaterialApp(home: home),
  );
}

Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  testWidgets('collections and documents render; playground retrieves', (
    tester,
  ) async {
    final controller = fakeController();
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: KnowledgeScreen())),
    );
    await settle(tester);

    expect(find.text('spider-facts'), findsOneWidget);
    await tester.tap(find.text('spider-facts'));
    await settle(tester);

    expect(find.text('spiders.md'), findsOneWidget);
    expect(find.textContaining('3 chunks'), findsOneWidget);

    await tester.enterText(
      find.widgetWithText(TextField, 'Query'),
      'wolf spiders',
    );
    await tester.tap(find.text('Retrieve'));
    await settle(tester);

    expect(find.text('Wolf spiders hunt at night.'), findsOneWidget);
    expect(find.text('0.91'), findsOneWidget);
    expect(find.text('spider-facts/spiders.md'), findsOneWidget);
  });

  // UT-FE-02: the collection list renders one delete button per collection.
  testWidgets('each collection renders a delete button', (tester) async {
    final controller = deletableController([
      collectionJson('c1', 'spider-facts'),
      collectionJson('c2', 'flutter-docs'),
    ]);
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: KnowledgeScreen())),
    );
    await settle(tester);

    expect(find.text('spider-facts'), findsOneWidget);
    expect(find.text('flutter-docs'), findsOneWidget);
    expect(find.byTooltip('Delete collection'), findsNWidgets(2));
    expect(find.byIcon(Icons.delete_outline), findsNWidgets(2));
  });

  // UT-FE-02: deletion is guarded by a confirmation dialog naming the target.
  testWidgets('cancelling the confirmation makes no request', (tester) async {
    final captured = <http.Request>[];
    final controller = deletableController([
      collectionJson('c1', 'spider-facts'),
    ], captured: captured);
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: KnowledgeScreen())),
    );
    await settle(tester);

    await tester.tap(find.byTooltip('Delete collection'));
    await settle(tester);
    expect(find.text('Delete collection?'), findsOneWidget);
    expect(find.textContaining('"spider-facts"'), findsOneWidget);
    expect(find.textContaining('cannot be undone'), findsOneWidget);

    await tester.tap(find.text('Cancel'));
    await settle(tester);

    expect(captured.any((r) => r.method == 'DELETE'), isFalse);
    expect(find.text('spider-facts'), findsOneWidget);
  });

  // IT-UI-01: a confirmed delete hits the API and the sidebar refreshes
  // immediately — the row disappears and the stale detail pane resets.
  testWidgets('confirmed delete refreshes the sidebar immediately', (
    tester,
  ) async {
    final captured = <http.Request>[];
    final controller = deletableController([
      collectionJson('c1', 'spider-facts'),
      collectionJson('c2', 'flutter-docs'),
    ], captured: captured);
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: KnowledgeScreen())),
    );
    await settle(tester);

    // open the doomed collection so the detail pane is showing it
    await tester.tap(find.text('spider-facts'));
    await settle(tester);
    expect(find.text('No documents in this collection.'), findsOneWidget);

    await tester.tap(find.byTooltip('Delete collection').first);
    await settle(tester);
    await tester.tap(find.text('Delete'));
    await settle(tester);

    expect(
      captured.any(
        (r) =>
            r.method == 'DELETE' &&
            r.url.path == '/api/v1/knowledge/collections/c1',
      ),
      isTrue,
    );
    expect(find.text('spider-facts'), findsNothing);
    expect(find.text('flutter-docs'), findsOneWidget);
    expect(
      find.text('Select a collection to see its documents.'),
      findsOneWidget,
    );
  });

  // IT-UI-01: a failed delete surfaces the controller's error and the
  // collection stays in the (still fresh) list.
  testWidgets('failed delete shows the error and keeps the collection', (
    tester,
  ) async {
    final controller = deletableController([
      collectionJson('c1', 'spider-facts'),
    ], deleteStatus: 502);
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: KnowledgeScreen())),
    );
    await settle(tester);

    await tester.tap(find.byTooltip('Delete collection'));
    await settle(tester);
    await tester.tap(find.text('Delete'));
    await settle(tester);

    expect(
      find.text('Qdrant delete failed — is the qdrant service running?'),
      findsOneWidget,
    );
    expect(find.text('spider-facts'), findsOneWidget);
  });

  testWidgets('admin screen shows audit log and revokes keys', (tester) async {
    final captured = <http.Request>[];
    final controller = fakeController(captured: captured);
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: AdminScreen())),
    );
    await settle(tester);

    expect(find.text('auth.login.success'), findsOneWidget);
    expect(find.text('192.168.1.9'), findsOneWidget);
    expect(find.textContaining('garage-box'), findsOneWidget);

    await tester.tap(find.text('Revoke'));
    await settle(tester);

    expect(
      captured.any(
        (r) =>
            r.method == 'DELETE' && r.url.path == '/api/v1/admin/api-keys/k1',
      ),
      isTrue,
    );
  });

  testWidgets('operator sees a requires-admin note instead of admin data', (
    tester,
  ) async {
    final controller = fakeController(role: 'operator');
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: AdminScreen())),
    );
    await settle(tester);

    expect(find.textContaining('requires the admin role'), findsOneWidget);
    expect(find.text('Audit log'), findsNothing);
  });
}
