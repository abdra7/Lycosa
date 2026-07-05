import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'api_exception.dart';

/// The authenticated caller identity returned by GET /api/v1/me.
class Principal {
  Principal({
    required this.type,
    required this.id,
    required this.role,
    this.email,
    this.name,
  });

  final String type; // "user" | "api_key"
  final String id;
  final String role;
  final String? email;
  final String? name;

  factory Principal.fromJson(Map<String, dynamic> json) => Principal(
        type: json['type'] as String,
        id: json['id'] as String,
        role: json['role'] as String,
        email: json['email'] as String?,
        name: json['name'] as String?,
      );

  String get displayName => email ?? name ?? id;
}

/// A node in the fabric, as returned by /api/v1/nodes.
class NodeInfo {
  NodeInfo({
    required this.id,
    required this.name,
    required this.status,
    this.role,
    this.recommendedRole,
    this.recommendationConfidence,
    this.recommendationRationale = const [],
    this.cpuCores,
    this.ramGb,
    this.gpuCount,
    this.gpuVramGb,
    this.storageGb,
    this.osName,
    this.hardwareProfile,
    this.lastHeartbeatAt,
    this.metrics,
    this.agentUrl,
  });

  final String id;
  final String name;
  final String status; // registered | online | offline
  final String? role;
  final String? recommendedRole;
  final double? recommendationConfidence;
  final List<String> recommendationRationale;
  final int? cpuCores;
  final double? ramGb;
  final int? gpuCount;
  final double? gpuVramGb;
  final double? storageGb;
  final String? osName;
  final Map<String, dynamic>? hardwareProfile;
  final DateTime? lastHeartbeatAt;
  final Map<String, dynamic>? metrics;
  final String? agentUrl;

  factory NodeInfo.fromJson(Map<String, dynamic> json) => NodeInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        status: json['status'] as String,
        role: json['role'] as String?,
        recommendedRole: json['recommended_role'] as String?,
        recommendationConfidence:
            (json['recommendation_confidence'] as num?)?.toDouble(),
        recommendationRationale:
            (json['recommendation_rationale'] as List?)?.cast<String>() ?? const [],
        cpuCores: json['cpu_cores'] as int?,
        ramGb: (json['ram_gb'] as num?)?.toDouble(),
        gpuCount: json['gpu_count'] as int?,
        gpuVramGb: (json['gpu_vram_gb'] as num?)?.toDouble(),
        storageGb: (json['storage_gb'] as num?)?.toDouble(),
        osName: json['os_name'] as String?,
        hardwareProfile: json['hardware_profile'] as Map<String, dynamic>?,
        lastHeartbeatAt: json['last_heartbeat_at'] != null
            ? DateTime.parse(json['last_heartbeat_at'] as String)
            : null,
        metrics: json['metrics'] as Map<String, dynamic>?,
        agentUrl: json['agent_url'] as String?,
      );
}

const nodeRoles = [
  'ai_compute',
  'hybrid',
  'knowledge',
  'tool',
  'vision',
  'storage',
];

/// One-time response from minting an API key: the full key appears only here.
class MintedApiKey {
  MintedApiKey({required this.id, required this.name, required this.apiKey});

  final String id;
  final String name;
  final String apiKey;

  factory MintedApiKey.fromJson(Map<String, dynamic> json) => MintedApiKey(
        id: json['id'] as String,
        name: json['name'] as String,
        apiKey: json['api_key'] as String,
      );
}

class TaskExecutionInfo {
  TaskExecutionInfo({
    required this.nodeId,
    required this.attempt,
    required this.status,
    this.output,
    this.error,
  });

  final String nodeId;
  final int attempt;
  final String status;
  final String? output;
  final String? error;

  factory TaskExecutionInfo.fromJson(Map<String, dynamic> json) =>
      TaskExecutionInfo(
        nodeId: json['node_id'] as String,
        attempt: json['attempt'] as int,
        status: json['status'] as String,
        output: json['output'] as String?,
        error: json['error'] as String?,
      );
}

class TaskInfo {
  TaskInfo({
    required this.id,
    required this.type,
    required this.status,
    required this.payload,
    this.result,
    this.error,
    this.nodeId,
    required this.queuedAt,
    this.finishedAt,
    this.executions = const [],
  });

