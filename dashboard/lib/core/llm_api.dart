part of 'api_client.dart';

/// Universal LLM layer (ADR-031): providers, accounts, models, routing.
/// Credentials are write-only: no response type here can carry one.

/// True / false / null (= unknown). Unknown is never shown as supported.
class LlmCapabilities {
  LlmCapabilities(this.values);

  final Map<String, bool?> values;

  static const labels = {
    'streaming': 'Streaming',
    'tool_calling': 'Tools',
    'parallel_tools': 'Parallel tools',
    'structured_output': 'JSON schema',
    'json_mode': 'JSON mode',
    'vision': 'Vision',
    'reasoning': 'Reasoning',
  };

  bool? operator [](String key) => values[key];

  factory LlmCapabilities.fromJson(Map<String, dynamic>? json) =>
      LlmCapabilities({
        for (final entry in (json ?? const {}).entries)
          entry.key: entry.value as bool?,
      });
}

class LlmProvider {
  LlmProvider({
    required this.id,
    required this.displayName,
    required this.kind,
    required this.authMethods,
    required this.credentialRequired,
    required this.baseUrlMode,
    required this.defaultBaseUrl,
    required this.officialBaseUrls,
    required this.capabilities,
    required this.docsUrl,
    this.subscriptionNote,
    this.notes = const [],
  });

  final String id;
  final String displayName;
  final String kind; // cloud | aggregator | local | compatible | node
  final List<String> authMethods;
  final bool credentialRequired;
  final String baseUrlMode; // fixed | official_choice | user
  final String defaultBaseUrl;
  final List<String> officialBaseUrls;
  final LlmCapabilities capabilities;
  final String docsUrl;
  final String? subscriptionNote;
  final List<String> notes;

  bool get isLocal => kind == 'local';

  /// Runs on a Lycosa node under the node owner's own sign-in (ADR-032):
  /// no key, no endpoint, personal only.
  bool get isNodeRun => kind == 'node';
  bool get supportsOAuth => authMethods.contains('oauth_pkce');
  bool get acceptsKeylessAccount => authMethods.contains('none');
  bool get takesBaseUrl => baseUrlMode != 'fixed' && !isNodeRun;

  factory LlmProvider.fromJson(Map<String, dynamic> json) => LlmProvider(
    id: json['id'] as String,
    displayName: json['display_name'] as String,
    kind: json['kind'] as String,
    authMethods: (json['auth_methods'] as List).cast<String>(),
    credentialRequired: json['credential_required'] as bool,
    baseUrlMode: json['base_url_mode'] as String,
    defaultBaseUrl: json['default_base_url'] as String,
    officialBaseUrls: (json['official_base_urls'] as List? ?? const [])
        .cast<String>(),
    capabilities: LlmCapabilities.fromJson(
      json['capabilities'] as Map<String, dynamic>?,
    ),
    docsUrl: json['docs_url'] as String,
    subscriptionNote: json['subscription_note'] as String?,
    notes: (json['notes'] as List? ?? const []).cast<String>(),
  );
}

class LlmAccount {
  LlmAccount({
    required this.id,
    required this.provider,
    required this.providerName,
    required this.label,
    required this.scope,
    required this.mine,
    required this.usable,
    required this.manageable,
    required this.authMethod,
    required this.baseUrl,
    required this.isLocal,
    required this.status,
    required this.apiAccess,
    required this.credentialConfigured,
    this.lastTestStatus,
    this.lastTestDetail,
    this.lastTestedAt,
    this.nodeId,
    this.nodeName,
  });

  final String id;
  final String provider;
  final String providerName;
  final String label;
  final String scope; // personal | deployment
  final bool mine;
  final bool usable;
  final bool manageable;
  final String authMethod;
  final String baseUrl;
  final bool isLocal;
  final String status; // active | disabled
  final String apiAccess; // available | unavailable | unknown
  final bool credentialConfigured;
  final String? lastTestStatus;
  final String? lastTestDetail;
  final DateTime? lastTestedAt;
  final String? nodeId;
  final String? nodeName;

  bool get isShared => scope == 'deployment';
  bool get isActive => status == 'active';
  bool get isNodeRun => authMethod == 'node_sign_in';

