import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';
import 'package:lycosa_dashboard/features/chat/chat_screen.dart';
import 'package:lycosa_dashboard/features/llm/ai_providers_screen.dart';
import 'package:lycosa_dashboard/features/tasks/tasks_screen.dart';

import 'api_client_tasks_test.dart' show taskJson;
import 'llm_client_test.dart' show accountJson, providerJson;
import 'tasks_workflows_screens_test.dart' show appWith, settle;

Map<String, dynamic> claudeProviderJson() => {
  ...providerJson('claude_subscription', 'Claude subscription'),
  'kind': 'node',
  'auth_methods': ['node_sign_in'],
  'credential_required': false,
  'base_url_mode': 'user',
  'default_base_url': '',
  'capabilities': {'chat': true, 'streaming': true, 'tool_calling': false},
  'notes': [
    'Runs Claude Code on one of your nodes, signed in with your own Claude '
        'plan. Lycosa never sees your Claude credentials.',
  ],
};

Map<String, dynamic> claudeAccountJson() => {
  ...accountJson(id: 'c1', provider: 'claude_subscription', label: 'My Claude'),
  'provider_name': 'Claude subscription',
  'auth_method': 'node_sign_in',
  'base_url': 'http://laptop:8010',
  'credential_configured': false,
  'node_id': 'n1',
  'node_name': 'laptop',
};

Map<String, dynamic> claudeNodeJson({
  String id = 'n1',
  String name = 'laptop',
  bool signedIn = true,
  bool ownerMatches = true,
}) => {
  'node_id': id,
  'node_name': name,
  'node_status': 'online',
  'reachable': true,
  'installed': true,
  'version': '2.1.300',
  'signed_in': signedIn,
  'auth_method': signedIn ? 'subscription' : 'none',
  'plan': signedIn ? 'pro' : null,
  'owner_matches': ownerMatches,
  'plan_usage': {
    'five_hour': {'utilization': 0.07, 'resets_at': 1792068000},
  },
  'detail': '',
};

String sse(List<(String, Map<String, dynamic>)> events) => [
  for (final (name, data) in events)
    'event: $name\ndata: ${jsonEncode(data)}\n\n',
].join();

final helloStream = sse([
  (
    'start',
    {
      'type': 'start',
      'metadata': {'provider': 'claude_subscription', 'model': 'sonnet'},
    },
  ),
  ('text', {'type': 'text', 'text': 'Hel'}),
  ('text', {'type': 'text', 'text': 'lo'}),
  (
    'usage',
    {
      'type': 'usage',
      'usage': {'total_tokens': 15},
    },
  ),
  ('finish', {'type': 'finish', 'finish_reason': 'stop'}),
  ('done', <String, dynamic>{}),
]);

MockClient fakeClaudeController({
  List<http.Request>? captured,
  List<Map<String, dynamic>>? nodes,
  String chatBody = '',
  bool withAccount = true,
}) {
  return MockClient((request) async {
    captured?.add(request);
    final path = request.url.path;
    switch (path) {
      case '/api/v1/me':
        return http.Response(
          jsonEncode({'type': 'user', 'id': 'u1', 'role': 'operator'}),
          200,
        );
      case '/api/v1/llm/providers':
        return http.Response(jsonEncode([claudeProviderJson()]), 200);
      case '/api/v1/llm/accounts' when request.method == 'GET':
        return http.Response(
          jsonEncode(withAccount ? [claudeAccountJson()] : []),
          200,
        );
      case '/api/v1/llm/accounts':
        return http.Response(jsonEncode(claudeAccountJson()), 201);
      case '/api/v1/llm/routing':
        return http.Response(
          jsonEncode({
            'purposes': ['default'],
            'personal': [],
            'deployment': [],
          }),
          200,
        );
      case '/api/v1/llm/claude-subscription/nodes':
        return http.Response(jsonEncode(nodes ?? [claudeNodeJson()]), 200);
      case '/api/v1/llm/accounts/c1/models':
        return http.Response(
          jsonEncode({
            'models': [
              for (final m in ['default', 'sonnet', 'opus', 'haiku'])
                {
                  'ref': 'claude_subscription:$m',
                  'provider': 'claude_subscription',
                  'id': m,
                  'account_id': 'c1',
                  'account_label': 'My Claude',
                  'display_name': m == 'default' ? 'Plan default' : m,
                  'context_window': null,
                  'max_output_tokens': null,
                  'capabilities': {'chat': true},
                },
            ],
            'errors': [],
          }),
          200,
        );
      case '/api/v1/llm/chat':
        return http.Response(
          chatBody,
          200,
          headers: {'content-type': 'text/event-stream'},
        );
      case '/api/v1/providers':
        return http.Response(jsonEncode([]), 200);
      case '/api/v1/tasks' when request.method == 'GET':
        return http.Response(jsonEncode([]), 200);
      case '/api/v1/tasks':
        return http.Response(jsonEncode(taskJson()), 201);
    }
    return http.Response(
      jsonEncode({
        'error': {'code': 'not_found', 'message': 'nope'},
      }),
      404,
    );
  });
}

