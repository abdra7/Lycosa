import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lycosa_dashboard/core/api_client.dart';

import 'api_client_tasks_test.dart' show taskJson;

Map<String, dynamic> accountJson({
  String id = 'a1',
  String provider = 'openai',
  String label = 'Personal',
  String scope = 'personal',
  String apiAccess = 'unknown',
  bool usable = true,
  bool manageable = true,
}) => {
  'id': id,
  'provider': provider,
  'provider_name': provider == 'openai' ? 'OpenAI' : provider,
  'label': label,
  'scope': scope,
  'owner_user_id': scope == 'personal' ? 'u1' : null,
  'mine': scope == 'personal',
  'usable': usable,
  'manageable': manageable,
  'auth_method': 'api_key',
  'base_url': 'https://api.openai.com/v1',
  'is_local': false,
  'status': 'active',
  'api_access': apiAccess,
  'credential_configured': true,
  'last_tested_at': null,
  'last_test_status': null,
  'last_test_detail': null,
  'created_at': '2026-10-02T10:00:00Z',
  'updated_at': '2026-10-02T10:00:00Z',
};

Map<String, dynamic> providerJson(
  String id,
  String name, {
  String kind = 'cloud',
  List<String> auth = const ['api_key'],
  String? note,
}) => {
  'id': id,
  'display_name': name,
  'kind': kind,
  'auth_methods': auth,
  'credential_required': !auth.contains('none'),
  'base_url_mode': kind == 'local' ? 'user' : 'fixed',
  'default_base_url': kind == 'local'
      ? 'http://localhost:11434'
      : 'https://api.example',
  'official_base_urls': <String>[],
  'capabilities': {
    'chat': true,
    'streaming': true,
    'tool_calling': kind == 'local' ? null : true,
    'vision': null,
    'embeddings': false,
  },
  'docs_url': 'https://docs.example',
  'subscription_note': note,
  'notes': <String>[],
};