  factory LlmAccount.fromJson(Map<String, dynamic> json) => LlmAccount(
    id: json['id'] as String,
    provider: json['provider'] as String,
    providerName: json['provider_name'] as String,
    label: json['label'] as String,
    scope: json['scope'] as String,
    mine: json['mine'] as bool,
    usable: json['usable'] as bool,
    manageable: json['manageable'] as bool,
    authMethod: json['auth_method'] as String,
    baseUrl: json['base_url'] as String,
    isLocal: json['is_local'] as bool,
    status: json['status'] as String,
    apiAccess: json['api_access'] as String,
    credentialConfigured: json['credential_configured'] as bool,
    lastTestStatus: json['last_test_status'] as String?,
    lastTestDetail: json['last_test_detail'] as String?,
    lastTestedAt: json['last_tested_at'] != null
        ? DateTime.parse(json['last_tested_at'] as String)
        : null,
    nodeId: json['node_id'] as String?,
    nodeName: json['node_name'] as String?,
  );
}

/// One usage window of a Claude plan (e.g. five_hour, seven_day).
class PlanWindow {
  const PlanWindow({required this.name, this.utilization, this.resetsAt});

  final String name;
  final double? utilization; // 0..1
  final DateTime? resetsAt;

  String get label => switch (name) {
    'five_hour' => '5-hour',
    'seven_day' => '7-day',
    _ => name.replaceAll('_', ' '),
  };
}

/// A node offering the Claude subscription runtime, as its agent reports it
/// for the signed-in user. Never carries the Claude account's identity.
class ClaudeNode {
  ClaudeNode({
    required this.nodeId,
    required this.nodeName,
    required this.nodeStatus,
    required this.reachable,
    required this.installed,
    required this.signedIn,
    required this.authMethod,
    required this.ownerMatches,
    required this.detail,
    this.version,
    this.plan,
    this.planUsage = const [],
  });

  final String nodeId;
  final String nodeName;
  final String nodeStatus;
  final bool reachable;
  final bool installed;
  final bool signedIn;
  final String authMethod; // subscription | api_key | other | none
  final bool ownerMatches;
  final String detail;
  final String? version;
  final String? plan;
  final List<PlanWindow> planUsage;

  /// Ready to connect: reachable, installed, signed in and serving you.
  bool get ready => reachable && installed && signedIn && ownerMatches;

  factory ClaudeNode.fromJson(Map<String, dynamic> json) => ClaudeNode(
    nodeId: json['node_id'] as String,
    nodeName: json['node_name'] as String,
    nodeStatus: json['node_status'] as String? ?? 'unknown',
    reachable: json['reachable'] as bool? ?? false,
    installed: json['installed'] as bool? ?? false,
    signedIn: json['signed_in'] as bool? ?? false,
    authMethod: json['auth_method'] as String? ?? 'none',
    ownerMatches: json['owner_matches'] as bool? ?? false,
    detail: json['detail'] as String? ?? '',
    version: json['version'] as String?,
    plan: json['plan'] as String?,
    planUsage: [
      for (final entry
          in ((json['plan_usage'] as Map<String, dynamic>?) ?? const {})
              .entries)
        PlanWindow(
          name: entry.key,
          utilization: ((entry.value as Map)['utilization'] as num?)
              ?.toDouble(),
          resetsAt: (entry.value as Map)['resets_at'] is int
              ? DateTime.fromMillisecondsSinceEpoch(
                  ((entry.value as Map)['resets_at'] as int) * 1000,
                )
              : null,
        ),
    ],
  );
}

class LlmChatMessage {
  const LlmChatMessage({required this.role, required this.content});

  final String role; // user | assistant
  final String content;

  Map<String, dynamic> toJson() => {'role': role, 'content': content};
}

/// One server-sent event of a streamed chat (ADR-031 normalized chunks).
class LlmChatEvent {
  const LlmChatEvent({
    required this.type,
    this.text,
    this.provider,
    this.model,
    this.totalTokens,
    this.finishReason,
    this.errorCode,
    this.errorMessage,
  });

  /// start | text | reasoning | usage | finish | done | error (others ignored)
  final String type;
  final String? text;
  final String? provider;
  final String? model;
  final int? totalTokens;
  final String? finishReason;
  final String? errorCode;
  final String? errorMessage;

  factory LlmChatEvent.fromSse(String type, Map<String, dynamic> data) {
    final metadata = (data['metadata'] as Map<String, dynamic>?) ?? const {};
    final usage = data['usage'] as Map<String, dynamic>?;
    return LlmChatEvent(
      type: type,
      text: data['text'] as String?,
      provider: metadata['provider'] as String?,
      model: metadata['model'] as String?,
      totalTokens: usage?['total_tokens'] as int?,
      finishReason: data['finish_reason'] as String?,
      errorCode: type == 'error' ? data['code'] as String? : null,
      errorMessage: type == 'error' ? data['message'] as String? : null,
    );
  }
}

