import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/features/tasks/tasks_screen.dart';
import 'api_client_tasks_test.dart' show taskJson;
import 'tasks_workflows_screens_test.dart' show appWith, fakeController, settle;

void main() {
  test('provider key blocks plaintext remote and redirects', () async {
    var calls = 0;
    final transport = MockClient((request) async {
      calls++;
      expect(request.followRedirects, isFalse);
      return http.Response(
        '',
        302,
        headers: {'location': 'https://other.invalid'},
      );
    });
    final remote = ApiClient(
      baseUrl: 'http://remote.invalid',
      httpClient: transport,
    );
    await expectLater(
      remote.saveProviderKey('openrouter', 'fixture', 'session'),
      throwsStateError,
    );
    expect(calls, 0);
    final local = ApiClient(
      baseUrl: 'http://127.0.0.1:8000',
      httpClient: transport,
    );
    await expectLater(
      local.saveProviderKey('openrouter', 'fixture', 'session'),
      throwsStateError,
    );
    expect(calls, 1);
  });

  test('provider key save and removal use explicit storage', () async {
    final captured = <http.Request>[];
    final client = ApiClient(
      baseUrl: 'https://controller.invalid',
      httpClient: MockClient((r) async {
        captured.add(r);
        return http.Response('', 204);
      }),
    );
    await client.saveProviderKey('openrouter', 'fixture-key', 'session');
    expect(jsonDecode(captured.first.body), {
      'key': 'fixture-key',
      'storage': 'session',
    });
    await client.deleteProviderKey('openrouter', 'session');
    expect(captured.last.url.queryParameters['storage'], 'session');
  });

  test('task request carries provider and scoped collection', () async {
    final client = ApiClient(
      baseUrl: 'http://localhost',
      httpClient: MockClient((r) async {
        final body = jsonDecode(r.body);
        expect(body['provider'], 'openrouter');
        expect(body['knowledge_collection'], 'qa-only');
        return http.Response(jsonEncode(taskJson()), 201);
      }),
    );
    await client.submitTask(
      prompt: 'test',
      provider: 'openrouter',
      knowledgeQuery: 'code',
      knowledgeCollection: 'qa-only',
    );
  });

  testWidgets(
    'selecting OpenRouter prefills exact model and warns about external context',
    (tester) async {
      await tester.pumpWidget(
        appWith(fakeController(), home: const Scaffold(body: TasksScreen())),
      );
      await settle(tester);
      await tester.tap(find.text('Ollama (local)').first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('OpenRouter (Nemotron free)').last);
      await tester.pumpAndSettle();
      expect(find.text('nvidia/nemotron-3.5-lightning:free'), findsOneWidget);
      expect(
        find.textContaining('prompt and retrieved context leave'),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );
}
