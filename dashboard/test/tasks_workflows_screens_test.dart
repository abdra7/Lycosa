import 'dart:async';
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

  group('default route', () {
    // Sprint 14 computer-use walkthrough: Tasks opened on the node/Ollama
    // route even when no node had Ollama and a default model was set
    MockClient routedController({
      required bool withDefault,
      List<http.Request>? captured,
    }) => MockClient((request) async {
      captured?.add(request);
      switch (request.url.path) {
        case '/api/v1/me':
          return http.Response(
            jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
            200,
          );
        case '/api/v1/tasks' when request.method == 'POST':
          return http.Response(jsonEncode(taskJson()), 201);
        case '/api/v1/llm/routing':
          return http.Response(
            jsonEncode({
              'purposes': ['default', 'coding'],
              'personal': [],
              'deployment': [
                if (withDefault)
                  {
                    'purpose': 'default',
                    'scope': 'deployment',
                    'chain': [
                      {'account_id': 'a1', 'model': 'gpt-x'},
                    ],
                    'updated_at': '2026-10-16T10:00:00Z',
                  },
              ],
            }),
            200,
          );
      }
      return http.Response(jsonEncode([]), 200);
    });

    String? routeValue(WidgetTester tester) => tester
        .widget<DropdownButton<String>>(
          find.descendant(
            of: find.ancestor(
              of: find.text('Route'),
              matching: find.byType(DropdownButtonFormField<String>),
            ),
            matching: find.byType(DropdownButton<String>),
          ),
        )
        .value;

    testWidgets('a configured default model becomes the default route', (
      tester,
    ) async {
      final captured = <http.Request>[];
      await tester.pumpWidget(
        appWith(
          routedController(withDefault: true, captured: captured),
          home: const Scaffold(body: TasksScreen()),
        ),
      );
      await settle(tester);
      expect(routeValue(tester), 'default');

      await tester.enterText(
        find.widgetWithText(TextField, 'Prompt'),
        'say hi',
      );
      await tester.tap(find.text('Run task'));
      await settle(tester);
      final body =
          jsonDecode(
                captured
                    .lastWhere(
                      (r) =>
                          r.method == 'POST' && r.url.path == '/api/v1/tasks',
                    )
                    .body,
              )
              as Map<String, dynamic>;
      expect(body['route'], 'default');
      expect(body.containsKey('provider'), isFalse);
    });

    testWidgets('without a default model the node route stays', (tester) async {
      await tester.pumpWidget(
        appWith(
          routedController(withDefault: false),
          home: const Scaffold(body: TasksScreen()),
        ),
      );
      await settle(tester);
      expect(routeValue(tester), 'node');
    });
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
    // statuses read as words, not identifiers
    expect(find.text('approval · pending approval'), findsOneWidget);
    expect(find.textContaining('pending_approval'), findsNothing);
    expect(find.text('gate'), findsOneWidget);

    await tester.tap(find.text('Approve'));
    await settle(tester);

    final approve = captured
        .where((r) => r.url.path.endsWith('/approve'))
        .single;
    expect(jsonDecode(approve.body), {'approved': true});
    expect(find.textContaining('Status: succeeded'), findsOneWidget);
    expect(find.text('Approve'), findsNothing); // bar gone once resumed
  });
  testWidgets(
    'leaving Tasks while a task runs does not touch the disposed screen',
    (tester) async {
      // Sprint 14 live QA: switching sections during a long task called
      // setState() on the disposed TasksScreen when the answer arrived
      final release = Completer<void>();
      final controller = MockClient((request) async {
        final path = request.url.path;
        if (path == '/api/v1/tasks' && request.method == 'POST') {
          await release.future;
          return http.Response(jsonEncode(taskJson()), 201);
        }
        if (path == '/api/v1/me') {
          return http.Response(
            jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
            200,
          );
        }
        return http.Response(jsonEncode([]), 200);
      });
      final showTasks = ValueNotifier(true);
      await tester.pumpWidget(
        appWith(
          controller,
          home: Scaffold(
            body: ValueListenableBuilder<bool>(
              valueListenable: showTasks,
              builder: (_, show, _) =>
                  show ? const TasksScreen() : const Text('elsewhere'),
            ),
          ),
        ),
      );
      await settle(tester);
      await tester.enterText(
        find.widgetWithText(TextField, 'Prompt'),
        'a long task',
      );
      await tester.tap(find.text('Run task'));
      await tester.pump();
      showTasks.value = false; // the user opens another section
      await settle(tester);
      release.complete(); // the answer arrives afterwards
      await settle(tester);
      expect(find.text('elsewhere'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('approval that returns after the bar is gone does not throw', (
    tester,
  ) async {
    // Sprint 14 live QA: approving runs the next step before the reply, so
    // the bar can be gone (run resumed, user went back) when it returns;
    // ref.invalidate on the unmounted bar threw a StateError
    final release = Completer<void>();
    final controller = MockClient((request) async {
      final path = request.url.path;
      if (path.endsWith('/approve')) {
        await release.future;
        return http.Response(jsonEncode(runJson(status: 'succeeded')), 200);
      }
      if (path == '/api/v1/workflows/w1/runs/r1') {
        return http.Response(jsonEncode(runJson()), 200);
      }
      if (path == '/api/v1/me') {
        return http.Response(
          jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });
    final showRun = ValueNotifier(true);
    await tester.pumpWidget(
      appWith(
        controller,
        home: Scaffold(
          body: ValueListenableBuilder<bool>(
            valueListenable: showRun,
            builder: (_, show, _) => show
                ? const RunScreen(
                    workflowName: 'release',
                    workflowId: 'w1',
                    runId: 'r1',
                  )
                : const Text('elsewhere'),
          ),
        ),
      ),
    );
    await settle(tester);
    await tester.tap(find.text('Approve'));
    await tester.pump();
    showRun.value = false; // the user goes back while the step runs
    await settle(tester);
    release.complete();
    await settle(tester);
    expect(tester.takeException(), isNull);
  });
}
