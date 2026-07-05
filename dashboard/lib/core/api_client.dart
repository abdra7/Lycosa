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

  void close() => _http.close();
}