class LlmProbeResult {
  LlmProbeResult({
    required this.status,
    required this.detail,
    required this.apiAccess,
    this.latencyMs,
    this.modelsAvailable,
    this.subscriptionNote,
  });

  final String status; // ready | offline | unauthorized | error | unknown
  final String detail;
  final String apiAccess;
  final int? latencyMs;
  final int? modelsAvailable;
  final String? subscriptionNote;

  factory LlmProbeResult.fromJson(Map<String, dynamic> json) => LlmProbeResult(
    status: json['status'] as String,
    detail: json['detail'] as String? ?? '',
    apiAccess: json['api_access'] as String,
    latencyMs: json['latency_ms'] as int?,
    modelsAvailable: json['models_available'] as int?,
    subscriptionNote: json['subscription_note'] as String?,
  );
}

class LlmModel {
  LlmModel({
    required this.ref,
    required this.id,
    required this.provider,
    required this.accountId,
    required this.accountLabel,
    required this.capabilities,
    this.displayName,
    this.contextWindow,
    this.maxOutputTokens,
  });

  final String ref;
  final String id;
  final String provider;
  final String accountId;
  final String accountLabel;
  final LlmCapabilities capabilities;
  final String? displayName;
  final int? contextWindow;
  final int? maxOutputTokens;

  factory LlmModel.fromJson(Map<String, dynamic> json) => LlmModel(
    ref: json['ref'] as String,
    id: json['id'] as String,
    provider: json['provider'] as String,
    accountId: json['account_id'] as String,
    accountLabel: json['account_label'] as String,
    capabilities: LlmCapabilities.fromJson(
      json['capabilities'] as Map<String, dynamic>?,
    ),
    displayName: json['display_name'] as String?,
    contextWindow: json['context_window'] as int?,
    maxOutputTokens: json['max_output_tokens'] as int?,
  );
}

class LlmRouteEntry {
  const LlmRouteEntry({required this.accountId, required this.model});

  final String accountId;
  final String model;

  Map<String, dynamic> toJson() => {'account_id': accountId, 'model': model};

  factory LlmRouteEntry.fromJson(Map<String, dynamic> json) => LlmRouteEntry(
    accountId: json['account_id'] as String,
    model: json['model'] as String,
  );
}

class LlmRoute {
  LlmRoute({required this.purpose, required this.scope, required this.chain});

  final String purpose;
  final String scope;
  final List<LlmRouteEntry> chain;

  factory LlmRoute.fromJson(Map<String, dynamic> json) => LlmRoute(
    purpose: json['purpose'] as String,
    scope: json['scope'] as String,
    chain: [
      for (final e in json['chain'] as List)
        LlmRouteEntry.fromJson(e as Map<String, dynamic>),
    ],
  );
}

class LlmRouting {
  LlmRouting({
    required this.purposes,
    required this.personal,
    required this.deployment,
  });

  final List<String> purposes;
  final List<LlmRoute> personal;
  final List<LlmRoute> deployment;

  LlmRoute? routeFor(String purpose, String scope) {
    for (final r in scope == 'deployment' ? deployment : personal) {
      if (r.purpose == purpose) return r;
    }
    return null;
  }

  factory LlmRouting.fromJson(Map<String, dynamic> json) => LlmRouting(
    purposes: (json['purposes'] as List).cast<String>(),
    personal: [
      for (final r in json['personal'] as List)
        LlmRoute.fromJson(r as Map<String, dynamic>),
    ],
    deployment: [
      for (final r in json['deployment'] as List)
        LlmRoute.fromJson(r as Map<String, dynamic>),
    ],
  );
}

class LlmAnswer {
  LlmAnswer({
    required this.content,
    required this.provider,
    required this.model,
    required this.finishReason,
    required this.latencyMs,
    required this.fallbackIndex,
    this.totalTokens,
    this.estimatedCost,
  });

  final String content;
  final String provider;
  final String model;
  final String finishReason;
  final int latencyMs;
  final int fallbackIndex;
  final int? totalTokens;
  final double? estimatedCost;

  factory LlmAnswer.fromJson(Map<String, dynamic> json) => LlmAnswer(
    content: json['content'] as String,
    provider: json['provider'] as String,
    model: json['model'] as String,
    finishReason: json['finish_reason'] as String,
    latencyMs: json['latency_ms'] as int,
    fallbackIndex: json['fallback_index'] as int,
    totalTokens:
        (json['usage'] as Map<String, dynamic>?)?['total_tokens'] as int?,
    estimatedCost: (json['estimated_cost'] as num?)?.toDouble(),
  );
}

class OpenRouterSignIn {
  OpenRouterSignIn({required this.authorizationUrl, required this.flow});