  final String id;
  final String type;
  final String status;
  final Map<String, dynamic> payload;
  final Map<String, dynamic>? result;
  final String? error;
  final String? nodeId;
  final DateTime queuedAt;
  final DateTime? finishedAt;
  final List<TaskExecutionInfo> executions;

  String get prompt => payload['prompt'] as String? ?? '';
  String? get output => result?['output'] as String?;

  factory TaskInfo.fromJson(Map<String, dynamic> json) => TaskInfo(
        id: json['id'] as String,
        type: json['type'] as String,
        status: json['status'] as String,
        payload: json['payload'] as Map<String, dynamic>? ?? const {},
        result: json['result'] as Map<String, dynamic>?,
        error: json['error'] as String?,
        nodeId: json['node_id'] as String?,
        queuedAt: DateTime.parse(json['queued_at'] as String),
        finishedAt: json['finished_at'] != null
            ? DateTime.parse(json['finished_at'] as String)
            : null,
        executions: (json['executions'] as List? ?? const [])
            .map((e) => TaskExecutionInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class WorkflowInfo {
  WorkflowInfo({
    required this.id,
    required this.name,
    this.description,
    required this.definition,
  });

  final String id;
  final String name;
  final String? description;
  final Map<String, dynamic> definition;

  factory WorkflowInfo.fromJson(Map<String, dynamic> json) => WorkflowInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        description: json['description'] as String?,
        definition: json['definition'] as Map<String, dynamic>? ?? const {},
      );
}

class StepRunInfo {
  StepRunInfo({
    required this.stepId,
    required this.kind,
    required this.status,
    required this.attempt,
    this.output,
    this.error,
  });

  final String stepId;
  final String kind;
  final String status;
  final int attempt;
  final String? output;
  final String? error;

  factory StepRunInfo.fromJson(Map<String, dynamic> json) => StepRunInfo(
        stepId: json['step_id'] as String,
        kind: json['kind'] as String,
        status: json['status'] as String,
        attempt: json['attempt'] as int,
        output: json['output'] as String?,
        error: json['error'] as String?,
      );
}

class WorkflowRunInfo {
  WorkflowRunInfo({
    required this.id,
    required this.workflowId,
    required this.status,
    required this.input,
    this.currentStep,
    this.error,
    this.stepRuns = const [],
  });

  final String id;
  final String workflowId;
  final String status; // running | paused | succeeded | failed
  final String input;
  final String? currentStep;
  final String? error;
  final List<StepRunInfo> stepRuns;

  bool get isPaused => status == 'paused';
  bool get isFinished => status == 'succeeded' || status == 'failed';

  factory WorkflowRunInfo.fromJson(Map<String, dynamic> json) => WorkflowRunInfo(
        id: json['id'] as String,
        workflowId: json['workflow_id'] as String,
        status: json['status'] as String,
        input: json['input'] as String,
        currentStep: json['current_step'] as String?,
        error: json['error'] as String?,
        stepRuns: (json['step_runs'] as List? ?? const [])
            .map((e) => StepRunInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

/// Hand-written typed client for the Lycosa controller API (ADR-015).
class ApiClient {
  ApiClient({required this.baseUrl, this.token, http.Client? httpClient})
      : _http = httpClient ?? http.Client();

  final String baseUrl;
  final String? token;
  final http.Client _http;

  Uri _uri(String path) => Uri.parse('${baseUrl.replaceAll(RegExp(r'/+$'), '')}$path');

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  Future<http.Response> _send(Future<http.Response> Function() call) async {
    try {
      return await call().timeout(const Duration(seconds: 15));
    } on ApiException {
      rethrow;
    } on SocketException catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    } on http.ClientException catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    } on Exception catch (e) {
      throw ControllerUnreachableException(baseUrl, e);
    }
  }

  Map<String, dynamic> _decode(http.Response response) {
    final body = response.body.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode >= 400) {
      throw ApiException.fromEnvelope(response.statusCode, body);
    }
    return body;
  }

  /// Liveness probe — used by connection setup to validate the URL.
  Future<void> healthz() async {
    final response = await _send(() => _http.get(_uri('/healthz')));
    _decode(response);
  }

  /// Returns the bearer access token.
  Future<String> login(String email, String password) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/auth/login'),
          headers: _headers,
          body: jsonEncode({'email': email, 'password': password}),
        ));
    return _decode(response)['access_token'] as String;
  }

  /// Revokes the current session server-side.
  Future<void> logout() async {
    final response =
        await _send(() => _http.post(_uri('/api/v1/auth/logout'), headers: _headers));
    if (response.statusCode >= 400 && response.statusCode != 401) {
      _decode(response);
    }
  }

  Future<Principal> me() async {
    final response = await _send(() => _http.get(_uri('/api/v1/me'), headers: _headers));
    return Principal.fromJson(_decode(response));
  }

  Future<List<NodeInfo>> listNodes({String? status}) async {
    final query = status != null ? '?status=$status' : '';
    final response =
        await _send(() => _http.get(_uri('/api/v1/nodes$query'), headers: _headers));
    final list = _decodeList(response);
    return list.map((e) => NodeInfo.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<NodeInfo> getNode(String id) async {
    final response =
        await _send(() => _http.get(_uri('/api/v1/nodes/$id'), headers: _headers));
    return NodeInfo.fromJson(_decode(response));
  }

  Future<NodeInfo> patchNode(String id, {String? role, String? name}) async {
    final response = await _send(() => _http.patch(
          _uri('/api/v1/nodes/$id'),
          headers: _headers,
          body: jsonEncode({'role': ?role, 'name': ?name}),
        ));
    return NodeInfo.fromJson(_decode(response));
  }

  /// Admin: mint a node-role API key. The full key is returned exactly once.
  Future<MintedApiKey> createNodeApiKey(String name) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/admin/api-keys'),
          headers: _headers,
          body: jsonEncode({'name': name, 'role': 'node'}),
        ));
    return MintedApiKey.fromJson(_decode(response));
  }

  /// Submit a task; v1 runs synchronously, so the response is final.
  Future<TaskInfo> submitTask({
    required String prompt,
    String? type,
    String? model,
    String? knowledgeQuery,
  }) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/tasks'),
          headers: _headers,
          body: jsonEncode({
            'prompt': prompt,
            'type': ?type,
            'model': ?model,
            'knowledge_query': ?knowledgeQuery,
          }),
        ));
    return TaskInfo.fromJson(_decode(response));
  }

  Future<List<TaskInfo>> listTasks({String? status}) async {
    final query = status != null ? '?status=$status' : '';
    final response =
        await _send(() => _http.get(_uri('/api/v1/tasks$query'), headers: _headers));
    return _decodeList(response)
        .map((e) => TaskInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<WorkflowInfo>> listWorkflows() async {
    final response =
        await _send(() => _http.get(_uri('/api/v1/workflows'), headers: _headers));
    return _decodeList(response)
        .map((e) => WorkflowInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<WorkflowInfo> createWorkflow({
    required String name,
    String? description,
    required Map<String, dynamic> definition,
  }) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/workflows'),
          headers: _headers,
          body: jsonEncode({
            'name': name,
            'description': ?description,
            'definition': definition,
          }),
        ));
    return WorkflowInfo.fromJson(_decode(response));
  }

  /// Runs synchronously until finished or paused at an approval step.
  Future<WorkflowRunInfo> runWorkflow(String workflowId, String input) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/workflows/$workflowId/run'),
          headers: _headers,
          body: jsonEncode({'input': input}),
        ));
    return WorkflowRunInfo.fromJson(_decode(response));
  }

  Future<WorkflowRunInfo> getRun(String workflowId, String runId) async {
    final response = await _send(() => _http
        .get(_uri('/api/v1/workflows/$workflowId/runs/$runId'), headers: _headers));
    return WorkflowRunInfo.fromJson(_decode(response));
  }

  Future<WorkflowRunInfo> approveRun(
      String workflowId, String runId, bool approved) async {
    final response = await _send(() => _http.post(
          _uri('/api/v1/workflows/$workflowId/runs/$runId/approve'),
          headers: _headers,
          body: jsonEncode({'approved': approved}),
        ));
    return WorkflowRunInfo.fromJson(_decode(response));
  }

  List<dynamic> _decodeList(http.Response response) {
    if (response.statusCode >= 400) {
      _decode(response); // throws with envelope
    }
    return jsonDecode(response.body) as List<dynamic>;
  }

  void close() => _http.close();
}
