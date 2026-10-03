import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/features/llm/ai_providers_screen.dart';

import 'llm_client_test.dart' show accountJson, providerJson;
import 'tasks_workflows_screens_test.dart' show appWith, settle;

MockClient fakeLlmController({
  List<http.Request>? captured,
  String probeStatus = 'unauthorized',
}) {
  return MockClient((request) async {
    captured?.add(request);
    final path = request.url.path;
    Map<String, dynamic>? routing;
    if (path == '/api/v1/me') {
      return http.Response(
        jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
        200,
      );
    }
    if (path == '/api/v1/llm/providers') {
      return http.Response(
        jsonEncode([
          providerJson(
            'openai',
            'OpenAI',
            note: 'ChatGPT Plus/Pro is a consumer subscription',
          ),
          providerJson(
            'ollama',
            'Ollama',
            kind: 'local',
            auth: ['none', 'api_key'],
          ),
        ]),
        200,
      );
    }
    if (path == '/api/v1/llm/accounts') {
      return http.Response(
        jsonEncode([accountJson(apiAccess: 'available')]),
        200,
      );
    }
    if (path == '/api/v1/llm/routing') {
      routing = {
        'purposes': ['default', 'coding', 'private'],
        'personal': [
          {
            'purpose': 'default',
            'scope': 'personal',
            'chain': [
              {'account_id': 'a1', 'model': 'gpt-x'},
            ],
            'updated_at': '2026-10-02T10:00:00Z',
          },
        ],
        'deployment': [],
      };
      return http.Response(jsonEncode(routing), 200);
    }
    if (path == '/api/v1/llm/accounts/a1/test') {
      return http.Response(
        jsonEncode({
          'status': probeStatus,
          'detail': probeStatus == 'ready'
              ? ''
              : 'The provider rejected the credential',
          'latency_ms': 12,
          'models_available': probeStatus == 'ready' ? 3 : null,
          'api_access': probeStatus == 'ready' ? 'available' : 'unavailable',
          'subscription_note': probeStatus == 'ready'
              ? null
              : 'ChatGPT Plus/Pro is a consumer subscription',
        }),
        200,
      );
    }
    if (path == '/api/v1/llm/accounts/a1' && request.method == 'DELETE') {
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

Future<void> pumpScreen(WidgetTester tester, MockClient controller) async {
  tester.view.physicalSize = const Size(1600, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    appWith(controller, home: const Scaffold(body: AiProvidersScreen())),
  );
  await settle(tester);
}

void main() {
  testWidgets('lists providers, connected accounts and the default route', (
    tester,
  ) async {
    await pumpScreen(tester, fakeLlmController());
    expect(find.text('AI Providers'), findsOneWidget);
    expect(find.text('OpenAI'), findsOneWidget);
    expect(find.text('Ollama'), findsOneWidget);
    expect(find.text('Personal'), findsOneWidget); // the connected account
    expect(find.textContaining('API access: available'), findsOneWidget);
    expect(find.text('Not connected'), findsOneWidget); // Ollama
    expect(find.text('Default: OpenAI · Personal · gpt-x'), findsOneWidget);
    // unknown capabilities are shown as unknown, never as supported
    expect(find.text('? Tools'), findsOneWidget); // Ollama: per-model
    expect(find.text('✓ Tools'), findsOneWidget); // OpenAI API-level
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'a refused key shows that the account is connected without API access',
    (tester) async {
      await pumpScreen(tester, fakeLlmController());
      await tester.tap(find.text('Test'));
      await settle(tester);
      expect(find.textContaining('Account connected'), findsOneWidget);
      expect(
        find.textContaining(
          'API access is not available through this subscription',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining("Configure the provider's API access separately"),
        findsOneWidget,
      );
      expect(find.textContaining('consumer subscription'), findsOneWidget);
    },
  );

  testWidgets('a working account reports its model count', (tester) async {
    await pumpScreen(tester, fakeLlmController(probeStatus: 'ready'));
    await tester.tap(find.text('Test'));
    await settle(tester);
    expect(find.textContaining('3 model(s) available'), findsOneWidget);
  });

  testWidgets('disconnect asks first, then deletes the account', (
    tester,
  ) async {
    final captured = <http.Request>[];
    await pumpScreen(tester, fakeLlmController(captured: captured));
    await tester.tap(find.text('Disconnect').first);
    await settle(tester);
    expect(
      find.textContaining('Revoke the key at the provider'),
      findsOneWidget,
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Disconnect'));
    await settle(tester);
    expect(
      captured.any(
        (r) => r.method == 'DELETE' && r.url.path == '/api/v1/llm/accounts/a1',
      ),
      isTrue,
    );
  });

  testWidgets('connect dialog explains subscriptions and hides the key', (
    tester,
  ) async {
    await pumpScreen(tester, fakeLlmController());
    await tester.tap(find.text('Add account'));
    await settle(tester);
    expect(find.text('Connect OpenAI'), findsOneWidget);
    expect(find.textContaining('consumer subscription'), findsWidgets);
    final keyField = tester.widget<TextField>(
      find.widgetWithText(TextField, 'API key'),
    );
    expect(keyField.obscureText, isTrue);
    // operators get no shared-scope selector; endpoint is fixed for cloud
    expect(find.text('Who can use it'), findsNothing);
    expect(find.text('Endpoint URL'), findsNothing);
  });
}