  final String authorizationUrl;
  final String flow;
}

const _loopbackHosts = ['localhost', '127.0.0.1', '::1'];

extension LlmApi on ApiClient {
  /// Credential-bearing requests: HTTPS (or a loopback controller) only, and
  /// redirects refused, as for the legacy provider keys.
  Future<http.Response> _sendSecret(
    String method,
    Uri uri,
    Map<String, dynamic> body,
  ) async {
    if (uri.scheme != 'https' &&
        !(uri.scheme == 'http' && _loopbackHosts.contains(uri.host))) {
      throw StateError('API keys require HTTPS or a loopback controller URL.');
    }
    final response = await _send(() async {
      final request = http.Request(method, uri)
        ..followRedirects = false
        ..headers.addAll(_headers)
        ..body = jsonEncode(body);
      return http.Response.fromStream(await _http.send(request));
    }, timeout: const Duration(seconds: 60));
    if (response.statusCode >= 300 && response.statusCode < 400) {
      throw StateError('Credential redirects are not allowed.');
    }
    return response;
  }

  Future<List<LlmProvider>> listLlmProviders() async {
    final response = await _send(
      () => _http.get(_uri('/api/v1/llm/providers'), headers: _headers),
    );
    return [
      for (final p in _decodeList(response))
        LlmProvider.fromJson(p as Map<String, dynamic>),
    ];
  }

  Future<List<LlmAccount>> listLlmAccounts() async {
    final response = await _send(
      () => _http.get(_uri('/api/v1/llm/accounts'), headers: _headers),
    );
    return [
      for (final a in _decodeList(response))
        LlmAccount.fromJson(a as Map<String, dynamic>),
    ];
  }

  Future<LlmAccount> connectLlmAccount({
    required String provider,
    required String label,
    String scope = 'personal',
    String? baseUrl,
    String? apiKey,
    String? nodeId,
  }) async {
    final body = {
      'provider': provider,
      'label': label,
      'scope': scope,
      'base_url': ?baseUrl,
      'api_key': ?apiKey,
      'node_id': ?nodeId,
    };
    final uri = _uri('/api/v1/llm/accounts');
    final response = apiKey != null
        ? await _sendSecret('POST', uri, body)
        : await _send(
            () => _http.post(uri, headers: _headers, body: jsonEncode(body)),
            timeout: const Duration(seconds: 60),
          );
    return LlmAccount.fromJson(_decode(response));
  }

  Future<LlmAccount> updateLlmAccount(
    String id, {
    String? label,
    String? status,
    String? baseUrl,
    String? apiKey,
  }) async {
    final body = {
      'label': ?label,
      'status': ?status,
      'base_url': ?baseUrl,
      'api_key': ?apiKey,
    };
    final uri = _uri('/api/v1/llm/accounts/$id');
    final response = apiKey != null
        ? await _sendSecret('PATCH', uri, body)
        : await _send(
            () => _http.patch(uri, headers: _headers, body: jsonEncode(body)),
          );
    return LlmAccount.fromJson(_decode(response));
  }

  Future<void> disconnectLlmAccount(String id) async {
    final response = await _send(
      () => _http.delete(_uri('/api/v1/llm/accounts/$id'), headers: _headers),
    );
    if (response.statusCode >= 400) _decode(response);
  }

  Future<LlmProbeResult> testLlmAccount(String id, {String? model}) async {
    final response = await _send(
      () => _http.post(
        _uri('/api/v1/llm/accounts/$id/test'),
        headers: _headers,
        body: jsonEncode({'model': ?model}),
      ),
      timeout: const Duration(minutes: 3),
    );
    return LlmProbeResult.fromJson(_decode(response));
  }

  Future<List<LlmModel>> listLlmAccountModels(
    String id, {
    bool refresh = false,
  }) async {
    final response = await _send(
      () => _http.get(
        _uri(
          '/api/v1/llm/accounts/$id/models${refresh ? '?refresh=true' : ''}',
        ),
        headers: _headers,
      ),
      timeout: const Duration(minutes: 2),
    );
    return [
      for (final m in (_decode(response)['models'] as List))
        LlmModel.fromJson(m as Map<String, dynamic>),
    ];
  }

  Future<LlmRouting> getLlmRouting() async {
    final response = await _send(
      () => _http.get(_uri('/api/v1/llm/routing'), headers: _headers),
    );
    return LlmRouting.fromJson(_decode(response));
  }