void main() {
  test(
    'connecting with a key requires HTTPS or loopback and refuses redirects',
    () async {
      var calls = 0;
      final transport = MockClient((request) async {
        calls++;
        expect(request.followRedirects, isFalse);
        return http.Response(
          '',
          307,
          headers: {'location': 'https://x.invalid'},
        );
      });
      final remote = ApiClient(
        baseUrl: 'http://lan-controller:8000',
        httpClient: transport,
      );
      await expectLater(
        remote.connectLlmAccount(
          provider: 'openai',
          label: 'P',
          apiKey: 'sk-x',
        ),
        throwsStateError,
      );
      expect(calls, 0); // the key never left over plain HTTP
      final local = ApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        httpClient: transport,
      );
      await expectLater(
        local.connectLlmAccount(provider: 'openai', label: 'P', apiKey: 'sk-x'),
        throwsStateError,
      );
      expect(calls, 1);
    },
  );

  test('keyless local accounts may use a plain-HTTP LAN controller', () async {
    final captured = <http.Request>[];
    final client = ApiClient(
      baseUrl: 'http://lan-controller:8000',
      httpClient: MockClient((r) async {
        captured.add(r);
        return http.Response(jsonEncode(accountJson(provider: 'ollama')), 201);
      }),
    );
    final account = await client.connectLlmAccount(
      provider: 'ollama',
      label: 'Lab',
      scope: 'deployment',
      baseUrl: 'http://192.168.1.20:11434',
    );
    expect(account.provider, 'ollama');
    expect(jsonDecode(captured.single.body), {
      'provider': 'ollama',
      'label': 'Lab',
      'scope': 'deployment',
      'base_url': 'http://192.168.1.20:11434',
    });
  });

  test(
    'accounts, probe results and models parse; unknown stays unknown',
    () async {
      final client = ApiClient(
        baseUrl: 'https://controller.invalid',
        httpClient: MockClient((r) async {
          switch (r.url.path) {
            case '/api/v1/llm/accounts':
              return http.Response(jsonEncode([accountJson()]), 200);
            case '/api/v1/llm/accounts/a1/test':
              return http.Response(
                jsonEncode({
                  'status': 'unauthorized',
                  'detail': 'The provider rejected the credential',
                  'latency_ms': null,
                  'models_available': null,
                  'api_access': 'unavailable',
                  'subscription_note':
                      'ChatGPT Plus is a consumer subscription',
                }),
                200,
              );
            default:
              return http.Response(
                jsonEncode({
                  'models': [
                    {
                      'ref': 'openai:gpt-x',
                      'provider': 'openai',
                      'id': 'gpt-x',
                      'account_id': 'a1',
                      'account_label': 'Personal',
                      'display_name': null,
                      'context_window': null,
                      'max_output_tokens': null,
                      'capabilities': {'tool_calling': true, 'vision': null},
                    },
                  ],
                  'errors': [],
                }),
                200,
              );
          }
        }),
      );
      final account = (await client.listLlmAccounts()).single;
      expect(account.isShared, isFalse);
      expect(account.apiAccess, 'unknown');
      final probe = await client.testLlmAccount('a1');
      expect(probe.status, 'unauthorized');
      expect(probe.subscriptionNote, contains('subscription'));
      final model = (await client.listLlmAccountModels('a1')).single;
      expect(model.capabilities['tool_calling'], isTrue);
      expect(model.capabilities['vision'], isNull);
    },
  );

  test('routes are written as ordered account+model chains', () async {
    final captured = <http.Request>[];
    final client = ApiClient(
      baseUrl: 'https://controller.invalid',
      httpClient: MockClient((r) async {
        captured.add(r);
        return http.Response(
          jsonEncode({
            'purpose': 'default',
            'scope': 'personal',
            'chain': jsonDecode(r.body)['chain'],
            'updated_at': '2026-10-02T10:00:00Z',
          }),
          200,
        );
      }),
    );
    await client.setLlmRoute('default', const [
      LlmRouteEntry(accountId: 'a1', model: 'gpt-x'),
      LlmRouteEntry(accountId: 'a2', model: 'claude-x'),
    ]);
    expect(captured.single.method, 'PUT');
    expect(captured.single.url.path, '/api/v1/llm/routing/default');
    expect(jsonDecode(captured.single.body), {
      'scope': 'personal',
      'chain': [
        {'account_id': 'a1', 'model': 'gpt-x'},
        {'account_id': 'a2', 'model': 'claude-x'},
      ],
    });
  });

  test('a task on an LLM route omits the legacy provider', () async {
    final client = ApiClient(
      baseUrl: 'http://localhost',
      httpClient: MockClient((r) async {
        final body = jsonDecode(r.body) as Map<String, dynamic>;
        expect(body['route'], 'coding');
        expect(body.containsKey('provider'), isFalse);
        return http.Response(jsonEncode(taskJson()), 201);
      }),
    );
    await client.submitTask(
      prompt: 'x',
      provider: 'openrouter',
      route: 'coding',
    );
  });

  test('OpenRouter sign-in: loopback callback out, sealed flow back', () async {
    final captured = <http.Request>[];
    final client = ApiClient(
      baseUrl: 'https://controller.invalid',
      httpClient: MockClient((r) async {
        captured.add(r);
        if (r.url.path.endsWith('/start')) {
          return http.Response(
            jsonEncode({
              'authorization_url':
                  'https://openrouter.ai/auth?code_challenge=c',
              'flow': 'sealed-flow',
              'expires_in': 600,
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode(accountJson(provider: 'openrouter')),
          201,
        );
      }),
    );
    final signIn = await client.startOpenRouterSignIn(
      callbackUrl: 'http://127.0.0.1:5123/callback',
    );
    expect(signIn.flow, 'sealed-flow');
    expect(jsonDecode(captured.first.body), {
      'callback_url': 'http://127.0.0.1:5123/callback',
    });
    await client.completeOpenRouterSignIn(
      flow: signIn.flow,
      code: 'abc',
      label: 'OpenRouter',
    );
    expect(jsonDecode(captured.last.body), {
      'flow': 'sealed-flow',
      'code': 'abc',
      'label': 'OpenRouter',
      'scope': 'personal',
    });
  });
}