Future<void> pump(
  WidgetTester tester,
  MockClient controller,
  Widget screen,
) async {
  tester.view.physicalSize = const Size(1600, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(appWith(controller, home: Scaffold(body: screen)));
  await settle(tester);
}

void main() {
  group('API client', () {
    test('lists Claude nodes with plan usage and never an identity', () async {
      final client = ApiClient(
        baseUrl: 'http://c:8000',
        token: 't',
        httpClient: fakeClaudeController(),
      );
      final nodes = await client.listClaudeSubscriptionNodes();
      expect(nodes.single.nodeName, 'laptop');
      expect(nodes.single.ready, isTrue);
      expect(nodes.single.plan, 'pro');
      expect(nodes.single.planUsage.single.label, '5-hour');
      expect(nodes.single.planUsage.single.utilization, 0.07);
    });

    test('connecting sends the node and no key', () async {
      final captured = <http.Request>[];
      final client = ApiClient(
        baseUrl: 'http://c:8000',
        token: 't',
        httpClient: fakeClaudeController(captured: captured),
      );
      final account = await client.connectLlmAccount(
        provider: 'claude_subscription',
        label: 'My Claude',
        nodeId: 'n1',
      );
      expect(account.isNodeRun, isTrue);
      expect(account.nodeName, 'laptop');
      final body = jsonDecode(captured.last.body) as Map<String, dynamic>;
      expect(body['node_id'], 'n1');
      expect(body.containsKey('api_key'), isFalse);
    });

    test('a streamed chat yields normalized events in order', () async {
      final captured = <http.Request>[];
      final client = ApiClient(
        baseUrl: 'http://c:8000',
        token: 't',
        httpClient: fakeClaudeController(
          captured: captured,
          chatBody: helloStream,
        ),
      );
      final events = await client
          .llmChatStream(
            messages: [const LlmChatMessage(role: 'user', content: 'hi')],
            accountId: 'c1',
            model: 'sonnet',
          )
          .toList();
      expect(events.map((e) => e.type), [
        'start',
        'text',
        'text',
        'usage',
        'finish',
        'done',
      ]);
      expect(
        events.where((e) => e.type == 'text').map((e) => e.text).join(),
        'Hello',
      );
      expect(events.first.provider, 'claude_subscription');
      expect(events[3].totalTokens, 15);
      final body = jsonDecode(captured.last.body) as Map<String, dynamic>;
      expect(body['stream'], isTrue);
      expect(body['account_id'], 'c1');
    });
  });

  group('Providers screen', () {
    testWidgets('the Claude card opens the node-based connect flow', (
      tester,
    ) async {
      final captured = <http.Request>[];
      await pump(
        tester,
        fakeClaudeController(captured: captured, withAccount: false),
        const AiProvidersScreen(),
      );
      expect(find.text('Claude subscription'), findsOneWidget);
      expect(find.text('Your plan, on your node'), findsOneWidget);
      expect(
        find.textContaining('never sees your Claude credentials'),
        findsOneWidget,
      );

      await tester.tap(find.text('Connect'));
      await settle(tester);
      expect(find.text('Connect your Claude plan'), findsOneWidget);
      expect(find.text('laptop'), findsOneWidget);
      expect(find.textContaining('Signed in · Pro plan'), findsOneWidget);
      expect(find.textContaining('5-hour: 7% used'), findsOneWidget);
      // no API key or endpoint field for a node-run provider
      expect(find.widgetWithText(TextField, 'API key'), findsNothing);

      await tester.tap(find.widgetWithText(FilledButton, 'Connect'));
      await settle(tester);
      final post = captured.lastWhere(
        (r) => r.method == 'POST' && r.url.path == '/api/v1/llm/accounts',
      );
      expect(jsonDecode(post.body)['node_id'], 'n1');
      expect(tester.takeException(), isNull);
    });

    testWidgets('a signed-out node shows how to sign in and blocks connect', (
      tester,
    ) async {
      await pump(
        tester,
        fakeClaudeController(
          withAccount: false,
          nodes: [claudeNodeJson(signedIn: false)],
        ),
        const AiProvidersScreen(),
      );
      await tester.tap(find.text('Connect'));
      await settle(tester);
      expect(
        find.text(
          'Not signed in\nClaude Code 2.1.300\nPlan usage · 5-hour: 7% used',
        ),
        findsOneWidget,
      );
      expect(find.text('Sign in on laptop'), findsOneWidget);
      expect(find.text('claude auth login'), findsOneWidget);
      final connect = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Connect'),
      );
      expect(connect.onPressed, isNull);
    });

    testWidgets('a node serving someone else cannot be connected', (
      tester,
    ) async {
      await pump(
        tester,
        fakeClaudeController(
          withAccount: false,
          nodes: [claudeNodeJson(ownerMatches: false)],
        ),
        const AiProvidersScreen(),
      );
      await tester.tap(find.text('Connect'));
      await settle(tester);
      expect(find.textContaining('Serves another Lycosa user'), findsOneWidget);
      final connect = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Connect'),
      );
      expect(connect.onPressed, isNull);
    });

    testWidgets('no Claude node explains how to enable one', (tester) async {
      await pump(
        tester,
        fakeClaudeController(withAccount: false, nodes: []),
        const AiProvidersScreen(),
      );
      await tester.tap(find.text('Connect'));
      await settle(tester);
      expect(find.textContaining('No node offers Claude yet'), findsOneWidget);
      expect(
        find.textContaining('LYCOSA_CLAUDE_SUBSCRIPTION=true'),
        findsOneWidget,
      );
    });

    testWidgets('a connected plan shows its node and plan wording', (
      tester,
    ) async {
      await pump(tester, fakeClaudeController(), const AiProvidersScreen());
      expect(find.text('My Claude'), findsOneWidget);
      expect(find.textContaining('Runs on laptop'), findsOneWidget);
      expect(
        find.textContaining('Claude plan: not tested yet'),
        findsOneWidget,
      );
      await tester.tap(find.text('Disconnect').first);
      await settle(tester);
      expect(find.textContaining('claude auth logout'), findsOneWidget);
    });
  });

  group('Chat screen', () {
    testWidgets('preselects the Claude plan and streams the answer', (
      tester,
    ) async {
      final captured = <http.Request>[];
      await pump(
        tester,
        fakeClaudeController(captured: captured, chatBody: helloStream),
        const ChatScreen(),
      );
      expect(find.textContaining('Runs Claude Code on laptop'), findsOneWidget);
      expect(find.text('Claude subscription · My Claude'), findsOneWidget);
      expect(find.text('Plan default'), findsOneWidget); // first model

      await tester.enterText(find.byKey(const Key('chat-input')), 'Say hello');
      await tester.tap(find.text('Send'));
      await settle(tester);
      expect(find.text('Say hello'), findsOneWidget);
      expect(find.text('Hello'), findsOneWidget);
      expect(find.textContaining('15 tokens'), findsOneWidget);
      final body =
          jsonDecode(
                captured
                    .lastWhere((r) => r.url.path == '/api/v1/llm/chat')
                    .body,
              )
              as Map<String, dynamic>;
      expect(body['account_id'], 'c1');
      expect(body['model'], 'default');
      expect(body['messages'], [
        {'role': 'user', 'content': 'Say hello'},
      ]);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a failed call shows the reason in the conversation', (
      tester,
    ) async {
      await pump(
        tester,
        fakeClaudeController(
          chatBody: sse([
            (
              'error',
              {
                'code': 'provider_quota_exceeded',
                'message':
                    "Your Claude plan's usage limit is reached; it resets later",
              },
            ),
          ]),
        ),
        const ChatScreen(),
      );
      await tester.enterText(find.byKey(const Key('chat-input')), 'hi');
      await tester.tap(find.text('Send'));
      await settle(tester);
      expect(find.textContaining('usage limit is reached'), findsOneWidget);
    });

    testWidgets('without accounts the empty state points to Providers', (
      tester,
    ) async {
      await pump(
        tester,
        fakeClaudeController(withAccount: false),
        const ChatScreen(),
      );
      expect(find.textContaining('Connect a model first'), findsOneWidget);
      expect(find.text('Default route'), findsOneWidget);
    });
  });

  testWidgets('Tasks can run on the Claude plan with the plan default model', (
    tester,
  ) async {
    final captured = <http.Request>[];
    await pump(
      tester,
      fakeClaudeController(captured: captured),
      const TasksScreen(),
    );
    await tester.tap(find.text('Provider above (node / legacy route)'));
    await settle(tester);
    await tester.tap(
      find.text('Account: Claude subscription · My Claude').last,
    );
    await settle(tester);
    expect(find.textContaining('Runs Claude Code on laptop'), findsOneWidget);
    await tester.enterText(
      find.widgetWithText(TextField, 'Prompt'),
      'Say hello',
    );
    await tester.tap(find.text('Run task'));
    await settle(tester);
    final body =
        jsonDecode(
              captured
                  .lastWhere(
                    (r) => r.method == 'POST' && r.url.path == '/api/v1/tasks',
                  )
                  .body,
            )
            as Map<String, dynamic>;
    expect(body['llm_account_id'], 'c1');
    expect(body['model'], 'default');
    expect(body.containsKey('route'), isFalse);
    expect(body.containsKey('provider'), isFalse);
  });
}