  Future<LlmRoute> setLlmRoute(
    String purpose,
    List<LlmRouteEntry> chain, {
    String scope = 'personal',
  }) async {
    final response = await _send(
      () => _http.put(
        _uri('/api/v1/llm/routing/$purpose'),
        headers: _headers,
        body: jsonEncode({
          'scope': scope,
          'chain': [for (final e in chain) e.toJson()],
        }),
      ),
    );
    return LlmRoute.fromJson(_decode(response));
  }

  Future<void> deleteLlmRoute(
    String purpose, {
    String scope = 'personal',
  }) async {
    final response = await _send(
      () => _http.delete(
        _uri('/api/v1/llm/routing/$purpose?scope=$scope'),
        headers: _headers,
      ),
    );
    if (response.statusCode >= 400) _decode(response);
  }

  /// One prompt through an account (+model) or a routing purpose.
  Future<LlmAnswer> llmTestPrompt({
    required String prompt,
    String? accountId,
    String? model,
    String? purpose,
  }) async {
    final response = await _send(
      () => _http.post(
        _uri('/api/v1/llm/test'),
        headers: _headers,
        body: jsonEncode({
          'prompt': prompt,
          'account_id': ?accountId,
          'model': ?model,
          'purpose': ?purpose,
        }),
      ),
      timeout: const Duration(minutes: 7),
    );
    return LlmAnswer.fromJson(_decode(response));
  }

  /// Nodes offering the Claude subscription runtime, each asked live
  /// whether Claude Code is installed, signed in and serving you (ADR-032).
  Future<List<ClaudeNode>> listClaudeSubscriptionNodes() async {
    final response = await _send(
      () => _http.get(
        _uri('/api/v1/llm/claude-subscription/nodes'),
        headers: _headers,
      ),
      timeout: const Duration(seconds: 45),
    );
    return [
      for (final n in _decodeList(response))
        ClaudeNode.fromJson(n as Map<String, dynamic>),
    ];
  }

  /// A streamed chat through an account (+model) or a routing purpose:
  /// normalized server-sent events, ending with `done` or `error`.
  Stream<LlmChatEvent> llmChatStream({
    required List<LlmChatMessage> messages,
    String? accountId,
    String? model,
    String? purpose,
    String? system,
  }) async* {
    final request = http.Request('POST', _uri('/api/v1/llm/chat'))
      ..followRedirects = false
      ..headers.addAll(_headers)
      ..headers['Accept'] = 'text/event-stream'
      ..body = jsonEncode({
        'messages': [for (final m in messages) m.toJson()],
        'account_id': ?accountId,
        'model': ?model,
        'purpose': ?purpose,
        'system': ?system,
        'stream': true,
      });
    final http.StreamedResponse response;
    try {
      response = await _http.send(request).timeout(const Duration(minutes: 2));
    } on SocketException catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    } on http.ClientException catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    } on Exception catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    }
    if (response.statusCode >= 400) {
      final body = await response.stream.bytesToString();
      _decode(http.Response(body, response.statusCode)); // throws ApiException
    }
    String event = 'message';
    final data = <String>[];
    await for (final line
        in response.stream
            .transform(utf8.decoder)
            .transform(const LineSplitter())) {
      if (line.isEmpty) {
        if (data.isNotEmpty) {
          final decoded = jsonDecode(data.join('\n'));
          yield LlmChatEvent.fromSse(
            event,
            decoded is Map<String, dynamic> ? decoded : const {},
          );
        }
        event = 'message';
        data.clear();
        continue;
      }
      if (line.startsWith(':')) continue;
      final colon = line.indexOf(':');
      final name = colon < 0 ? line : line.substring(0, colon);
      var value = colon < 0 ? '' : line.substring(colon + 1);
      if (value.startsWith(' ')) value = value.substring(1);
      if (name == 'event') event = value;
      if (name == 'data') data.add(value);
    }
  }

  Future<OpenRouterSignIn> startOpenRouterSignIn({String? callbackUrl}) async {
    final response = await _send(
      () => _http.post(
        _uri('/api/v1/llm/oauth/openrouter/start'),
        headers: _headers,
        body: jsonEncode({'callback_url': ?callbackUrl}),
      ),
    );
    final body = _decode(response);
    return OpenRouterSignIn(
      authorizationUrl: body['authorization_url'] as String,
      flow: body['flow'] as String,
    );
  }

  Future<LlmAccount> completeOpenRouterSignIn({
    required String flow,
    required String code,
    required String label,
    String scope = 'personal',
  }) async {
    final response = await _sendSecret(
      'POST',
      _uri('/api/v1/llm/oauth/openrouter/complete'),
      {'flow': flow, 'code': code, 'label': label, 'scope': scope},
    );
    return LlmAccount.fromJson(_decode(response));
  }
}
