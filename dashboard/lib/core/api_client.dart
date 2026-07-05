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

  List<dynamic> _decodeList(http.Response response) {
    if (response.statusCode >= 400) {
      _decode(response); // throws with envelope
    }
    return jsonDecode(response.body) as List<dynamic>;
  }

  void close() => _http.close();
}
