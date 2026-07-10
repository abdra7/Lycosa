import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/core/profiles.dart';
import 'package:lycosa_dashboard/core/session.dart';
import 'package:lycosa_dashboard/features/tasks/tasks_screen.dart';
import 'package:lycosa_dashboard/features/workflows/run_screen.dart';

import 'api_client_tasks_test.dart' show runJson, taskJson;

MockClient fakeController({List<http.Request>? captured}) {
  var approved = false;
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
    if (path == '/api/v1/tasks' && request.method == 'POST') {
      return http.Response(jsonEncode(taskJson()), 201);
    }
    if (path == '/api/v1/tasks' && request.method == 'GET') {
      return http.Response(jsonEncode([taskJson()]), 200);
    }
    if (path == '/api/v1/workflows/w1/runs/r1/approve') {
      approved = true;
      return http.Response(jsonEncode(runJson(status: 'succeeded')), 200);
    }
    if (path == '/api/v1/workflows/w1/runs/r1') {
      return http.Response(
        jsonEncode(runJson(status: approved ? 'succeeded' : 'paused')),
        200,
      );
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
  testWidgets('submitting a task shows the result inline', (tester) async {
    final controller = fakeController();
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: TasksScreen())),
    );
    await settle(tester);

    await tester.enterText(find.widgetWithText(TextField, 'Prompt'), 'say hi');
    await tester.tap(find.text('Run task'));
    await settle(tester);

    expect(find.text('hello!'), findsWidgets); // result panel + recent list
    expect(find.textContaining('succeeded'), findsWidgets);
  });

  testWidgets('task list shows the execution trace when expanded', (
    tester,
  ) async {
    final controller = fakeController();
    await tester.pumpWidget(
      appWith(controller, home: const Scaffold(body: TasksScreen())),
    );
    await settle(tester);

    expect(find.text('say hi'), findsOneWidget);
    await tester.tap(find.text('say hi'));
    await settle(tester);

    expect(find.textContaining('attempt 1: succeeded'), findsOneWidget);
  });

  testWidgets('paused run shows approval bar; approve resumes', (tester) async {
    final captured = <http.Request>[];
    final controller = fakeController(captured: captured);
    await tester.pumpWidget(
      appWith(
        controller,
        home: const RunScreen(
          workflowName: 'gated',
          workflowId: 'w1',
          runId: 'r1',
        ),
      ),
    );
    await settle(tester);

    expect(find.textContaining('paused'), findsWidgets);
    expect(find.text('draft'), findsOneWidget);
    expect(find.text('gate'), findsOneWidget);
    expect(find.textContaining('pending_approval'), findsOneWidget);

    await tester.tap(find.text('Approve'));
    await settle(tester);

    final approve = captured
        .where((r) => r.url.path.endsWith('/approve'))
        .single;
    expect(jsonDecode(approve.body), {'approved': true});
    expect(find.textContaining('Status: succeeded'), findsOneWidget);
    expect(find.text('Approve'), findsNothing); // bar gone once resumed
  });
}
